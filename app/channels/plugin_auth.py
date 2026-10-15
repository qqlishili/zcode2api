"""P8 修复：派生凭证与宿主的一致性（可见 + 可一键同步）。

问题（实测确认）：
    宿主在 init 时把 auth.json 读进内存，之后不再重读。新增账号后若不重启宿主，
    请求指定该账号的槽位时，host.js 的 accountKey() 会**静默回退到第一个账号**：

        function accountKey(provider, account) {
          if (account && providerOf(account) === provider) return account
          return accountsOf(readAuth(), provider)[0] ?? provider   // ← 回退
        }

    这不是"降级"，是**用错账号**：请求记在别的账号头上，新账号从不被使用。
    所以必须让"派生凭证已变、宿主尚未加载"这件事**可见**，并能**一键同步**。

设计：
    · 重建派生文件（快）与"宿主已加载"分开记录 —— 文件永远最新，下次重启即生效
    · 用**内容哈希**判断是否 stale（加一个再删掉 → 内容相同 → 不该报 stale）
    · **不自动重启**（避免掐断在飞流），由人工一键同步
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import tempfile
import time

from .. import logs, settings
from ..store import store
from .plugin_channel import slot_of

_ACCESS = "zcode"
_SITE = "zai"
_BASE = "https://zcode.z.ai/api/v1/zcode-plan/anthropic"

# DB meta 键
_KEY_FP = "plugin_auth_fingerprint"       # 当前文件的内容哈希
_KEY_LOADED = "plugin_auth_loaded"        # 宿主已加载的指纹与时间


def _device_of(account) -> str | None:  # noqa: ANN001 - Account，避免循环导入
    """取账号的设备标识。缺失时返回 None，由调用方跳过该账号。

    必须用 `fingerprint_view()` 而不是直接读 `account.fingerprint`：
    后者在内存态可能是 DeviceProfile **对象**（服务启动时由 profile_from 还原），
    只判 dict 会让所有账号都"缺 device"—— 那就等于把派生凭证写成空文件
    （2026-10-15 实测踩到：sync 之后 13 个槽位全丢）。fingerprint_view 正是
    为「对外取 dict」而存在的。

    也不能在缺失时编一个随机值：device 参与上游对「同一账号是否同一设备」的判断，
    随机 device 等于每次都宣称「这是新设备」；而且它会让同一账号两次生成的内容
    不同、指纹失去确定性、is_stale() 永远为真。
    """
    view = account.fingerprint_view()
    if isinstance(view, dict) and view.get("device_mid"):
        return str(view["device_mid"])
    return None


def build_auth_doc() -> dict:
    """构造 auth.json 内容：{槽位名: {type, access, refresh}}。

    只导出可用于 Plan 通道的账号（jwt 模式且启用），与 native 侧的候选口径一致。
    """
    doc: dict[str, dict] = {}
    skipped: list[str] = []
    for account in store.list_accounts("zai"):
        if not account.enabled or account.mode != "jwt" or not account.jwt_token:
            continue
        device = _device_of(account)
        if not device:
            # 缺设备指纹：跳过并如实记录。少一个账号是可见的（数量会变），
            # 比静默编造一个随机身份安全。
            skipped.append(account.name)
            continue
        refresh = json.dumps({
            "jwt": account.jwt_token,
            "device": device,
            "site": _SITE,
            "base": _BASE,
        }, ensure_ascii=False)
        doc[slot_of(account.id)] = {"type": "oauth", "access": _ACCESS, "refresh": refresh}
    if skipped:
        logs.warn("plugin", f"缺少设备指纹，已跳过 {len(skipped)} 个账号: {skipped[:3]}")
    return doc


def _fingerprint(doc: dict) -> str:
    """内容哈希（排序后序列化，与键顺序无关）。"""
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:32]


def write_plugin_auth() -> pathlib.Path:
    """全量重写派生凭证文件（权限 600，原子替换），并记录内容指纹。"""
    path = pathlib.Path(settings.PLUGIN_AUTH_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = build_auth_doc()

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".plugin-auth-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    try:
        store.set_setting(_KEY_FP, _fingerprint(doc))
    except Exception as err:  # noqa: BLE001
        logs.warn("plugin", f"记录派生凭证指纹失败: {err}")

    logs.ok("plugin", f"派生凭证已写入 {path}（{len(doc)} 个槽位）")
    return path


def mark_loaded() -> None:
    """记录「宿主已加载当前指纹」（start/restart 成功后调用）。"""
    try:
        raw = store.get_setting(_KEY_FP) or ""
        store.set_setting(_KEY_LOADED, json.dumps({"fp": raw, "at": time.time()}))
    except Exception as err:  # noqa: BLE001
        logs.warn("plugin", f"记录宿主加载指纹失败: {err}")


def refresh_plugin_auth() -> None:
    """账号集合变化后重建凭证文件（**不重启宿主**）。

    失败只记 warn：凭证文件不是请求路径上的硬依赖，重建失败不应影响 native 通道。
    """
    try:
        write_plugin_auth()
    except Exception as err:  # noqa: BLE001
        logs.warn("plugin", f"派生凭证重建失败: {err}")


def file_fingerprint() -> str:
    """按**磁盘上的实际内容**重算指纹（不读 DB 缓存）。

    见 is_stale() 的说明：DB 里的 plugin_auth_fingerprint 是派生记录，
    文件可能被其他进程改写，所以判定必须以磁盘为准。
    """
    path = pathlib.Path(settings.PLUGIN_AUTH_PATH)
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    if not isinstance(doc, dict):
        return ""
    return _fingerprint(doc)


def is_stale() -> bool:
    """宿主加载的凭证是否已落后于**磁盘上的当前文件**。

    比较对象是「文件实际内容重算的指纹」与「宿主启动时记录的指纹」。
    不用 DB 里缓存的 current：那个值由 write_plugin_auth() 写入，而文件同样
    可能被诊断脚本/独立进程改写，缓存与磁盘脱节时会给出与事实相反的结论
    （2026-10-15 实测踩到：文件与宿主一致，却报 stale=true）。

    尚未记录过加载指纹时（例如从未启动宿主）不报 stale。
    """
    try:
        raw = store.get_setting(_KEY_LOADED)
    except Exception:  # noqa: BLE001
        return False
    if not raw:
        return False
    try:
        loaded = json.loads(raw).get("fp") or ""
    except (TypeError, ValueError):
        return False
    if not loaded:
        return False
    current = file_fingerprint()
    if not current:
        return False          # 文件不可读：不谎报 stale（宿主可能仍在正常工作）
    return loaded != current


def status() -> dict:
    """派生凭证与宿主的一致性状态（管理页/诊断用）。"""
    try:
        recorded = store.get_setting(_KEY_FP) or ""
        raw = store.get_setting(_KEY_LOADED)
        loaded_at = 0.0
        loaded_fp = ""
        if raw:
            try:
                parsed = json.loads(raw)
                loaded_fp = parsed.get("fp") or ""
                loaded_at = float(parsed.get("at") or 0.0)
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa: BLE001
        recorded, loaded_fp, loaded_at = "", "", 0.0

    from .plugin_channel import plugin_host

    # current 以**磁盘实际内容**为准；recorded 一并暴露，方便诊断是谁写过的
    return {
        "stale": is_stale(),
        "current_fingerprint": file_fingerprint(),
        "recorded_fingerprint": recorded,
        "loaded_fingerprint": loaded_fp,
        "loaded_at": loaded_at,
        "host_alive": plugin_host.is_alive(),
    }


async def sync_plugin_auth() -> dict:
    """一键同步：重建凭证 → 优雅重启宿主（drain 在飞请求）。

    这是**唯一**让新账号在 plugin 通道生效的路径；由人工触发。
    """
    from .base import start_plugin_host, stop_plugin_host

    if getattr(settings, "REQUEST_CHANNEL", "native") != "plugin":
        return {"ok": False, "error": "当前不是 plugin 通道，无需同步"}

    try:
        write_plugin_auth()
    except Exception as err:  # noqa: BLE001
        return {"ok": False, "error": f"重建派生凭证失败: {err}"}

    await stop_plugin_host(drain=True)
    started = await start_plugin_host()
    if started is None:
        return {"ok": False, "error": "宿主重启失败，请查看日志"}
    return {"ok": True, "stale": is_stale()}

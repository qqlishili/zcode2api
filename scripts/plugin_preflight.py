"""Plugin 通道依赖预检：确认 Bun、宿主脚本与插件目录是否就绪。

设计目标：
1. 第一性原理：plugin 通道能否启用，只取决于三个外部事实——运行时（Bun）存在且可执行、
   宿主脚本（host.js）存在、插件目录（packages/zcode）含可加载入口。缺任一即不可用。
2. 零副作用：只做只读检查，不启动进程、不写文件，因此可在服务启动路径上安全调用。
3. 可降级：任一项不满足时返回 ready=False 并给出 detail，调用方据此降级到 native 通道，
   而不是让服务启动失败（见 spec: dual-channel-upstream REQ-DUAL-09）。

用法：
    python scripts/plugin_preflight.py            # 人类可读
    python scripts/plugin_preflight.py --json     # 机器可读（服务启动时调用）
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import sys

ROOT_DIR = pathlib.Path(__file__).resolve().parents[1]

# 与 app/settings.py 的默认值保持一致（此处不 import app，避免脚本依赖 venv 之外的包）
# 默认交给 PATH 解析（check_bun 本就有 shutil.which 回退），避免写死家目录路径。
DEFAULT_BUN_PATH = "bun"
DEFAULT_HOST_JS = "vendor/magpie_host.js"
DEFAULT_PLUGIN_DIR = "vendor/magpie-plugins/packages/zcode"


def _resolve(path_str: str) -> pathlib.Path:
    """相对路径按仓库根解析，绝对路径原样返回。"""
    p = pathlib.Path(path_str)
    return p if p.is_absolute() else (ROOT_DIR / p)


def check_bun(path_str: str) -> tuple[bool, str]:
    """Bun 可执行文件存在且真的能跑。返回 (ok, detail)。"""
    p = _resolve(path_str)
    if not p.exists():
        found = shutil.which("bun")
        return False, f"未找到 {p}" + (f"（PATH 中有 bun: {found}）" if found else "")
    if not os.access(p, os.X_OK):
        return False, f"{p} 存在但不可执行"
    return True, str(p)


def check_host_js(path_str: str) -> tuple[bool, str]:
    """宿主脚本存在且非空。"""
    p = _resolve(path_str)
    if not p.is_file():
        return False, f"未找到 {p}"
    size = p.stat().st_size
    if size == 0:
        return False, f"{p} 是空文件"
    return True, f"{p}（{size} bytes）"


def check_plugin_dir(path_str: str) -> tuple[bool, str]:
    """插件目录含可加载入口：package.json 声明了 main/exports，或存在 index.*。

    对齐 host.js 的 entries()：host 按 pkg.exports["./server"] → pkg.main →
    pkg.exports["."] → INDEX_FILES 的顺序找入口，此处只需确认"至少有一个入口存在"。
    """
    p = _resolve(path_str)
    if not p.is_dir():
        return False, f"未找到目录 {p}"

    index_files = ("index.ts", "index.tsx", "index.js", "index.mjs", "index.cjs")
    for name in index_files:
        if (p / name).is_file():
            return True, f"{p}（入口 {name}）"

    pkg_file = p / "package.json"
    if pkg_file.is_file():
        try:
            pkg = json.loads(pkg_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as err:
            return False, f"{p}/package.json 无法解析：{err}"
        if isinstance(pkg.get("main"), str) or pkg.get("exports"):
            return True, f"{p}（入口由 package.json 声明）"

    return False, f"{p} 内未找到可加载入口（无 index.* 且 package.json 未声明 main/exports）"


def preflight(
    bun_path: str = DEFAULT_BUN_PATH,
    host_js: str = DEFAULT_HOST_JS,
    plugin_dir: str = DEFAULT_PLUGIN_DIR,
) -> dict:
    """执行三项检查并汇总。任一项失败 → ready=False。"""
    bun_ok, bun_detail = check_bun(bun_path)
    host_ok, host_detail = check_host_js(host_js)
    dir_ok, dir_detail = check_plugin_dir(plugin_dir)

    ready = bun_ok and host_ok and dir_ok
    detail = {
        "bun": bun_detail,
        "host_js": host_detail,
        "plugin_dir": dir_detail,
    }
    if not ready:
        missing = [k for k, ok in (("bun", bun_ok), ("host_js", host_ok), ("plugin_dir", dir_ok)) if not ok]
        detail["missing"] = missing
        detail["hint"] = "plugin 通道不可用；请检查上述路径，服务将降级到 native 通道"

    return {
        "bun": bun_ok,
        "host_js": host_ok,
        "plugin_dir": dir_ok,
        "ready": ready,
        "detail": detail,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Plugin 通道依赖预检")
    parser.add_argument("--json", action="store_true", help="输出 JSON（服务启动时用）")
    parser.add_argument("--bun-path", default=os.getenv("ZCODE_BUN_PATH", DEFAULT_BUN_PATH))
    parser.add_argument("--host-js", default=os.getenv("ZCODE_PLUGIN_HOST_JS", DEFAULT_HOST_JS))
    parser.add_argument("--plugin-dir", default=os.getenv("ZCODE_PLUGIN_DIR", DEFAULT_PLUGIN_DIR))
    args = parser.parse_args()

    result = preflight(args.bun_path, args.host_js, args.plugin_dir)

    if args.json:
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ready"] else 1

    for item in ("bun", "host_js", "plugin_dir"):
        mark = "OK  " if result[item] else "MISS"
        print(f"  [{mark}] {item:11s} {result['detail'][item]}")
    print()
    if result["ready"]:
        print("plugin 通道就绪")
        return 0
    print("plugin 通道不可用：" + "、".join(result["detail"]["missing"]))
    print(result["detail"]["hint"])
    return 1


if __name__ == "__main__":
    sys.exit(main())

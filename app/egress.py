"""托管出口的完整身份、路由版本及私有进程记录。"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .constants import REGION_PORT_TIERS

PROTOCOL_VERSION = 1
PROVIDER_NAME = "zcode-egress"


def node_fingerprint(proxy: dict) -> str:
    """只剔除显示名；未知连接字段及值类型也属于身份。"""
    definition = {k: v for k, v in proxy.items() if k != "name"}
    return hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def registry_generation(registry: dict) -> str:
    route = {"regions": {r: sorted(v) for r, v in registry["regions"].items()}, "slots": registry["slots"]}
    return hashlib.sha256(json.dumps(route, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def managed_port(proxy: str | None) -> int | None:
    if not proxy:
        return None
    try:
        url = urlsplit(proxy)
        if url.hostname not in {"127.0.0.1", "localhost", "::1"} or url.scheme != "http":
            return None
        port = url.port
        return port if any(low <= (port or 0) <= high for low, high in REGION_PORT_TIERS.values()) else None
    except ValueError:
        return None


def slot_identity(proxy: str | None, slots: dict) -> tuple | None:
    slot = slots.get(str(managed_port(proxy)))
    return (managed_port(proxy), slot["node_id"], slot["epoch"]) if slot and slot["node_id"] else None


def validate_registry(registry: dict) -> None:
    if not isinstance(registry, dict) or not isinstance(registry.get("regions"), dict):
        raise ValueError("registry_structure_invalid")
    if "slots" not in registry:
        return  # 首次维护前兼容旧注册表，日常发布另行校验迁移前提。
    slots = registry["slots"]
    if not isinstance(slots, dict) or registry.get("generation") != registry_generation(registry):
        raise ValueError("registry_generation_invalid")
    routed = set()
    for region, urls in registry["regions"].items():
        if region not in REGION_PORT_TIERS or not isinstance(urls, list):
            raise ValueError("registry_region_invalid")
        low, high = REGION_PORT_TIERS[region]
        for url in urls:
            port = managed_port(url)
            slot = slots.get(str(port), {})
            if (port is None or url != f"http://127.0.0.1:{port}" or not low <= port <= high or url in routed or
                    slot.get("region") != region or slot.get("admission") not in {"active", "fallback"} or
                    not slot.get("node_id") or type(slot.get("epoch")) is not int or slot["epoch"] < 1):
                raise ValueError("registry_route_invalid")
            routed.add(url)
    expected = {str(p): r for r, (low, high) in REGION_PORT_TIERS.items() for p in range(low, high + 1)}
    if set(slots) != set(expected):
        raise ValueError("registry_slots_incomplete")
    for port, slot in slots.items():
        if (slot.get("region") != expected[port] or
                slot.get("admission") not in {"active", "fallback", "draining", "empty"} or
                type(slot.get("epoch")) is not int or slot["epoch"] < 0 or
                (slot.get("admission") == "empty") != (slot.get("node_id") is None)):
            raise ValueError("registry_slot_invalid")
        if (slot["admission"] in {"active", "fallback"}) != (f"http://127.0.0.1:{port}" in routed):
            raise ValueError("registry_admission_inconsistent")


def process_identity(pid: int) -> str:
    """Linux 启动标识与系统 boot 绑定，PID 复用不继承确认。"""
    stat = (Path("/proc") / str(pid) / "stat").read_text().rsplit(")", 1)[1].split()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return f"{boot}:{stat[19]}"


def write_private_json(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            os.chmod(temporary, 0o600)
            json.dump(value, file, ensure_ascii=False, sort_keys=True)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
        if os.name == "posix":
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

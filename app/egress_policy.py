"""启动生效的用途出口策略；保存目标不参与运行中选路。"""

from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from .constants import REGION_PORT_TIERS


@dataclass(frozen=True)
class ClaimBinding:
    region: str
    port: int
    node_id: str
    epoch: int

    @property
    def identity(self) -> tuple[int, str, int]:
        return self.port, self.node_id, self.epoch

    @property
    def proxy(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def selection(self) -> str:
        import hashlib

        return hashlib.sha256(f"{self.region}:{self.port}:{self.node_id}:{self.epoch}".encode()).hexdigest()


@dataclass(frozen=True)
class EgressPolicy:
    chat_mode: str = "inherit"
    chat_proxy_url: str = ""
    claim_mode: str = "inherit"
    claim_bindings: tuple[ClaimBinding, ...] = ()

    def binding(self, region: str | None) -> ClaimBinding | None:
        return next((binding for binding in self.claim_bindings if binding.region == region), None)

    def mode(self, purpose: str | None, global_mode: str) -> str:
        if purpose not in (None, "chat", "claim"):
            raise ValueError("未知出口用途")
        mode = getattr(self, f"{purpose}_mode") if purpose else "inherit"
        return global_mode if mode == "inherit" else mode

    def as_dict(self) -> dict:
        return {
            "chat_mode": self.chat_mode, "chat_proxy_url": self.chat_proxy_url,
            "claim_mode": self.claim_mode,
            "claim_bindings": {binding.region: {
                "port": binding.port, "node_id": binding.node_id, "epoch": binding.epoch,
            } for binding in self.claim_bindings},
        }


def parse_policy(value: object) -> EgressPolicy:
    """环境与数据库共用完整对象校验，不逐字段合并不同来源。"""
    keys = {"chat_mode", "chat_proxy_url", "claim_mode", "claim_bindings"}
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError("出口策略必须包含且仅包含四个配置字段")
    for name in ("chat_mode", "claim_mode"):
        if value[name] not in ("inherit", "direct", "proxy"):
            raise ValueError(f"{name} 只支持 inherit / direct / proxy")
    raw_url = value["chat_proxy_url"]
    if not isinstance(raw_url, str):
        raise ValueError("chat_proxy_url 必须为字符串")
    proxy_url = raw_url.strip()
    if proxy_url:
        try:
            url = urlsplit(proxy_url)
            host = url.hostname
            loopback = host == "localhost" or ipaddress.ip_address(host or "").is_loopback
            if (url.scheme != "http" or not loopback or not url.port or url.username is not None or
                    url.password is not None or url.path not in ("", "/") or url.query or url.fragment):
                raise ValueError
            proxy_url = f"http://{'[' + host + ']' if ':' in host else host}:{url.port}"
        except (ValueError, TypeError) as exc:
            raise ValueError("chat_proxy_url 必须是带端口的本地 HTTP 代理，不能包含凭证或路径") from exc
    if value["chat_mode"] == "proxy" and not proxy_url:
        raise ValueError("chat proxy 缺少本地代理 URL")
    bindings = value["claim_bindings"]
    if not isinstance(bindings, dict):
        raise ValueError("claim_bindings 必须为地区对象")
    parsed = []
    for region, binding in sorted(bindings.items()):
        if region not in REGION_PORT_TIERS or not isinstance(binding, dict) or set(binding) != {
            "port", "node_id", "epoch",
        }:
            raise ValueError("领取绑定包含未知地区或字段")
        port, node_id, epoch = binding["port"], binding["node_id"], binding["epoch"]
        start, end = REGION_PORT_TIERS[region]
        if type(port) is not int or not start <= port <= end:
            raise ValueError(f"{region} 领取端口不在本区端口段")
        if not isinstance(node_id, str) or not re.fullmatch(r"[0-9a-f]{64}", node_id):
            raise ValueError("领取节点指纹必须为 64 位小写十六进制")
        if type(epoch) is not int or epoch < 1:
            raise ValueError("领取节点 epoch 必须为正整数")
        parsed.append(ClaimBinding(region, port, node_id, epoch))
    return EgressPolicy(value["chat_mode"], proxy_url, value["claim_mode"], tuple(parsed))


def environment_policy(environ) -> EgressPolicy:
    try:
        bindings = json.loads(environ.get("ZCODE_CLAIM_EGRESS_BINDINGS", "{}"))
    except (ValueError, TypeError) as exc:
        raise ValueError("ZCODE_CLAIM_EGRESS_BINDINGS 必须为合法 JSON") from exc
    return parse_policy({
        "chat_mode": environ.get("ZCODE_CHAT_EGRESS_MODE", "inherit").strip().lower(),
        "chat_proxy_url": environ.get("ZCODE_CHAT_PROXY_URL", ""),
        "claim_mode": environ.get("ZCODE_CLAIM_EGRESS_MODE", "inherit").strip().lower(),
        "claim_bindings": bindings,
    })

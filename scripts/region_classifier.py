"""多级大区归一化正则与真实出口 IP (Ground-Truth Geo-IP) 校正模块。

设计目标：
1. 第一性原理：精确识别真实落地节点，过滤中转/入口干扰（如“沪日专线”落地为 JP，“日本-中转->美国”落地为 US）。
2. 真机校正：当提供真实出口 Trace/loc (如 cloudflare trace 的 loc=US) 时，强行纠正命名欺骗。
3. 归一化大区代码：HK, TW, JP, SG, US, KR, EU, OTHER。
"""

from __future__ import annotations

import re

# 优先级 1：显式出口/落地正则（处理 ->落地 或 -落地 模式）
EXIT_PATTERN = re.compile(
    r'(?:[-_>|/]\s*|\b(?:to|exit|落地|专线|接入|出口)\s*[-_>|/]?\s*)'
    r'(?P<loc>香港|HK|Hong\s*Kong|🇭🇰|台湾|TW|Taiwan|台北|🇹🇼|日本|JP|Japan|东京|大阪|🇯🇵|新加坡|SG|Singapore|狮城|🇸🇬|美国|US|USA|洛杉矶|硅谷|纽约|圣何塞|🇺🇸|韩国|KR|Korea|首尔|🇰🇷|德国|法国|英国|荷兰|欧洲|DE|FR|GB|UK|NL)',
    re.IGNORECASE,
)

# 优先级 2：常规地名关键词与机场专线简称（沪日/广港/深港等）匹配
REGION_KEYWORDS: list[tuple[str, re.Pattern]] = [
    ("HK", re.compile(r'(?:香港|Hong\s*Kong|HK|\bHKG\b|🇭🇰|广港|深港|沪港|京港|中港|港)', re.IGNORECASE)),
    ("TW", re.compile(r'(?:台湾|Taiwan|TW|\bTPE\b|台北|🇹🇼|沪台|京台|广台|中台)', re.IGNORECASE)),
    ("JP", re.compile(r'(?:日本|Japan|JP|\bTYO\b|\bOSA\b|东京|大阪|🇯🇵|沪日|京日|广日|中日)', re.IGNORECASE)),
    ("SG", re.compile(r'(?:新加坡|Singapore|SG|\bSIN\b|狮城|🇸🇬|广新|沪新|京新|中新)', re.IGNORECASE)),
    ("US", re.compile(r'(?:美国|USA|US|\bLAX\b|\bSJC\b|\bJFK\b|洛杉矶|硅谷|西雅图|纽约|圣何塞|波特兰|🇺🇸|沪美|京美|广美|中美)', re.IGNORECASE)),
    ("KR", re.compile(r'(?:韩国|Korea|KR|\bICN\b|\bSEL\b|首尔|🇰🇷|沪韩|京韩|广韩|中韩)', re.IGNORECASE)),
    ("EU", re.compile(r'(?:德国|法国|英国|荷兰|欧洲|DE|FR|GB|UK|NL|\bFRA\b|\bAMS\b|\bLON\b|沪德|京德|沪英|京英|沪欧|京欧)', re.IGNORECASE)),
]

# 真实 IP 国家代码映射至标准大区代码
COUNTRY_TO_REGION: dict[str, str] = {
    "HK": "HK",
    "TW": "TW",
    "JP": "JP",
    "SG": "SG",
    "US": "US",
    "KR": "KR",
    "DE": "EU",
    "FR": "EU",
    "GB": "EU",
    "NL": "EU",
    "IT": "EU",
    "ES": "EU",
    "CH": "EU",
    "SE": "EU",
}


def classify_region_by_name(name: str) -> str:
    """根据节点名称进行多级正则识别。"""
    raw = (name or "").strip()
    if not raw:
        return "OTHER"

    # 1. 尝试匹配显式出口/落地标识（过滤中转地名）
    # 例如：日本中转->美国洛杉矶，优先取尾部美国
    exit_match = EXIT_PATTERN.search(raw)
    if exit_match:
        loc_str = exit_match.group("loc").upper()
        for reg, pat in REGION_KEYWORDS:
            if pat.search(loc_str):
                return reg

    # 2. 从后向前搜索关键词（通常机场命名为 [入口]-[出口]）
    # 拆分分隔符
    tokens = re.split(r'[-_>|/]', raw)
    for token in reversed(tokens):
        token = token.strip()
        for reg, pat in REGION_KEYWORDS:
            if pat.search(token):
                return reg

    # 3. 全局直接搜索
    for reg, pat in REGION_KEYWORDS:
        if pat.search(raw):
            return reg

    return "OTHER"


def parse_cf_trace(trace_text: str) -> str | None:
    """从 Cloudflare trace (http://cloudflare.com/cdn-cgi/trace) 文本解析 loc 国家代码。"""
    if not trace_text:
        return None
    for line in trace_text.splitlines():
        line = line.strip()
        if line.startswith("loc="):
            loc = line.split("=", 1)[1].strip().upper()
            return loc
    return None


def resolve_region(name: str, real_country_code: str | None = None) -> str:
    """综合节点命名与真机出口国家代码确定最终大区。真机结果具备绝对覆盖权。"""
    if real_country_code:
        cc = real_country_code.strip().upper()
        if cc in COUNTRY_TO_REGION:
            return COUNTRY_TO_REGION[cc]
        return "OTHER"

    return classify_region_by_name(name)

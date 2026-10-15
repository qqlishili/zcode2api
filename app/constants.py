"""上游常量收口 —— 所有与 zcode/zai/bigmodel 上游相关的 URL、模型名、判定关键字集中在此。

约定（docs/development/06-dev-guide.md）：
- 模块代码禁止再硬编码上游 URL / 模型名 / 关键字，一律 import 本模块。
- 改动任何常量必须先在 tests/unit/test_constants.py 补断言，防止无意漂移。
- 值均来自源码实证，出处标注见 docs/development/05-upstream-protocols.md。
"""

from __future__ import annotations

# ── 上游 origin ──────────────────────────────────────────────────────────────
# Plan 通道（JWT + 验证码）：zcode.z.ai 的 coding-plan 代理端点
ZCODE_ORIGIN = "https://zcode.z.ai"
# API Key 通道（回退 / bigmodel 同形）：api.z.ai 的 anthropic 兼容端点
ZAI_API_ORIGIN = "https://api.z.ai"
# bigmodel（智谱开放平台）anthropic 兼容端点
BIGMODEL_ORIGIN = "https://open.bigmodel.cn"

# ── messages 端点（settings.UPSTREAM 的默认值来源）────────────────────────────
MESSAGES_PATHS = {
    # zai + jwt（Plan 通道，需 X-Aliyun-Captcha-Verify-Param）
    "zai": "/api/v1/zcode-plan/anthropic/v1/messages",
    # zai + apiKey 回退通道（免验证码）
    "zai_fallback": "/api/anthropic/v1/messages",
    "bigmodel": "/api/anthropic/v1/messages",
}
MESSAGES_URLS = {
    "zai": ZCODE_ORIGIN + MESSAGES_PATHS["zai"],
    "zai_fallback": ZAI_API_ORIGIN + MESSAGES_PATHS["zai_fallback"],
    "bigmodel": BIGMODEL_ORIGIN + MESSAGES_PATHS["bigmodel"],
}

# ── 计费 / 额度端点（quota.py / claim.py）────────────────────────────────────
BILLING_BASE = f"{ZCODE_ORIGIN}/api/v1/zcode-plan"
BILLING_CURRENT_PATH = "/billing/current"
BILLING_BALANCE_PATH = "/billing/balance"
USAGE_PATH = "/usage"
# WAF 风险点：billing/* 连续查询易触发拦截，轮询必须错峰（docs 05 §风险控制）

# ── OAuth ────────────────────────────────────────────────────────────────────
OAUTH_CLI_INIT_PATH = "/api/v1/oauth/cli/init"
OAUTH_CLI_POLL_PATH = "/api/v1/oauth/cli/poll"   # + /{flow_id}

# ── 客户端版本（单一真相源：跟进上游 bda8ea1 的官方客户端 3.14.4）────────────
# 客户端 claim 头实测缺版本/平台头 → 上游 3007；client/configs 带 platform 参数 → 3001
CLIENT_APP_VERSION = "3.14.4"
CLIENT_PLATFORM = "darwin-arm64"  # asar TH() = process.platform-arch，服务端固定伪装
CLIENT_CONFIGS_URL = f"{ZCODE_ORIGIN}/api/v1/client/configs"
CLIENT_CONFIGS_QUERY = f"app_version={CLIENT_APP_VERSION}"

# ── billing 族版本 / 激活上报（复用同一官方客户端版本）────────────────────────
BILLING_APP_VERSION = CLIENT_APP_VERSION
BILLING_TITLE = "Z Code@electron"        # zcode-switch billing 头实证形态
BILLING_RELEASE_CHANNEL = "stable"
# 官方客户端每日活跃事件：POST /api/v1/event/report（不在 zcode-plan 下、无
# Authorization）。zcode-switch claim_refresh 在 preview 前上报 app_launch +
# app_daily_active —— 疑似活动套餐投放的资格信号（官方客户端可见活动而纯
# billing 轮询号 preview 为空的差异点）。
EVENT_REPORT_URL = f"{ZCODE_ORIGIN}/api/v1/event/report"
ACTIVATION_ELEMENTS = ("app_launch", "app_daily_active")
ACTIVATION_SCREEN_RESOLUTION = "2560x1440"
# 登录成功事件（官方 useRootOAuthEffects.ts）：event_region 为 app_profile（非常规
# app），detail 携带登录提供方标签（telemetry.resolve_login_provider 按账号 provider
# 推导）。仅登录态（user_id 非空）上报，且按 installed_at 一次性去重，避免高频刷新
# 被上游读作「账号反复登录」。
LOGIN_EVENT = {
    "element": "app_login_success",
    "event_region": "app_profile",
    "event_type": "view",
}

# ── 验证码默认配置（client/configs 拉取失败时的兜底；region 实测线上为 cn）────
CAPTCHA_DEFAULTS = {"enabled": True, "prefix": "no8xfe", "region": "cn", "sceneId": "11xygtvd"}

# ── 模型名 ───────────────────────────────────────────────────────────────────
# Z.AI 上游模型名大小写敏感；客户端传小写别名时映射到官方名（gateway.MODEL_NAME_MAP）。
# 表内未收录的模型名原样透传给上游，由上游裁决（账号套餐不含的模型会拿到 3006）。
MODEL_NAME_MAP = {
    "codex-auto-review": "GLM-5.3-Flash",
    "glm-5.3-flash": "GLM-5.3-Flash",
    "glm-5.3": "GLM-5.3",
    "glm-5.2": "GLM-5.2",
    "glm-5-turbo": "GLM-5-Turbo",
    "glm-turbo": "GLM-5-Turbo",
    "glm-5.1": "GLM-5.1",
    "glm-4.7": "GLM-4.7",
}
# /v1/models 对外公布（2026-09-05 实测：当前账号套餐不含 GLM-5.2/5-Turbo，
# 上游 3006 model not allowed；按账号实际余额窗口公布）
AVAILABLE_MODELS = ["GLM-5.3-Flash", "GLM-5.3"]

# 上游 max_tokens 合法范围（2026-09-06 实测：超限报 400 code 1210
# 「max_tokens参数非法：限制数值范围[1,131072]」，客户端（如 auto-compact 续传）
# 可能带更大的值，网关统一钳制）
MAX_TOKENS_LIMIT = 131072

# ── 请求头 ───────────────────────────────────────────────────────────────────
ANTHROPIC_VERSION = "2023-06-01"
USER_AGENT = f"ZCode/{CLIENT_APP_VERSION}"
X_ZCODE_APP_VERSION = CLIENT_APP_VERSION
X_ZCODE_AGENT = "glm"
HTTP_REFERER = "https://zcode.z.ai/"
CAPTCHA_HEADER = "X-Aliyun-Captcha-Verify-Param"
# region 头（zapi captcha.ts REGION_HEADER；与 PARAM 成对下发，缺失易 3007）
CAPTCHA_REGION_HEADER = "X-Aliyun-Captcha-Verify-Region"

# ── 身份头仿真（对齐 zapi identity.ts 的 pio；顺序、取值逐字段镜像官方客户端）──
# 官方客户端 companion 头集合，让代理在指纹层与官方 ZCode 桌面端不可区分。
# darwin-arm64 桌面身份。X-Device-Mid 走 quota.device_mid() 持久化复用。
IDENTITY_TITLE = "Z Code@electron"      # X-Title = "Z Code@{sourceTitle}"；桌面端账号走 electron
IDENTITY_RELEASE_CHANNEL = "stable"
IDENTITY_CLIENT_LANGUAGE = "zh-CN"
IDENTITY_CLIENT_TIMEZONE = "Asia/Shanghai"
# X-Os-Category：darwin→macos / win32→windows / 其它→linux
IDENTITY_OS_CATEGORY = "macos"
# X-Os-Version：os.release() 语义；darwin 25.x 对应 macOS 15。固定伪装值。
IDENTITY_OS_VERSION = "25.5.0"

# ── 上游被拒信号 → 账号动作（对齐 ZCode failure-provider-business-codes.ts）───
EXHAUST_HTTP_STATUSES = (402,)
EXHAUST_KEYWORDS = ("quota", "insufficient", "balance", "exhaust", "额度", "余额不足")
EXHAUST_BUSINESS_CODES = (
    "1005", "1304", "1308", "1310", "1313", "2056", "20097",
    "1316", "1317", "1318", "1319", "1320", "1321",
    "insufficient_quota", "credit_balance_exhausted",
)
AUTH_INVALID_BUSINESS_CODES = ("1006",)
CAPTCHA_BUSINESS_CODES = ("3007",)
RATE_LIMIT_BUSINESS_CODES = (
    "3002", "3008", "3009", "3010", "1302", "1303", "1305",
    "rate_limit_reached_error", "rate_limit_error",
)
# 并发上限类错误码（立即切换账号或走 Key 回退，不在同一账号上 sleep 等待）
CONCURRENCY_LIMIT_BUSINESS_CODES = ("3008", "3009", "3010")
SERVER_ERROR_BUSINESS_CODES = (
    "500", "1120", "1230", "1234", "1312", "2007",
    "engine_overloaded_error", "overloaded_error",
)

# 验证码挑战：HTTP 403 + 文案，或 HTTP 200/400/403 + body {"code":3007}（docs 05 §被拒信号表）
# 注意：403 不再无条件判 invalid —— 需先排除验证码挑战（captcha/verify 文案或
# challenge 头），否则一次人机校验续期就把账号错杀成 INVALID（对齐 zapi
# classifyAccountFailure：403 + captcha 文案 → 非账号失败）。
CAPTCHA_BODY_MARKERS = ('"code":3007', '"code": 3007', '"code":"3007"', '"code": "3007"')

# ── 风控信号（2026-09-05 3012 事件实证）────────────────────────────────────────
# 3012「unusual activity」= 上游风控（HTTP 405 或包在 200/400 JSON 承载），不在官方公开错误码表；
# 高频请求触发，官方政策定性为临时限制（限流/冻结，3 次以上违规才封号）。
# 处置：禁用 Plan 通道（或切 API Key 回退），人工确认恢复后手动启用。
RISK_CONTROL_HTTP_STATUSES = (405,)
RISK_CONTROL_MARKERS = (
    '"code":3012', '"code": 3012', '"code":"3012"', '"code": "3012"',
    "unusual activity",
)

# ── 大区规范与百位端口段（单一事实源，对齐 OpenSpec geo-pinned-egress-ports）────────
REGIONS: tuple[str, ...] = ("HK", "TW", "JP", "SG", "US", "KR", "EU", "OTHER")
REGION_PORT_TIERS: dict[str, tuple[int, int]] = {
    "HK": (21100, 21199),
    "TW": (21200, 21299),
    "JP": (21300, 21399),
    "SG": (21400, 21499),
    "US": (21500, 21599),
    "KR": (21600, 21699),
    "EU": (21700, 21799),
    "OTHER": (21800, 21899),
}

# ── 大区发牌梯队与权重（单一事实源；离线发牌脚本与运行期发牌共用）────────────
# P0 市场主流供给核心区（目标占比 >= 80%），P1 优质次级区（目标 ~15-20%）。
# P2（KR/EU/OTHER）市场供给少、延迟高，不纳入任何发牌路径。
REGION_ASSIGN_P0: tuple[str, ...] = ("HK", "JP", "TW")
REGION_ASSIGN_P1: tuple[str, ...] = ("SG", "US")
REGION_ASSIGN_P2: tuple[str, ...] = ("KR", "EU", "OTHER")
# P0 内部加权轮转比例（HK:JP:TW = 5:4:2），与离线发牌脚本原实现一致。
REGION_ASSIGN_P0_WEIGHTS: tuple[tuple[str, int], ...] = (("HK", 5), ("JP", 4), ("TW", 2))
# P1 共享配额比例（占发牌总数），总数不足 5 时不发 P1。
REGION_ASSIGN_P1_RATIO = 0.15


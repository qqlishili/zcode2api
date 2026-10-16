"""运行期配置：环境变量 + 默认值。

所有可调参数集中在此。账号与凭证不在此处，而是持久化到 data/ 目录（见 store.py）。
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from . import constants
from .egress_policy import environment_policy

load_dotenv()

# 项目根目录
ROOT_DIR = Path(__file__).resolve().parents[1]


def _resolve_path(env_name: str, default: str) -> Path:
    raw = (os.getenv(env_name, default) or default).strip()
    path = Path(raw)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return path


def _int(env_name: str, default: int) -> int:
    try:
        return int(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(env_name: str, default: float) -> float:
    try:
        return float(os.getenv(env_name, str(default)))
    except (TypeError, ValueError):
        return default


# ── 目录 ─────────────────────────────────────────────────────────────────────
DATA_DIR = _resolve_path("ZCODE_DATA_DIR", "data")
# 账号与设置持久化到本地 SQLite（与 grok2api 的 local 后端一致）
DB_PATH = DATA_DIR / "accounts.db"
# 前端目录（前后端分离）：默认仓库根 frontend/，可用 ZCODE_FRONTEND_DIR 指向
# 独立部署目录；包内 statics 仅作兜底
FRONTEND_DIR = _resolve_path(
    "ZCODE_FRONTEND_DIR",
    "frontend" if (ROOT_DIR / "frontend").is_dir() else str(Path(__file__).resolve().parent / "statics"),
)

# ── 服务 ─────────────────────────────────────────────────────────────────────
PORT = _int("ZCODE_PORT", 3000)
HOST = os.getenv("ZCODE_HOST", "0.0.0.0")

# ── 鉴权 ─────────────────────────────────────────────────────────────────────
# 后台管理密码默认值，首次启动写入 data/accounts.db，之后以数据库（meta 表）为准。
DEFAULT_ADMIN_KEY = os.getenv("ZCODE_ADMIN_KEY", "zcode")

# ── 验证码 ───────────────────────────────────────────────────────────────────
# 预解 token 池：只按「可服务账号实际绑定的出口端口」预热（见
# openspec/changes/pin-captcha-egress-and-refill-domain/design.md D2）。
# 水位是**每端口**的，不是全局的：1 枚 / 75s = 0.8 枚/min/端口，低于实测端口压力阈值
# （2026-10-13 实测：同端口产率被推高后 sdk_failed 陡增，JP 21301 只有 1/3 成功，
#  同大区无人压的 21303 为 3/3 且单次 6.9s）。
CAPTCHA_REGIONAL_POOL_MIN = _int("CAPTCHA_REGIONAL_POOL_MIN", 1)  # 每端口目标库存
CAPTCHA_REGIONAL_POOL_MAX = _int("CAPTCHA_REGIONAL_POOL_MAX", 5)  # 每端口上限
CAPTCHA_TOKEN_TTL = _int("CAPTCHA_TOKEN_TTL", 75_000) # 单枚 token 最大可用时长（ms；上游实际 ~2min）
CAPTCHA_CONFIG_CACHE_TTL = _int("CAPTCHA_CONFIG_CACHE_TTL", 600_000)  # ms
CAPTCHA_CIRCUIT_BREAKER_COOLDOWN = _int("CAPTCHA_CIRCUIT_BREAKER_COOLDOWN", 600)  # 节点熔断隔离时长（秒）
# 端口级求解退避：同端口连续失败达阈值即暂停该端口求解，
# 实际窗口 = CAPTCHA_PORT_COOLDOWN × 0.75~1.5 抖动（避免多端口同步恢复再挤爆求解槽）
CAPTCHA_PORT_FAIL_THRESHOLD = _int("CAPTCHA_PORT_FAIL_THRESHOLD", 2)
CAPTCHA_PORT_COOLDOWN = _int("CAPTCHA_PORT_COOLDOWN", 60)

# 领额度错峰门禁参数
CLAIM_PROXY_INTERVAL = _float("CLAIM_PROXY_INTERVAL", 20.0)      # 同一出口领额度物理最小间隔（秒）
CLAIM_PROXY_MAX_WAIT = _float("CLAIM_PROXY_MAX_WAIT", 45.0)      # 领额度排队最大等待超时（秒，超限快速失败）

# 验证码求解（无浏览器：Node + happy-dom 模拟浏览器环境，运行阿里云无痕 SDK）
NODE_PATH = os.getenv("ZCODE_NODE_PATH", "node")
CAPTCHA_SOLVER_DIR = ROOT_DIR / "captcha_node"
CAPTCHA_SOLVER_JS = CAPTCHA_SOLVER_DIR / "solver.js"
CAPTCHA_SOLVE_RETRIES = _int("ZCODE_CAPTCHA_RETRIES", 4)
# 每个 producer 总预算及单次 Node 上限（秒）。25s 依据实测：求解失败（sdk_failed）
# 耗时 13~20s，40s 预算只是让唯一求解槽被白占 40s（实测 16 次预算耗尽 = 10% 槽时间）
CAPTCHA_SOLVE_TIMEOUT = _int("ZCODE_CAPTCHA_TIMEOUT", 25)

# ── 落盘证据 ─────────────────────────────────────────────────────────────────
# 请求与验证码求解事件持久化到 data/evidence.db，用于跨重启的可用性判定
# （journald 只留 1 小时、请求监控只在内存 500 条，历史回归会被反复当成新问题）
EVIDENCE_ENABLED = _int("ZCODE_EVIDENCE_ENABLED", 1)
EVIDENCE_RETENTION_DAYS = _int("ZCODE_EVIDENCE_RETENTION_DAYS", 7)

# ── 用量监控 ─────────────────────────────────────────────────────────────────
# 后台自动刷新账号额度的间隔（秒）。0 表示关闭后台轮询，仅按需刷新。
QUOTA_REFRESH_INTERVAL = _int("ZCODE_QUOTA_REFRESH_INTERVAL", 60)
# 成功对话后计费刷新的最小间隔（秒）：billing/* 连续查询易触发上游拦截，
# 每条消息都刷是流量放大器，与 monitor 轮询共享 last_checked_at 去抖。
BILLING_REFRESH_MIN_INTERVAL = _int("ZCODE_BILLING_REFRESH_MIN_INTERVAL", 60)
# ── 上游错误重试 / 冷却（参数可设定）─────────────────────────────────────────
# 429 频控：账号不冷却，原地等待后重试，耗尽后换下一个账号（账号保持可用）
RETRY_429_TIMES = _int("ZCODE_RETRY_429_TIMES", 5)       # 429 重试次数
RETRY_429_WAIT = _int("ZCODE_RETRY_429_WAIT", 60)        # 429 重试等待秒数（上游 Retry-After 优先）
RETRY_429_WAIT_MAX = _int("ZCODE_RETRY_429_WAIT_MAX", 120)  # Retry-After 采信上限（防吊死客户端）
# 5xx 等一般错误：重试，耗尽后账号冷却 COOLING_SECONDS 并换下一个账号
RETRY_5XX_TIMES = _int("ZCODE_RETRY_5XX_TIMES", 3)       # 5xx 重试次数
RETRY_5XX_WAIT = _int("ZCODE_RETRY_5XX_WAIT", 5)         # 5xx 重试等待秒数
# 限流（cooling）冷却时长（秒）——仅 5xx 重试耗尽 / 连接失败使用
COOLING_SECONDS = _int("ZCODE_COOLING_SECONDS", 300)
# 单账号并发上限（0 = 不限）。默认 2；运行期可在后台设置改（meta 表即时生效）
ACCOUNT_CONCURRENCY = _int("ZCODE_ACCOUNT_CONCURRENCY", 2)

# ── 活动监控与 Bark 推送 ─────────────────────────────────────────────────────
# Bark 服务器地址与设备 Key，默认值通过环境变量指定，之后以数据库（meta 表）为准
BARK_SERVER_URL = os.getenv("ZCODE_BARK_SERVER_URL", "https://api.day.app")
BARK_DEVICE_KEY = os.getenv("ZCODE_BARK_DEVICE_KEY", "")
# 哨兵巡检活动间隔（秒），默认 1800（30 分钟），0 表示关闭自动巡检
SENTINEL_INTERVAL = _int("ZCODE_SENTINEL_INTERVAL", 1800)
# 发现新活动时是否自动为全池有效 JWT 账号顺序抢领（1=是，0=否）
SENTINEL_AUTO_CLAIM = _int("ZCODE_SENTINEL_AUTO_CLAIM", 1)

# ── 请求通道 ─────────────────────────────────────────────────────────────────
# 上游请求「形态」的生成方式：
#   native = 本仓库手写拼装（现网逻辑，保底与对照）
#   plugin = 官方插件经插件宿主组装（跟随上游更新，见 docs/adr/0001-*.md）
# 单请求只走一条，不做并行/主备。
REQUEST_CHANNEL = os.getenv("ZCODE_REQUEST_CHANNEL", "native").strip().lower()

# 插件通道运行时（Bun + 宿主脚本 + 插件目录）
# 默认交给 PATH 解析：bun 的安装位置与家目录无关，写死绝对路径会让换机即失效。
# 需要指定时用 ZCODE_BUN_PATH（scripts/plugin_preflight.py 的 --bun-path 同源）。
BUN_PATH = os.getenv("ZCODE_BUN_PATH", "bun")
# 全局兼容模式；用途 override 与数据库生效快照优先，代理失败不能切直连。
EGRESS_MODE = os.getenv("ZCODE_EGRESS_MODE", "proxy").strip().lower()
EGRESS_POLICY = environment_policy(os.environ)
# 出口活性探测间隔（秒）。0 = 关闭。
# 断路器只被动响应真实请求踩雷；探针把「发现失效」提前到请求之前，
# 也是被隔离节点恢复的唯一信号来源（见 app/egress_probe.py）。
EGRESS_PROBE_INTERVAL = _int("ZCODE_EGRESS_PROBE_INTERVAL", 600)
# 探针启动后首次开跑的延迟（秒）：断路器为内存态、重启即清零，越早跑越好；
# 留一小段只为避开与其他启动任务抢验证码求解槽。
EGRESS_PROBE_START_DELAY = _int("ZCODE_EGRESS_PROBE_START_DELAY", 5)
PLUGIN_HOST_JS = _resolve_path("ZCODE_PLUGIN_HOST_JS", "vendor/magpie_host.js")
PLUGIN_DIR = _resolve_path("ZCODE_PLUGIN_DIR", "vendor/magpie-plugins/packages/zcode")
# 派生凭证文件：从 accounts.db 派生（非权威源），权限 600，原子替换。
# 实测改此文件不重启宿主不生效，故账号变化时全量重写 + 重启。
PLUGIN_AUTH_PATH = _resolve_path("ZCODE_PLUGIN_AUTH_PATH", "data/plugin-auth.json")
# 宿主 init 等待上限（秒）。实测 16 槽位仅 70ms，5s 留足冷启动余量。
PLUGIN_INIT_TIMEOUT = _float("ZCODE_PLUGIN_INIT_TIMEOUT", 5.0)
# 首次响应（head）等待上限（秒）：上游思考可能较久，与 native 的 read=None 同源考虑。
PLUGIN_HEAD_TIMEOUT = _float("ZCODE_PLUGIN_HEAD_TIMEOUT", 300.0)
# 宿主连续失败达此次数即冷却（spec: plugin-channel-runtime REQ-PLUG-08）
PLUGIN_RESTART_LIMIT = _int("ZCODE_PLUGIN_RESTART_LIMIT", 3)
PLUGIN_COOLDOWN_SECONDS = _int("ZCODE_PLUGIN_COOLDOWN_SECONDS", 300)
# 重启宿主前等在飞请求结束的上限（秒）。重启由人工操作触发，频率低，# 无需 Go 侧 retireWait=10min 的滚动替换窗口。
PLUGIN_DRAIN_TIMEOUT = _int("ZCODE_PLUGIN_DRAIN_TIMEOUT", 30)
# ── vendor 层版本监控 ───────────────────────────────────────────────────────
# 只报告不自动应用：vendor 是向上游发请求的前提，插件回归会让网关整体不可用；
# 而"更新失败自动回滚"本身也要重启宿主，在自动化名义下放大风险。
VENDOR_CHECK_INTERVAL = _int("ZCODE_VENDOR_CHECK_INTERVAL", 86400)
VENDOR_PLUGIN_DIR = _resolve_path("ZCODE_VENDOR_PLUGIN_DIR",
                                  "vendor/magpie-plugins")
# 上游 host.js 的原始地址（用于比对 sha256；只读，不落盘）
VENDOR_HOSTJS_UPSTREAM_URL = os.getenv(
    "ZCODE_VENDOR_HOSTJS_URL",
    "https://raw.githubusercontent.com/yetone/magpie/main/internal/plugin/host.js")

# ── 上游端点 ─────────────────────────────────────────────────────────────────
# 上游端点：默认值统一收口在 constants.py，环境变量仅作覆盖
UPSTREAM = {
    "zai": os.getenv("ZAI_UPSTREAM_URL", constants.MESSAGES_URLS["zai"]),
    "zai_fallback": os.getenv("ZAI_FALLBACK_URL", constants.MESSAGES_URLS["zai_fallback"]),
    "bigmodel": os.getenv("BIGMODEL_UPSTREAM_URL", constants.MESSAGES_URLS["bigmodel"]),
}

# ZCode 计费 / 额度查询端点
ZCODE_BILLING_BASE = constants.BILLING_BASE
# 激活事件上报（测试时指向 Mock 上游）
ZCODE_EVENT_REPORT_URL = os.getenv("ZCODE_EVENT_REPORT_URL", constants.EVENT_REPORT_URL)

# OAuth 与兑换链 origin（测试时指向 Mock 上游）
OAUTH_API_BASE = os.getenv("ZCODE_OAUTH_API_BASE", constants.ZCODE_ORIGIN + "/api/v1")
ZAI_EXCHANGE_ORIGIN = os.getenv("ZCODE_EXCHANGE_ORIGIN", constants.ZAI_API_ORIGIN)

# ── 多出口代理池 ─────────────────────────────────────────────────────────────
# 全局多出口代理列表（英文逗号分隔，如 "http://127.0.0.1:21081,http://127.0.0.1:21082"）
# 未配置时自动回落为账号专属直连 Client（优雅降级）
def get_global_proxies() -> list[str]:
    raw = os.getenv("ZCODE_PROXIES", "").strip()
    return [p.strip() for p in raw.split(",") if p.strip()]

ZCODE_PROXIES = get_global_proxies()
CLIENT_IDLE_TIMEOUT = _int("ZCODE_CLIENT_IDLE_TIMEOUT", 300)

USER_AGENT = os.getenv("UPSTREAM_USER_AGENT", constants.USER_AGENT)
APP_VERSION = "2.6.7"

_FRONTEND_VERSION_FILE = FRONTEND_DIR / "version"


def frontend_version() -> str:
    """前端版本号（frontend/version 文件，每次读取 → 前端独立发版即生效）。

    文件缺失/为空时回退 APP_VERSION，保证本地开发与旧部署不破。
    """
    try:
        v = _FRONTEND_VERSION_FILE.read_text("utf-8").strip()
        return v or APP_VERSION
    except OSError:
        return APP_VERSION

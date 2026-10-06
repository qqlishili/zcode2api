# 03 — 数据格式与存储规范

状态：SQLite / 配置描述当前实现；enc:v1 / `.zsb` 为待实现的来源格式参考。

## 1. 当前 SQLite Schema（`$ZCODE_DATA_DIR/accounts.db`，WAL）

```sql
-- 账号池
CREATE TABLE accounts (
    id          TEXT PRIMARY KEY,          -- <规范化名称>-<8位随机十六进制>；重登幂等由 Store 检查凭证
    provider    TEXT NOT NULL,             -- 'zai' | 'bigmodel'
    name        TEXT,
    mode        TEXT,                      -- 'jwt' | 'apiKey'
    status      TEXT,                      -- active|exhausted|cooling|invalid|disabled
    enabled     INTEGER NOT NULL DEFAULT 1,
    created_at  REAL,
    data        TEXT NOT NULL              -- JSON：凭证 + 运行时统计（见下）
);
CREATE INDEX idx_acc_provider ON accounts(provider);
CREATE INDEX idx_acc_status   ON accounts(status);

-- data JSON 字段（Account.to_dict()）
-- { id, name, provider, mode, jwt_token, api_key, quota, plan, plans, plan_slots, claimable_plans,
--   usage, use_count, fail_count, cooling_until, claim_blocked_until, last_used_at, last_checked_at, ... }

-- 设置 KV（admin_key / gateway_key / 各 interval）
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

存储约定：

- **内存驻留 + 落库同步**：运行期账号对象常驻内存保证轮询游标与状态实时性，每次变更 `INSERT OR REPLACE` 落库；启动时读快照重建（z2a `store.py` 语义）。
- 凭证随 `data` JSON 明文存储；`Store.export` 输出明文 `{version, exported_at, providers}` JSON。加密落库与领取历史待实现。
- `public_view()` 的 `token_masked` 对长度超过 16 的凭证使用 `{key[:8]}…{key[-6:]}`，短字符串沿用原值。

## 2. 运行时配置（settings）

下表为 `app/settings.py` 的主要环境变量。网关密钥由 DB meta 管理，不读取 `ZCODE_GATEWAY_KEY`；`config.example.yaml` 未接入。

| 变量 | 默认 | 说明 |
|------|------|------|
| `ZCODE_PORT` / `ZCODE_HOST` | 3000 / 0.0.0.0 | 服务监听 |
| `ZCODE_ADMIN_KEY` | `zcode` | 后台密码初值（之后以 DB meta 为准） |
| `ZCODE_DATA_DIR` | `./data` | SQLite 与凭证目录 |
| `ZCODE_COOLING_SECONDS` | 300 | 5xx 重试耗尽 / 连接失败冷却；429 为原地等待重试 |
| `ZCODE_ACCOUNT_CONCURRENCY` | 2 | 单号并发初值，DB meta 可热改，0 不限 |
| `ZCODE_QUOTA_REFRESH_INTERVAL` | 60 | 额度轮询初值，DB meta 可热改，0 关闭 |
| `ZCODE_BILLING_REFRESH_MIN_INTERVAL` | 60 | 成功对话与后台额度刷新去抖 |
| `ZCODE_SENTINEL_INTERVAL` / `ZCODE_SENTINEL_AUTO_CLAIM` | 1800 / 1 | 活动哨兵初值，DB meta 可热改 |
| `ZAI_UPSTREAM_URL` / `ZAI_FALLBACK_URL` / `BIGMODEL_UPSTREAM_URL` | 官方端点 | 上游可覆写（测试注入用） |
| `ZCODE_NODE_PATH` / `ZCODE_CAPTCHA_TIMEOUT` / `ZCODE_CAPTCHA_RETRIES` | node / 40s / 4 | 验证码求解 |
| `CAPTCHA_POOL_MIN` / `CAPTCHA_POOL_MAX` / `CAPTCHA_TOKEN_TTL` | 3 / 10 / 75000 | 预解池目标 / 上限 / token TTL（毫秒） |

## 3. enc:v1 编解码（ZCode 客户端凭证格式，zsw zcrypto.rs）

用于 ZCode 客户端凭证（Phase 3，待实现），与 `frontend/js/auth.js` 的同名后台密钥格式不同。

```
字符串形态:  enc:v1:{nonce_b64}.{tag_b64}.{ct_b64}
base64:     URL_SAFE_NO_PAD
nonce:      12 字节随机
tag:        GCM tag，16 字节（注意：加密输出时从密文尾部切 16 字节，验证时拼回 ct 尾部）
算法:       AES-256-GCM，key = SHA256(secret)（32 字节，无 KDF 迭代）
secret 默认: "zcode-credential-fallback:{platform}:{home}:{username}"
             platform ∈ {win32, darwin, linux}   （node os.platform() 语义）
             username 解析顺序: USERNAME → (非Windows) id -un → USER → LOGNAME → "unknown"
env 覆盖:    ZCODE_CREDENTIAL_SECRET
```

Python 参考算法（须通过对拍向量）：

```python
import base64, hashlib
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

PREFIX = "enc:v1:"

def derive_key(secret: str) -> bytes:
    return hashlib.sha256(secret.encode()).digest()

def decrypt_with_secret(value: str, secret: str) -> str:
    body = value.removeprefix(PREFIX)
    n_b64, t_b64, c_b64 = body.split(".")          # 恰好三段
    nonce  = base64.urlsafe_b64decode(n_b64 + "==")
    tag    = base64.urlsafe_b64decode(t_b64 + "==")
    ct     = base64.urlsafe_b64decode(c_b64 + "==")
    assert len(nonce) == 12
    return AESGCM(derive_key(secret)).decrypt(nonce, ct + tag, None).decode()

def encrypt_with_secret(plain: str, secret: str) -> str:
    nonce = os.urandom(12)
    sealed = AESGCM(derive_key(secret)).encrypt(nonce, plain.encode(), None)
    ct, tag = sealed[:-16], sealed[-16:]
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()
    return f"{PREFIX}{b64(nonce)}.{b64(tag)}.{b64(ct)}"
```

> 实现注意：`urlsafe_b64decode` 需补齐 `=` padding；`extra=None`（ZCode 客户端无 AAD）。

### ZCode 客户端相关文件（Phase 3 快照/切换对象）

| 文件 | 内容 | 操作 |
|------|------|------|
| `~/.zcode/v2/credentials.json` | 登录凭证 JSON，敏感字段形如 `enc:v1:...`（含 `zcodejwttoken`） | 快照 / 原子写回 |
| `~/.zcode/v2/config.json` | provider 配置（`builtin:zai-coding-plan` 等，options.apiKey） | 快照 / 原子写回 |
| `~/.zcode/v2/telemetry-state.json` | 遥测状态 | 随账号一并快照 |
| 账号库目录 | `~/.zcode-switch/accounts/{id}/`（zsw 约定，我们读写兼容） | 快照存放；id 白名单 `[A-Za-z0-9-]` |

## 4. `.zsb` 加密封包（与 zcode-switch 互通）

设计目标（待实现）：**zcode-hub 导出的 .zsb 能被 zcode-switch 导入，反之亦然。**

> 状态：**已回填定稿**（2026-09-03，源码：zsw `cipher.rs` 全文 110 行 + `store.rs` `export_bundle_value` L777 / `import_candidates` L794 / Account 结构 L82 / `capture_current` L448）。

### 4.1 外层 envelope（加密层，cipher.rs）

`.zsb` 文件本体是一个 JSON 对象，顶层字段为四个：`format` / `version` / `kdf` / `cipher`：

```json
{
  "format": "zcode-accounts-bundle",
  "version": 1,
  "kdf":    { "algo": "pbkdf2-hmac-sha256", "iters": 100000, "salt": "<STD b64, 16 字节 salt>" },
  "cipher": { "algo": "aes-256-gcm", "nonce": "<STD b64, 12 字节>", "tag": "<STD b64, 16 字节>", "data": "<STD b64>" }
}
```

要点（全部来自 `cipher.rs` 逐行核对）：

- 常量：`FORMAT_BUNDLE = "zcode-accounts-bundle"`，`KDF_ITERS = 100_000`；envelope 的 `version` 恒为 `1`。
- 派生：`key = PBKDF2-HMAC-SHA256(password, salt, 100000, 32B)`，salt 由 OsRng 随机 16 字节。
- 加密：AES-256-GCM，随机 12 字节 nonce；Rust `aes_gcm` 的 `encrypt` 返回 `ciphertext || tag`，zsw 用 `split_at(ct.len()-16)` 把它拆成 `data`（密文本体）与 `tag`（认证标签）**分开存储**。
- base64：`base64::engine::general_purpose::STANDARD` = **标准字母表 + padding**（与 enc:v1 的 URL_SAFE_NO_PAD 不同！Python 侧直接用 `base64.b64encode/b64decode` 即可）。
- 解密侧校验顺序：`kdf`→`cipher` 字段存在性 → salt/nonce/tag/data 可解码 → **nonce 必须 12 字节** → 派生 key → `data || tag` 一起喂给 GCM `decrypt` → 明文必须能 JSON 反序列化。密码错误报 `wrong_password`（这是 zsw UI 判定「密码不对」的唯一依据，我们的实现要保留同语义）。
- `is_sealed()` 判定：对象同时含 `kdf` 与 `cipher` 字段即视为已加密信封。
- 口令：trim 后为空即拒绝 seal（open 侧无此校验，空口令可解密空口令封的包）。

### 4.2 内层明文 payload（store.rs）

`open()` 解出的明文是 `export_bundle_value()` 的 JSON 序列化（`serde_json::to_vec`）：

```json
{
  "format": "zcode-accounts-bundle",
  "version": 2,
  "exportedAt": "2026-09-03T12:00:00+08:00",
  "accounts": [
    {
      "name": "账号显示名",
      "createdAt": "2026-09-01T10:00:00+08:00",
      "credentials": { "oauth:zai:access_token": "enc:v1:...", "zcodejwttoken": "enc:v1:..." },
      "config": { "provider": { "builtin:zai-coding-plan": { "options": { "apiKey": "..." } } } }
    }
  ]
}
```

要点：

- **内层 `version` 是 2，外层 envelope `version` 是 1** —— 两个版本号不通用，互导判定时分开看。
- `accounts[]` 每项四字段：`name` / `createdAt` / `credentials` / `config`（`config` 可为 null，zsw 导入时 `config: null` 允许通过）。注意内层**不含** `id`/`hash`/`updatedAt`/`virtual_device_mid` —— 这些是 zsw 本地账号库的私有字段，导出时被有意剥离。
- `credentials` 是 `~/.zcode/v2/credentials.json` 的原样内容（`capture_current` 直接存 live 文件），敏感值是 `enc:v1:` 密文；`config` 是 `config.json` 原样内容或 null。
- 导入识别：`import_candidates()` 只认 `format == "zcode-accounts-bundle"`，其余（含旧版单账号 `zcode-account`、裸 credentials.json）报「无法识别」拒绝——**不要**为了宽容而放宽这个判定，否则 03 测试文档的负向向量 BN-002/003 会失效。
- 时间戳：zsw 用本地时区 ISO8601（chrono `Local`），我们保持 ISO8601 即可，导入方不校验格式。
- 封包导入计划只取 `credentials` 中的可解字段（provider/apiKey/secret/jwt/name），多余字段忽略（同 zsw 行为）。

### 4.3 口令来源

- 规划：CLI `--password` 或环境变量 `ZSW_PASSWORD`（沿用 zsw 命名；`ZCODE_BUNDLE_PASSWORD` 为备选，`ZSW_PASSWORD` 优先）。

## 5. 运行时产物

| 产物 | 位置 | 生命周期 |
|------|------|----------|
| `accounts.db` (+wal/shm) | `$ZCODE_DATA_DIR` | 常驻，备份对象 |
| 验证码预解池 | 进程内存 | 默认 token TTL 75000ms，进程重启即失 |
| claim_history（规划） | 尚无对应表 | 持久化历史与 UI 仍待实现 |
| 日志 | stdout（由进程管理器接管） | 滚动由部署层负责 |

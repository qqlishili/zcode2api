# 07 — 路线图

模块名保留原计划；勾选仅表示相应功能已有实现，里程碑按出口标准验收。

## 里程碑总览

```
M0 骨架      ──▶ M1 账号运营层(P1) ──▶ M2 网关扩展(P2) ──▶ M3 客户端融合(P3) ──▶ M4 风控对抗(P4, 按需)
 (1-2 天)        (1.5-2 周)              (2 周)                (1 周)
```

## M0 — 骨架（开工首日）

- [ ] 仓库初始化：`app/` 骨架、`requirements.txt`、`config.example.yaml`、CI（pytest + ruff + mypy-basic）
- [ ] 从 zcode2api 复制底座：`store/models/captcha(+captcha_node)/oauth(zai)/routes/gateway(anthropic)/statics`
- [ ] 上游常量收口 `app/constants.py`；`docs/development/03-data-formats.md` 的 .zsb envelope 字段回填（通读 zsw `cipher.rs` 全文 + `store.rs` L1090-L1300）
- [x] Mock 上游最小版（messages + billing + client/configs）进 `tests/mock_upstream/`

**出口标准**：zcode2api 原行为在 Mock 上游下全绿（回归基线建立）。

## M1 — 账号运营层（Phase 1）

- [ ] `pool.py`：三态健康 + 时间窗重进 + round-robin（依赖 M0）
- [ ] `gateway/classify.py` + 故障转移循环（`pool.max_attempts`）
- [ ] `quota.py` v2：PlanSlot/QuotaItem、多端点并发探测、错峰轮询、自动耗尽/恢复标记
- [ ] `claim.py`：preview 轮询 + 自动/手动领取 + 退避 + claim_history
- [ ] `bundle.py`：.zsb 兼容封包（替换明文导出）；凭证落库加密（`ZCODE_MASTER_SECRET`）
- [ ] bigmodel OAuth 授权码流
- [ ] 后台 UI：额度面板（分组 + reset 倒计时）、领取页、池概览

**出口标准**：测试文档 02/04 中 POOL-*/QUOTA-*/CLAIM-*/BUNDLE-* 全绿；真实上游冒烟（额度查询 + 手动领取）通过；`.zsb` 与 zcode-switch 互导成功。

## M2 — 网关扩展（Phase 2）

- [x] `translator/`：OpenAI↔Anthropic 请求/响应/usage/tool_calls 映射（对照表先写测试用例）
- [x] `gateway/openai.py`：`/v1/chat/completions`（流式 + 批量）
- [ ] `translator/sse.py`：SSE 逐块双向翻译
- [ ] `gateway/responses.py`：`/v1/responses` + previous_response_id LRU
- [x] 网关鉴权接入新端点；模型白名单统一

**出口标准**：Claude Code（Anthropic 透传）、OpenAI SDK（chat/completions 流式+批量）、Codex CLI（/v1/responses）三客户端在 Mock 上游回归全绿，真实上游冒烟各 1 次。

## M3 — 客户端融合（Phase 3）

- [ ] `zclient.enc_v1`：编解码 + 对拍向量（含 zsw `node-enc-v1.json`）
- [ ] `zclient` 导入：从本机 `~/.zcode/v2/credentials.json` 解密入池（只读，不写回）
- [ ] `zclient` 快照/切换：三文件快照 + 原子切换 + 切换前保全（可选功能，CLI + 后台按钮）
- [ ] 路径穿越防护 + 失败回滚测试

**出口标准**：ZCLIENT-* 用例全绿；在装有 ZCode 桌面端的机器上实测「导入入池」与「切换身份」各一次。

## M4 — 风控对抗（按需，触发条件驱动）

- [ ] endpoint routing：`agent/configs` 拉取 + URL 重写（fail-open） —— **触发条件**：观测到上游把流量迁往 ultra 端点。
- [ ] client signing V4：Ed25519 + PoW —— **触发条件**：`agent/configs` 返回 `codingPlanSignature.enable=true`。
- [x] start-plan 验证码池预热（进程内，非请求时求解）
- [x] 预解池多大区健康主动充盈：按 (region, proxy) 代理亲和水库维持 Token 最小水位，结合 Singleflight 单飞防惊群，彻底消除热路径现解延迟与网络瞬断暴露。**2026-10-13 修订**：原"按每大区首个健康端口均衡采样"经实测证伪（求解成功率受**每端口产率**支配：被压端口 1/3 成功、同区未压端口 3/3；且 6/8 大区无消费者）。现预热域收窄为可服务账号实际绑定端口、每端口水位 1，并新增端口级退避与稳定出口绑定，见 `openspec/changes/pin-captcha-egress-and-refill-domain/`。
- [x] 代理节点三态断路器与自愈（CLOSED/OPEN/HALF-OPEN）：连续超时（3次）或 WAF 封禁（2次）自动熔断隔离 600s，半开探针自愈恢复。
- [x] 领额度错峰物理门禁：ClaimPacingGate 强制 20s 错峰串行与 45s 排队快速失败，杜绝同代理 1005/3012 WAF 连坐。
- [ ] 阿里验证码环境指纹混淆增强：持续跟进 AliyunCaptcha 特征演进，降低交互式滑块挑战率。
- [ ] 账号与大区配额动态自愈平衡：大区出口完全离线时按优先级受控漂移至备选健康大区。

**出口标准**：触发条件出现后 1 周内上线；Mock 上游覆盖握手/签名/旁路梯子。

## 持续项

- Git 发布同步：完成三处提交号与运行文件核对（见 `06` §5）。
- 真实用量取证：补齐正常业务缓存语义与套餐余额差额证据。
- 会话轮转观察：完成轮转后的有限运行观察。
- 每里程碑：更新任务进度与 `02` 的实际落点，`05` 协议文档按实测修订
- 上游协议变更追踪：`client/configs` 字段漂移（如 startPlanPreview 的 showName 变更已有先例）纳入 QUOTA-* 回归
- 安全目标：`ZCODE_MASTER_SECRET` / `ZSW_PASSWORD` 不落盘、不入日志；`.env` / `data/` 不入镜像（.dockerignore 断言测试）。

# Proposal

> 2026-10-16 核对及复核：**部分实现，未验收**。后端 `quota_start` / `quota_start_ts` 和前端额度生效期、授予量展示已存在，相关后端隔离测试通过；`STATUS_LABEL.exhausted` 仍固定为“用完”。另在隔离环境调用当前 `planGroupHtml`，已复现 `effective_at` 过去仍标“未生效”，所以待生效 / 待结算语义与状态文案均未闭合。本次未修改业务代码或完成浏览器及当前线上验收。见 [项目交接](../../../docs/HANDOFF.md)。

> 2026-10-17 继续执行：上述为历史快照。待生效/待结算及按全部真实余额判定已由 `fix-global-build-entitlement-display` / `entitlements-fallback-before-balances` 上线；本方案只补时间一致性、临期计算和对应验收，保持既有状态规则。原始方案存于本轮诊断目录，以下更新为当前执行口径。

## Why

套餐卡片的时间与状态展示存在 4 类不一致（2026-10-14 实测）：

1. **时间格式不一致**：`starts_at` 走前端 `fmtDate()`（`10/14 14:33`，无年份），`ends_at` 走后端 `_extract_expire`（`2026-10-15 23:59`，有年份），拼接后成 `10/14 14:33 ~ 2026-10-15 23:59`。
2. **语义不准确**：`ZCode Global Build`（`daily_night` 夜间活动）的 `starts_at=10-14 14:33`，但额度 `entitlements[].effective_at=10-14 23:00`——显示 `14:33` 会让用户以为额度那时就能用，实际要等 23:00。
3. **`国际-ok li` 四句矛盾文案**：`用完`（status）+ `有效期 2026-10-15 23:59:59`（planEnd）+ `额度已授予，等待结算`（last_error）+ `额度未获取，点上方刷新额度`（fallbackQuotaHtml 兜底）——四处独立渲染，未归一化。
4. **`待生效` 样式**不统一（需优化视觉，P2）。

## What Changes

**保留时间戳作为计算依据，统一北京时间短格式**：

- 后端 `_build_plan_slots` 新增 `quota_start` 字段：额度生效时间 = `entitlements[].effective_at`（取最早有效值）否则回退 `starts_at`。作为「额度有效期」的唯一起点。
- 后端复用统一的时间解析和 `_fmt_short(ts) -> "MM-DD HH:MM"`，保留 `expire_ts` 供临期计算；秒、毫秒、ISO 时间统一到北京时间，缺失/无效时间保持未知。
- 前端 `planGroupHtml`：时间标签统一显示 **`quota_start ~ ends_at`**（额度生效期，方案 B），格式统一 `MM-DD HH:MM`。
- 前端短时间固定北京时间，不随浏览器时区变化；结束时间基于时间戳判断临期，旧短字符串不猜年份。套餐开始时间的独立展示与排序由 `plan-slot-start-date-display` 承接。
- 保持已上线状态：未来权益显示未生效，已到/未知显示待结算；按所有真实余额判断账号可选性，不将授予量累加到余额。

## Capabilities

### New Capabilities

- `plan-slot-time-display`：统一套餐时间、额度生效期和临期提醒；现有主规范尚无套餐展示能力，新增独立时间契约，不重复另一个未归档方案的状态要求。

## Impact

- 后端：`app/quota.py` 的套餐/余额时间归一化；管理 API 的槽位新增可选 `expire_ts`，模型额度项新增可选 `expires_at_ts`，旧字符串字段保留。
- 前端：`frontend/admin/accounts.html` 的时间格式、额度生效期和临期计算，保持原状态语义。
- 测试：`tests/unit/test_plan_slots.py` 与 `tests/frontend/quota_entitlements.test.mjs`。
- 无数据库迁移、无新服务、无第三方依赖。

## Validation

- 第一组：固定时间的后端与真实页面 JS DOM，覆盖秒/毫秒/ISO、跨年、未知时间、生效时间优先和回退、临期及两个时区。
- 第二组：真实 VPS 管理 API + 已上线页面 JS DOM；只刷新指定账号，不领取活动。状态随当前真实余额核对，历史日期不作当前期望。
- 第三组：公网插件两模型普通/SSE、服务与出口保护核对。采用用户确认的无浏览器验收，真实浏览器视觉与兼容性未验；不核销真实结算恢复。

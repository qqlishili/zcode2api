# 账号列表字段来源与显示规则

2026-10-17 核对。表格六列由 `frontend/admin/accounts.html` 渲染，统一读取 `GET /admin/api/accounts` 的 `accounts[]`；该接口返回本地账号快照，页面每 5 秒在可见且无弹窗时轮询，并不每次直接请求上游。上游 billing 接口前缀为 `https://zcode.z.ai/api/v1/zcode-plan`。

## 凭证 / 账号

| 页面字段 | 管理 API 字段（单个 account） | 原始来源与规则 |
| --- | --- | --- |
| 账号名称 | `name` | 本地账号资料，登录/导入时建立，可通过 `PUT /admin/api/accounts/{id}` 修改；不是 billing 返回的套餐名称 |
| 套餐角标 | `plan.show_name` / `plan.plan_id` | `GET /billing/current` 的 `data.plans[0]` 保存为 `plan`；前端按名称片段归类 Max/Pro/Lite/Start/Trial，未识别则显示原值，不代表全部套餐 |
| 凭证摘要 | `token_masked` | 本地 JWT/API Key 经 `Account.public_view()` 脱敏，不来自 billing |
| 提供商与凭证方式 | `provider`、`mode` | 本地账号资料；显示 `zai/bigmodel` 与 `JWT/API Key` |
| 左侧颜色标记 | `id` | 前端按本地账号 ID 计算颜色，无业务状态含义 |

## 可用状态

| 页面字段 | 管理 API 字段 | 原始来源与规则 |
| --- | --- | --- |
| 状态徽章 | `status` | 本地状态经 `effective_status()` 返回；不是上游同名字段。`active/exhausted/cooling/invalid/disabled` 分别显示正常/用完/限流/异常/禁用。冷却截止已过时返回 active |
| 待生效 / 待结算徽章 | `status` + `last_error` | 仅 exhausted 且错误文本精确匹配“额度已授予，等待生效/结算”时替换“用完”；后台结合 balance 真实余额、current 权益授予量与 pending 生效时间判断。其他活动或模型仍有余额时不因单项未生效停用整号 |
| 冷却倒计时 | `cooling_until` + `status` | 本地网关限流/重试处理写入，显示距截止的剩余时长；有有效倒计时时优先于下一行的套餐结束时间 |
| 有效期 | `plans[].ends_at`（兼容 `expires_at`；无数组回退 `plan`） | 来源 `GET /billing/current` 的 `data.plans[]`；取列表中最晚结束，未在此处额外过滤套餐 status，不是 JWT 到期，也不是模型余额到期。不超过 7 天时警示；已过期也属于该警示条件 |
| 领取避让倒计时 | `claim_blocked_until` | 本地领取业务对上游名额用尽等结果记下的避让截止，并非活动结束。剩余大于零时另行显示 |
| 错误说明 | `last_error` | 本地网关、额度刷新、鉴权或领取过程记录的诊断文案，无单一上游字段；超过 26 字符截短，title 保留完整说明 |

上述是显示规则，不等于实际请求一定成功。状态后台会结合真实余额、鉴权、请求结果及人工启停更新；前端不根据“有效期”自行推导正常状态。

## 额度信息

| 页面字段 | 管理 API 字段 | 原始来源与规则 |
| --- | --- | --- |
| 活动名称 | `plan_slots[].name`，缺失回退 tier/pid | 优先 `GET /billing/balance` 的 `data.plans[].name/plan_id`，无 plans 才回退 current；按 plan_id 分配对应余额，未匹配余额放常规额度 |
| 活动有效期 | `plan_slots[].activity_start_ts` / `ends_at` | balance plans 权益 period：daily 开始取套餐 starts_at；daily_night 取权益 effective_at，归零后取同 user_plan_id、entitlement_id 的余额桶 period_start；one_time 只取 effective_at。结束均取同套餐 ends_at。缺失或冲突显示 —，未识别周期沿用套餐起止。名称与时间同行、不换行，额度列加宽至400px、列间距8px，账号页维持原1280px居中宽度与两侧留白容纳六列；starts_at 原值仍用于原排序 |
| 模型名称 | `plan_slots[].items[].name` | balance `data.balances[].show_name`，兼容 name/model；按套餐匹配后显示 |
| 剩余 / 总额 | items 的 `remaining` / `total` | balance `remaining_units`（缺失兼容 available_units）/ `total_units`；`used` 来自 used_units。页面以 M 表示百万 token，不把权益 grant_units 加入余额 |
| 剩余百分比及进度颜色 | items 的 `remaining`、`total` | 前端计算 remaining/total；剩余不大于零或低于 15% 为红色，低于 50% 为琥珀色，否则绿色；无总额时比例为零 |
| 模型额度右下角 | items/pending 的 `period`；槽位 `ends_at` 或 items 的 `expires_at_ts` / `expires_at` | period 按同 user_plan_id、entitlement_id 关联原权益。daily 显示对应桶 expires_at（今日额度到期）；daily_night/one_time 的余额与授予行显示同套餐 ends_at（活动截止），缺失显示 —；未知周期保留原模型到期展示。不因当前 ends_at/period_end/expires_at 相等就视为通用同义字段 |
| 未生效 / 待结算、授予量与生效提示 | `plan_slots[].pending[]` 的 `name`、`grant_units`、`effective_at` | balance plans（缺失时 current）的 entitlements；该套餐该模型没有已结算桶才列为 pending。未来 effective_at 显示未生效，已到或未知显示待结算，授予量不作为可用余额；Global/Weekend 右下角改为活动截止，生效时刻仍保留在提示中 |
| 待领活动条 | `claimable_plans[]` 的 `plan_id/name/description/grants[]` | 本号 `GET /billing/preview` 的 data.plans，经解析并剔除已持有活动；grants 取 model_usage/token 权益的 show_name、grant_units、period，不合并别号资格。页面 GET accounts 只读取快照 |
| 旧数据回退 | `quota` 与 `plans[].entitlements[]` | 无可展示槽位时，quota 来源 balance 按模型合并的余额，总额/已用/剩余相加、结束取最晚；权益行按原始单项信息补充。它不提供可靠的单活动区间 |

模型项到期、pending 生效与套餐记录范围保留各自来源，展示按 [领域定义](../CONTEXT.md) 区分活动类型。新展示字段在正常余额刷新后补齐，旧槽位暂按旧字段展示；本次只刷新指定两号。活动生效不保证额度已到账或尚有余额。

2026-10-17 23:08 VPS 实测：指定两号的 Global 均从生效前无桶、effective_at=23:00，变为已有正余额和消耗、effective_at=0；新桶 period_start=10-17 23:00，period_end=expires_at=套餐 ends_at=10-18 09:00。ZCode 客户端源码将 effective_at=0 作为立即生效哨兵值，并分别用 expires_at 表达额度桶重置边界、ends_at 表达套餐截止。Weekend 当前三种结束均为10-19 09:00，但 period_start=10-12 20:00 与权益 effective_at=10-16 23:00 不同；按用户规则展示后者，不能据当前快照证明该账号在10-12实际可消费。证据见 `data/diagnostics/plan-field-compare-20261017/after-activation.json`。

## 最近请求

| 页面字段 | 管理 API 字段 | 原始来源与规则 |
| --- | --- | --- |
| 最近时间 | `last_used_at` | 网关进入上游成功分支时记录本机时间；不是 billing 的最后使用时间，也不能当作 SSE 完整成功证明 |
| 最近 20 个结果标记 | `recent_results[].ok/at/detail` | 网关 `record_result()` 本地记录；普通响应记录结果，流式由结果回调记录。旧布尔记录没有时间/详情时显示历史记录/无明细 |

## 历史用量

| 页面字段 | 管理 API 字段 | 原始来源与规则 |
| --- | --- | --- |
| 调用 | `use_count` | 网关本地累计，在上游成功分支加一，流式在交付流之前计入；不是上游 token 用量 |
| 失败 | `fail_count` | 网关本地计数，当前明确增量点是其它 4xx 直接回传分支；不能据此认定已覆盖所有失败种类 |
| 成功率 | `use_count`、`fail_count` | 前端计算 use_count/(use_count+fail_count)，四舍五入；两者均零显示 —。沿用现有统计口径，并非严格的完整请求成功率 |
| 风控 | `risk_strikes` | 本地风控处理累加，允许清理的后续成功分支会归零，不是永久历史累计 |

虽然后台刷新还会请求 `GET /usage` 并保存 `account.usage`，当前 `public_view()` 未返回 usage，这一列没有展示上游 usage 的 token 消耗明细。

## 操作

| 控件 | 依赖字段 | 触发接口 |
| --- | --- | --- |
| 刷新额度（JWT） | mode、id；加载中状态来自前端集合 | `POST /admin/api/accounts/{id}/refresh`；当前会 include_claimable=true，查询 current/balance/usage，资格查询前还会尝试日活上报，不能等同纯余额读取 |
| 自动领取（JWT） | claimable_plans、plans、plan_slots、claim_blocked_until | `POST /admin/api/claim`；资格与避让影响提示，不等于领取成功 |
| 手动领取（JWT）及待领条按钮 | id、claimable_plans[].plan_id | `GET /admin/api/claim/preview?account_id=…` 加载本号候选，随后按方式调用 `POST /admin/api/claim` 或 `/claim/manual`；浏览器验证配置来自 `/claim/captcha-config` |
| 编辑 | id、name、凭证 | `PUT /admin/api/accounts/{id}` |
| 删除 | id | `DELETE /admin/api/accounts`，请求体为 ID 数组 |
| 启用/禁用 | enabled、status | `POST /admin/api/accounts/{id}/enabled`；前端开关勾选条件为 enabled 不为 false 且 status 不是 disabled |

## 近期改动边界

- 当前可解析最早版本 `aa993eb` 已包含状态栏最晚套餐结束、卡片额度生效区间、模型桶到期等不同层级时间。
- `d9379fe` 增加待生效/待结算徽章；`8b17502` 统一北京时间/解析，并将卡片时间拆为套餐期与额度生效期，没有改变状态栏取最晚套餐结束的规则。
- 本次只统一额度卡片为活动有效期；可用状态仅整理规则，等待用户另行指定调整。

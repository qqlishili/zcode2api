# 出口配置管理

## 配置来源与生效

启动时先完成 SQLite Store 加载，再冻结完整出口策略，之后才启动安装、验证码、巡检和探针。网关与 CLI 共用此顺序。

优先级为数据库 `egress_policy` 完整对象、环境默认、用途 `inherit` 解析旧 `ZCODE_EGRESS_MODE`。不同来源不逐字段混合。匿名启动仍使用旧全局模式。

环境默认由 `ZCODE_CHAT_EGRESS_MODE`、`ZCODE_CHAT_PROXY_URL`、`ZCODE_CLAIM_EGRESS_MODE`、`ZCODE_CLAIM_EGRESS_BINDINGS` 定义；两个用途默认为 `inherit`。chat proxy 只接受带端口且无凭证/路径的本地 HTTP URL，WARP 预设为 `http://127.0.0.1:40000`。claim proxy 使用按大区固定的 `(port, node_id, epoch)`。合法空表或部分地区缺项只阻止对应领取，不阻止直连对话；非法结构、未知字段和损坏的数据库对象会明确报错。

保存和恢复默认只改变已保存目标，均需重启生效，不写 `.env`、不重启服务、不发上游请求。服务端同时返回当前生效/已保存策略及来源；规范化策略及来源完全相同时 `pending_restart=false`，策略相同但来源不同仍需重启。恢复环境默认只删除出口覆盖，不改变其它设置、账号和当前运行快照。外部修改 `.env` 也须重启读取。

## 用途与排空

同一账号的 chat / claim 分别缓存客户端，缓存还包含实际代理及槽位身份；嵌套操作只复用同账号、同用途、同实际出口。已准入 direct 的 None 是确定的直连，不能重新按账号代理解析。账号关闭后拒绝新操作，旧流及 producer 释放后才关闭全部用途客户端。

显式领取代理只取账号 assigned_region 的固定绑定。来源缺失、退役、回收、空槽或身份/代次变化均在联网前拒绝，不能换节点或切直连。chat draining 本身不否定来源仍有效的固定领取绑定，也不会把该节点加入对话准入。断路器按用途、实际代理及身份隔离；取消半开网络请求会中性释放唯一恢复资格，不增加奖励探测 POST。

运行快照中的领取绑定加入消费者 fixed_ports 及 fixed 确认；只保存删除、未重启时仍保留原固定引用。物理退役关闭所有用途的新准入，并等待操作、producer、缓存、token 和固定引用排空。保持原注册表协议。

断路器诊断包含 purpose、identity、state、失败计数及剩余隔离时间。节点存在、保存成功和监听开放都不代表业务可服务。

## 后台操作与接口

入口为 `/admin/settings`「配置管理」，原鉴权/刷新/并发/巡检/通知表单继续独立保存。出口区域可选择对话继承/直连/WARP/指定本地 HTTP 代理、领取继承/直连/固定同区代理，并为各地区选择节点。WARP 是本地 40000 的 URL 预设，保存不启动该服务。

节点下拉显示名称、地区、端口及候选/不可用原因，不需手填指纹或代次。允许 billing 的 JWT 账号决定需覆盖地区；遗漏或失效地区明确显示领取离线，候选可选不等于真实领取成功。页面停留期间身份/来源失效时，保存拒绝并要求刷新；不能按裸端口悄悄绑定新节点。

三项接口共用现有管理员鉴权：

| 接口 | 契约 |
|---|---|
| GET `/admin/api/settings/egress` | active_policy / active_source、saved_policy / saved_source、saved_selections、pending_restart |
| GET `/admin/api/settings/egress/nodes` | nodes 的不透明 selection、名称/地区/端口/可选原因，以及 required_regions；不返回节点密码、订阅或账号标识 |
| PUT `/admin/api/settings/egress` | 完整 chat_mode / chat_proxy_url / claim_mode / claim_bindings，绑定值为 nodes 返回的 selection；服务端校验并生成实际身份 |
| PUT 同上，`{"restore_environment":true}` | 只移除该策略覆盖；其它字段不得混入 |

非法字段/类型/模式/URL/跨区返回 400，节点选择失效返回 409，数据库保存失败返回 500；均保留原生效和已保存值。旧 PUT `/settings` 忽略出口键。保存不发活动请求、不覆盖其它设置或修改 `.env`。页面同时展示两个来源和待重启状态，刷新不能把保存当成已切流。

## 链路归属与兼容

| 链路 | 用途 |
|---|---|
| 普通/SSE/三协议对话、Plan 与 API Key 回退、挑战重解、对话探针 | chat |
| claim / preview / 激活、自动领取整次尝试、服务端验证码 | claim |
| ZCode JWT billing/current、balance、usage、对话后异步刷新 | claim |
| 按账号安装/登录事件、哨兵与管理活动查询 | claim |
| 无账号启动、OAuth 建号前、非 ZCode JWT 的旧额度链路 | 旧全局策略 |

插件接收已准入客户端的真实 proxy；继承代理未通过账号/出口确认时明确不可用，显式本地代理可直接尝试，错误不能改为直连。宿主继续将无代理序列化为 `direct`。同次自动领取从 preview 到验证码/POST 持有同一 claim 操作，节流和设备身份保持原约定。

验证码水库/单飞按实际代理和托管身份隔离，producer 保留父用途及引用。周期预热只看可选 Plan 的 chat 目标，保留唯一 direct None、水位及预算；claim 命中或现解均不触发常驻补货。3007 只清失败实际出口/身份，显式 None 只清直连，不清其它水库。

**兼容迁移**：claim 为显式 direct/proxy 时，人工选择账号/套餐仍保留，页面改用已有 POST `/claim` 的服务端验证码；旧 POST `/claim/manual` 在发上游前返回 HTTP 409，`detail.code=claim_verification_route_mismatch`。GET `/claim/captcha-config` 返回 verification_mode=server，此时页面不启动浏览器 SDK。inherit 保留浏览器路径（verification_mode=browser），不宣称该外部 token 已验证同 IP 亲和。

隔离接口/DOM 验证不代表已上线或真实节点可服务；公网、真实领取和额度到账按执行记录分别核验。1003 只证明已进入“已领取”业务返回，不代表新发额度。

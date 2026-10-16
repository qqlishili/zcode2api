# 发布验收

## 提交

- `a2005cfd9021419fe279a705994197d135aeb1d6`：fix(gateway): 正确收口流式静默截断
- `299faab762cf17d2a9560a92fd9c7fa3019fe843`：fix(gateway): 对齐免费模型三档思考契约

原始工作区总差分与两个提交合计补丁 SHA256 完全相同。第一提交按暂存快照独立验证；一次夹具暂存位置错误在提交前修正，复验 66 项通过。未改写历史，未携带私有配置或 OpenSpec。

## 运行证据

| 检查 | 结果 |
|------|------|
| 本机 / GitHub main / VPS HEAD | 均为 299faab762cf17d2a9560a92fd9c7fa3019fe843 |
| 两端工作区 | 干净 |
| 三个核心 Python 文件 SHA256 | 两端一致 |
| 原服务与目录 | 原服务重启成功，实际进程仍读取原目录 |
| /meta、/v1/models | 200，版本 2.6.6、唯一免费 Flash 模型 |
| 三接口非法 medium | 均 400 invalid_request_error，消息列出三档 |
| 本机真实 pi → VPS → 上游 | low/high/max 三次完整 OK，stop 终态，用户配置不变 |
| 真实 Chat 流式 high | 200、完整 OK、finish_reason=stop、[DONE] |
| 真实 Responses 流式 max | 200、完整 OK、response.completed、effort=max |
| 账号数据 | 发布前后 7 个账号均保留 |

继续沿用既有虚拟环境、Node 依赖、服务、目录及数据；未执行 rsync、重建服务或覆盖配置。本次未人为制造生产静默 EOF，相关失败场景以已通过的隔离回归为证。Antigravity CLI 发布验收 PASS，结论保存为 release-acceptance.txt。

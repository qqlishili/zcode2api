# VPS部署验收

任务：`HP_ZCODE_VPS_DEPLOY_20261009_7a10f1a6`。2026-10-09 UTC。

## 结果

仅部署四个已验收运行模块。服务于06:12:26重启，06:12:28恢复；PID由1820332变为3578010。06:14:42复核仍active/running、NRestarts=0。进程Python3.12、cwd与ExecStart路径均为原 `/root/code/dengyie-zcode2api`，端口18088，数据路径仍为该目录data/accounts.db。

本机不经代理直连 `http://107.174.244.167:18088` 的三协议×stream=true/false六请求均实际返回JSON503，保留no_available_account；code=quota_exhausted、cause=quota_exhausted/account_state、retryable=false、retry_after=null，无Retry-After头，request_id=X-Request-Id。后台GET monitoring HTTP200，六个request_id全部匹配，同源原因/重试字段一致，stop_reason=no_candidate、coverage_complete=true、unresolved_count=0。

这是当前账号状态的最终503验收。部署前后均7个enabled账号标记exhausted、zai可选池0，监控在途0；没有修改账号或耗尽额度来制造失败，也没有从中推断历史时窗根因。未验证成功模型链路或额度恢复。

## 文件与回滚

| 文件 | 部署前SHA256 | 部署后SHA256 |
|------|--------------|--------------|
| app/models.py | 3c820a18c15d1a14ff061518a6c097d430f6eaec76eb96f154757cee26bf855a | 39216f4d0849dc7dd25bdd083a44707370d2385290604b32937c7a27aef12cae |
| app/store.py | 4b4db7f78cc4d5fc5aef5b65cfd705032084be0854c7dc703c47f3dec6e8217f | 874258435061a050b9d779b4b4944b52749b4af2832475ca22af14d6b10335a0 |
| app/routes/gateway.py | 157197951dd39d23adcbd6dc9964e559a39d17a310defb5788e291295dda243d | ef257114d3b7dea79d9eda55c63f49075abaea0713bcd1c6ea8353630d7fa3e4 |
| app/reqlog.py | 8c17c1dbe0681b1a7df2554401a8dfa8cfb2a5e37333ee5078e35b74601774ed | ad4a565629ca7fbf529a47329ea8965ecffe84d184f2ca21f1fbd45467a6fcfc |

四个新SHA256与本机核定值一致，权限仍0644、uid/gid=0。回滚备份保留在 `/root/.local/state/zcode2api-deploy/HP_ZCODE_VPS_DEPLOY_20261009_7a10f1a6/backup`，同级manifest.json记录旧指纹、权限及所有者。只备份代码，没有备份或传输账号数据库。

上传只到同级唯一staging目录，先检查上传指纹、原文件未漂移和Python编译，再复核在途0，用同目录临时文件os.replace逐个替换并重启原service；异常路径按manifest恢复四文件并重启。无失败部署或回滚发生。一次前置查询误假设表名而StopIteration，退出1且尚未替换文件；随后从实际源码AST读取_META解决，未猜密钥或改数据库。

## 证据与命令

- `deployment-verification.json`：原始脱敏Netcatty结果、完整实际preflight/backup/deploy/verify命令、时间、PID、指纹和监控。
- `deployment-client-probe.py`：从现有Pi zcode配置内存读取凭据，urllib ProxyHandler({})直连实际IP，六个最小合法请求；不输出或存储密钥。
- `deployment-client-results.json` / `deployment-client.log`：本机实际响应、头、关联ID与UTC时间，命令 `.\.venv\Scripts\python.exe openspec/changes/clarify-gateway-unavailability-reasons/deployment-client-probe.py`，真实退出0。
- journalctl限定本次重启后时窗，服务端只输出白名单类别计数：startup_complete=1，traceback/import_error/syntax_error/missing_module均0，不输出原始MESSAGE。

远端读取SQLite只使用mode=ro、PRAGMA query_only=ON、所需元数据与分组状态计数；没有导入store启动额外应用。管理凭据仅服务端内存用于正常GET；客户端凭据复用本机既有配置。测试没有改任何配置、账号、额度、依赖、Node或其它服务；没有重新跑446项，四文件没有漂移，Python3.12编译和实际接口运行均通过。

## Git与范围

后续状态：2026-10-09 06:58 UTC，新增明确授权下已按原Git流程发布闭合，三处HEAD为82a355f5083274df157f65d6a5082344678dcfb8，VPS工作区干净，运行字节与PID不变。见 [release-verification.md](release-verification.md)。下文保留原部署时点证据；其局部Git限制不作为当前项目规则。

本机与远端HEAD均仍299faab762cf17d2a9560a92fd9c7fa3019fe843。按明确禁止commit/push的部署授权，远端工作区现在仅四个部署模块为M，这是未提交部署，不能说Git日志记录了新版本；版本追溯用上述文件SHA256与备份manifest。

本机原六项无关修改保留（2插入/255删除），未上传pages、06-dev-guide、测试、部署脚本或docs。未初始化、更改Git配置、commit/push/PR或全量拉取覆盖。主控parent-acceptance目录保留。本轮没有启动额外服务或夹具；只清理唯一staging四文件及空目录，保留代码回滚备份、manifest和本机验收证据。

# Git 发布验收

任务：`HP_ZCODE_RELEASE_APPROVED_20261009_63b18a7d`。依据本轮用户明确授权发布指定11文件；此前跨项目Git禁令已撤回。

## 结果

2026-10-09 06:58 UTC，VPS快进完成。三处main均为 `82a355f5083274df157f65d6a5082344678dcfb8`，上一提交为 `299faab762cf17d2a9560a92fd9c7fa3019fe843`。

提交：`fix(gateway): 区分账号不可用原因与重试建议`。恰好11文件，907插入/40删除。完整文件清单、SHA256、Git blob验收、原始远端命令和返回见 `release-verification.json`、`release-head-verification.json`。所有11文件的提交blob、本机文件、VPS文件与受测指纹一致。私有OpenSpec、配置、凭据、数据库和运维记录均未提交。

本机显式暂存11文件后提交，`git push origin main`退出0：`299faab..82a355f main -> main`。`git ls-remote origin refs/heads/main`退出0并返回完整新提交。VPS `git fetch origin main`退出0；核四个dirty模块与新提交完全相同、暂存为空且无其它修改后，保存备份与差分，仅恢复这四文件再 `git merge --ff-only 82a355f5083274df157f65d6a5082344678dcfb8`，退出0且输出Fast-forward。未使用reset --hard、clean或全库stash。

本次备份：`/root/.local/state/zcode2api-release/HP_ZCODE_RELEASE_APPROVED_20261009_63b18a7d`，包含四模块backup、权限/指纹manifest、deployed.diff及verification.json。前次部署原文件备份继续保留。

## 运行与边界

VPS工作区干净，原服务active/running、NRestarts=0，PID仍3578010，cwd仍原目录。四模块最终运行字节未变，因此没有重复重启。06:58:46 UTC，status/monitoring均200，inflight=0，7账号enabled且项目状态exhausted，zai/bigmodel可选池均0。原6组真实503仍在监控中匹配；复用deployment-verification中的响应/头/ID同源证据，不重新发模型请求。

06:59:15 UTC，本机原.venv直连实际IP、禁用代理，只读GET：`/meta`返回200/version=2.6.6；`/v1/models`返回200且仅GLM-5.3-Flash。命令与输出见release-client.log/results.json，退出0。未重复446项测试，受测内容没有变化；不宣称官方实时额度恢复或模型成功调用。

本机暂存为空，仅余原六项：pages.py、06-dev-guide.md修改及三个部署脚本/test_deploy_scripts.py删除；保护基线六项全部匹配。未修改其它项目、Git配置、账号、额度、依赖或生产配置，没有PR、强推、历史改写或OpenSpec归档。

commit时出现fsmonitor IPC非致命警告，commit仍退出0且提交blob核验通过。一次文件读取触发次数Hook，工具明确计数重置后读取成功；没有自动审批拒绝发布。

最终原.venv保护核验在受限沙箱内调用Git失败（子进程退出3221225794，即0xc0000142，脚本退出1）；同一命令require_escalated执行退出0，11项matches_tested=true、六项保护匹配。原始成功输出见release-final-local.log。未重建环境或更改Git配置。

当前交付入口：本文件与release-verification.json。部署记录中的旧HEAD/dirty为历史时点，未抹除。

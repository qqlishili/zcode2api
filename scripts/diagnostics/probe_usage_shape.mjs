// probe_usage_shape.mjs — 判定 usage RPC 的返回结构（决定旁路对照可行性）
//
// 判定问题：插件的 usage 返回什么？能否用于额度旁路对照？
// 结论（2026-10-14 实测）：返回 magpie 卡片格式（plan/until/windows[used,limit,display,
//   resetsAt,span,models]）。是实时数据（消耗后数字上涨）。
//   已知边界：普通与 -Trial 消耗计入**同一窗口**，无法细分到池。
// 用法：bun scripts/diagnostics/probe_usage_shape.mjs
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import readline from 'node:readline';

// ── 路径解析（环境变量优先，其次用项目内 vendor/）──
// REPO_ROOT：本脚本位于 <repo>/scripts/diagnostics/，故上溯两级。
// 默认走 vendor/，与生产（app/settings.py 的 PLUGIN_HOST_JS / PLUGIN_DIR）
// 用**同一份**副本 —— 避免诊断与生产版本不一致，也让项目自包含
// （换机器只需 clone 本仓库及其子模块）。
// 需要对着上游仓库调试时，用 MAGPIE_HOST_JS / MAGPIE_PLUGIN_DIR 覆盖。
const REPO_ROOT = path.resolve(new URL('.', import.meta.url).pathname, '../..');
const BUN_BIN = process.env.ZCODE_BUN_PATH ?? 'bun';
const HOST_JS = process.env.MAGPIE_HOST_JS
  ?? path.join(REPO_ROOT, 'vendor/magpie_host.js');
const PLUGIN_DIR = process.env.MAGPIE_PLUGIN_DIR
  ?? path.join(REPO_ROOT, 'vendor/magpie-plugins/packages/zcode');
const WORK = '/tmp/probe-usage';
fs.mkdirSync(WORK, { recursive: true });

const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
fs.writeFileSync(authPath, JSON.stringify({
  zcode: {
    type: 'oauth', access: 'zcode',
    refresh: JSON.stringify({
      jwt: A.jwt, device: A.device_mid || crypto.randomUUID(), site: 'zai',
      base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
    }),
  },
}, null, 2));

const child = spawn(BUN_BIN, [HOST_JS], { stdio: ['pipe', 'pipe', 'inherit'], cwd: WORK });
let seq = 0;
const waiting = new Map();
readline.createInterface({ input: child.stdout, terminal: false }).on('line', (line) => {
  if (!line.trim()) return;
  let msg; try { msg = JSON.parse(line); } catch { return; }
  const w = waiting.get(msg.id);
  if (msg.event) { if (w && w.onEvent) w.onEvent(msg); return; }
  if (w) { waiting.delete(msg.id); w.resolve(msg); }
});
const rpc = (method, params) => {
  const id = ++seq;
  return new Promise((resolve) => {
    waiting.set(id, { resolve });
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
  });
};

async function main() {
  await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });

  console.log('=== providers（模型表，供 /v1/models 动态化参考）===');
  const p = await rpc('providers', {});
  const zc = (p.result || []).find((x) => x.id === 'zcode');
  const models = (zc && zc.models) || [];
  for (const m of models) {
    console.log(`  ${String(m.id).padEnd(22)} context=${m.context} output=${m.output} reasoning=${m.reasoning} image=${m.image}`);
  }
  console.log('  注：模型挂在 provider.models，accounts[].models 为空数组');

  console.log('');
  console.log('=== usage（额度旁路对照的数据源）===');
  const u = await rpc('usage', { provider: 'zcode', account: 'zcode' });
  const r = u.result || {};
  console.log('  plan :', r.plan);
  console.log('  until:', r.until);
  console.log('  error:', JSON.stringify(r.error));
  for (const w of r.windows || []) {
    console.log(`  window ${String(w.name).padEnd(16)} ${w.display}  resetsAt=${w.resetsAt} span=${w.span}`);
    console.log(`         models=${JSON.stringify(w.models)}`);
  }
  console.log('');
  console.log('原始 JSON（前 400 字符）：');
  console.log(JSON.stringify(r).slice(0, 400));

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

// probe_init_cost.mjs — 判定 N 槽位 init 的耗时与内存（决定同步/异步 init）
//
// 判定问题：多个账号槽位一起 init 会不会拖慢服务启动？
// 结论（2026-10-14 实测）：16 槽位 init = **70 ms**，RSS 29–54 MB。
//   → 因此决定「同步等 init」（超时 5 s），不需要异步初始化。
// 用法：bun scripts/diagnostics/probe_init_cost.mjs [槽位数]
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
const WORK = '/tmp/probe-init';
fs.mkdirSync(WORK, { recursive: true });

const N = parseInt(process.argv[2] || '16', 10);
const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
const REF = () => JSON.stringify({
  jwt: A.jwt, device: A.device_mid || crypto.randomUUID(), site: 'zai',
  base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
});
const sha1 = (s) => crypto.createHash('sha1').update(s).digest('hex').slice(0, 10);

const doc = {};
for (let i = 0; i < N; i++) doc['zcode#h' + sha1('account-' + i)] = { type: 'oauth', access: 'zcode', refresh: REF() };
fs.writeFileSync(authPath, JSON.stringify(doc, null, 2));
console.log(`auth.json 写入 ${N} 个槽位，体积 ${fs.statSync(authPath).size} bytes`);

const t0 = Date.now();
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

const rss = () => {
  try {
    const s = fs.readFileSync(`/proc/${child.pid}/status`, 'utf8');
    return Math.round(parseInt(/VmRSS:\s+(\d+)/.exec(s)[1], 10) / 1024);
  } catch { return -1; }
};

async function main() {
  const init = await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });
  console.log(`${N} 槽位 init 总耗时: ${Date.now() - t0} ms`);
  console.log(`init 后 RSS: ${rss()} MB`);
  const plugins = (init.result && init.result.plugins) || [];
  console.log('init 结果:', JSON.stringify(plugins));

  const p = await rpc('providers', {});
  const zc = (p.result || []).find((x) => x.id === 'zcode');
  const keys = ((zc && zc.accounts) || []).map((a) => a.key);
  console.log(`providers 识别槽位: ${keys.length} 个`);
  console.log(`providers 后 RSS: ${rss()} MB`);

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

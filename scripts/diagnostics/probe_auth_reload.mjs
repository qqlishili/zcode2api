// probe_auth_reload.mjs — 判定改 plugin-auth.json 后是否需要重启 host
//
// 判定问题：账号凭证变化时，改 auth.json 能否不重启 host 就生效？
// 结论（2026-10-14 实测）：**不能**。补写槽位后不重启 → HTTP 401。
//   → 因此写入策略定为「全量重写 + 重启进程」（重启实测 70–84 ms，代价可接受）。
// 用法：bun scripts/diagnostics/probe_auth_reload.mjs
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
const WORK = '/tmp/probe-auth-reload';
fs.mkdirSync(WORK, { recursive: true });

const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
const REF = () => JSON.stringify({
  jwt: A.jwt, device: A.device_mid || crypto.randomUUID(), site: 'zai',
  base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
});

const NEW_SLOT = 'zcode#probe-new-slot';
fs.writeFileSync(authPath, JSON.stringify({
  'zcode': { type: 'oauth', access: 'zcode', refresh: REF() },
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
const rpc = (method, params, onEvent) => {
  const id = ++seq;
  return new Promise((resolve) => {
    waiting.set(id, { resolve, onEvent });
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
  });
};

async function send(account) {
  const chunks = [];
  let head = null;
  await rpc('fetch', {
    provider: 'zcode', account, model: 'GLM-5.3-Flash', npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model: 'GLM-5.3-Flash', max_tokens: 8, stream: false,
      messages: [{ role: 'user', content: 'hi' }],
    })),
    window: 8 << 20, proxy: 'direct',
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  });
  return head && head.status;
}

async function main() {
  await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });

  console.log('步骤 1：用已有的首槽发请求');
  console.log('  HTTP', await send('zcode'));
  await new Promise((r) => setTimeout(r, 6000));

  console.log('');
  console.log('步骤 2：补写新槽位到 auth.json，**不重启** host');
  const doc = JSON.parse(fs.readFileSync(authPath, 'utf8'));
  doc[NEW_SLOT] = { type: 'oauth', access: 'zcode', refresh: REF() };
  fs.writeFileSync(authPath, JSON.stringify(doc, null, 2));
  await new Promise((r) => setTimeout(r, 2000));

  const withoutRestart = await send(NEW_SLOT);
  console.log('  HTTP', withoutRestart, withoutRestart === 401 ? '→ 不生效（需重启）✅ 符合预期' : '→ 意外生效');

  console.log('');
  console.log('步骤 3：重启 host 后重试');
  child.kill();
  await new Promise((r) => setTimeout(r, 1500));
  const t0 = Date.now();
  const child2 = spawn(BUN_BIN, [HOST_JS], { stdio: ['pipe', 'pipe', 'inherit'], cwd: WORK });
  // 重挂事件路由
  waiting.clear();
  readline.createInterface({ input: child2.stdout, terminal: false }).on('line', (line) => {
    if (!line.trim()) return;
    let msg; try { msg = JSON.parse(line); } catch { return; }
    const w = waiting.get(msg.id);
    if (msg.event) { if (w && w.onEvent) w.onEvent(msg); return; }
    if (w) { waiting.delete(msg.id); w.resolve(msg); }
  });
  const rpc2 = (method, params, onEvent) => {
    const id = ++seq;
    return new Promise((resolve) => {
      waiting.set(id, { resolve, onEvent });
      child2.stdin.write(JSON.stringify({ id, method, params }) + '\n');
    });
  };
  await rpc2('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });
  console.log('  重启+init 耗时:', Date.now() - t0, 'ms');

  const chunks = [];
  let head = null;
  await rpc2('fetch', {
    provider: 'zcode', account: NEW_SLOT, model: 'GLM-5.3-Flash', npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model: 'GLM-5.3-Flash', max_tokens: 8, stream: false,
      messages: [{ role: 'user', content: 'hi' }],
    })),
    window: 8 << 20, proxy: 'direct',
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  });
  console.log('  重启后 HTTP', head && head.status, (head && head.status) === 200 ? '→ 生效 ✅' : '→ 仍失败');

  child2.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

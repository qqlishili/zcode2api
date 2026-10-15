// probe_dress_effect.mjs — 判定 dress 是否为必需条件 + 代理节点是否已被 ESA 拉黑
//
// 判定问题：
//   1. 有 dress vs 无 dress，上游响应差异？
//   2. 走代理 vs 直连，上游响应差异？
// 结论（2026-10-14 实测）：
//   - 有 dress + 直连  → HTTP 200
//   - 有 dress + 代理  → HTTP 405 3012（该节点已被 ESA 拉黑）
//   - 无 dress + 直连  → HTTP 405 3012
// 用法：bun scripts/diagnostics/probe_dress_effect.mjs
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
const WORK = '/tmp/probe-dress';
fs.mkdirSync(WORK, { recursive: true });

const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
const REF = () => JSON.stringify({
  jwt: A.jwt, device: A.device_mid || crypto.randomUUID(), site: 'zai',
  base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
});
fs.writeFileSync(authPath, JSON.stringify({
  zcode: { type: 'oauth', access: 'zcode', refresh: REF() },
}, null, 2));

// 代理池
const env = {};
for (const line of fs.readFileSync(path.join(REPO_ROOT, '.env'), 'utf8').split('\n')) {
  const t = line.trim();
  if (t && !t.startsWith('#') && t.includes('=')) {
    const i = t.indexOf('=');
    env[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^"|"$/g, '');
  }
}
const PROXIES = (env.ZCODE_PROXIES || '').split(',').map((s) => s.trim()).filter(Boolean);

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

async function send(label, proxy) {
  const chunks = [];
  let head = null;
  await rpc('fetch', {
    provider: 'zcode', account: 'zcode', model: 'GLM-5.3-Flash', npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model: 'GLM-5.3-Flash', max_tokens: 16, stream: false,
      messages: [{ role: 'user', content: 'hi' }],
    })),
    window: 8 << 20,
    proxy,
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  });
  const text = Buffer.concat(chunks.map((c) => Buffer.from(c, 'base64'))).toString('utf8');
  const tag = head && head.status === 200 ? '✅' : '❌';
  console.log(`${tag} ${label.padEnd(28)} HTTP ${head && head.status}  ${text.slice(0, 90)}`);
  return head && head.status;
}

async function main() {
  await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });
  console.log('host 已就绪（fetch 内部即调用插件 dress + startHeaders）');
  console.log('');
  await send('直连（有 dress）', 'direct');
  await new Promise((r) => setTimeout(r, 6000));
  if (PROXIES[0]) {
    await send('代理 ' + PROXIES[0].slice(-5) + '（有 dress）', PROXIES[0]);
    await new Promise((r) => setTimeout(r, 6000));
  }
  await send('直连 + 无 dress 对照', 'direct-nodress');
  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

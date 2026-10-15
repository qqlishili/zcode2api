// probe_host_rpc.mjs — 证明 host.js 可独立运行（不需要 magpie Go 二进制）
//
// 判定问题：magpie 的插件宿主能否脱离 Go 程序独立驱动？
// 结论：能。init + fetch 走通，直连返回 HTTP 200。
//
// 用法：bun scripts/diagnostics/probe_host_rpc.mjs
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
const WORK = '/tmp/probe-host-rpc';
fs.mkdirSync(WORK, { recursive: true });

const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
fs.writeFileSync(authPath, JSON.stringify({
  zcode: {
    type: 'oauth',
    access: 'zcode',
    refresh: JSON.stringify({
      jwt: A.jwt,
      device: A.device_mid || crypto.randomUUID(),
      site: 'zai',
      base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
    }),
  },
}, null, 2));

const child = spawn(BUN_BIN, [HOST_JS], { stdio: ['pipe', 'pipe', 'inherit'], cwd: WORK });
let seq = 0;
const waiting = new Map();

readline.createInterface({ input: child.stdout, terminal: false }).on('line', (line) => {
  if (!line.trim()) return;
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
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

async function main() {
  const t0 = Date.now();
  const init = await rpc('init', {
    authPath,
    modelsDevPath: '',
    piPath: '',
    directory: WORK,
    config: {},
    prefer: {},
    options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });
  const initMs = Date.now() - t0;
  const pushed = init.result && init.result.plugins;
  console.log('init 耗时:', initMs, 'ms');
  console.log('init 结果:', JSON.stringify(pushed));
  const provides = (pushed && pushed[0] && pushed[0].provides) || [];
  console.log('provides 含 zcode ?', provides.includes('zcode'));

  const chunks = [];
  let head = null;
  await rpc('fetch', {
    provider: 'zcode',
    account: 'zcode',
    model: 'GLM-5.3-Flash',
    npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model: 'GLM-5.3-Flash', max_tokens: 16, stream: false,
      messages: [{ role: 'user', content: 'hi' }],
    })),
    window: 8 << 20,
    proxy: 'direct',
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  });

  const text = Buffer.concat(chunks.map((c) => Buffer.from(c, 'base64'))).toString('utf8');
  console.log('');
  console.log('fetch HTTP:', head && head.status, '  server:', head && head.headers && head.headers.server);
  console.log('响应前 160:', text.slice(0, 160));
  console.log('');
  console.log(head && head.status === 200 ? '✅ host.js 可独立运行' : '❌ 未返回 200');

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

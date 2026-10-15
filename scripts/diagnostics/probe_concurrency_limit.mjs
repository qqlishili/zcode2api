// probe_concurrency_limit.mjs — 判定上游单账号并发上限
//
// 判定问题：一个 ZCode 账号能同时承接几个请求？
// 结论（2026-10-14 实测）：**上限 = 3**。
//   并发 1 → 1/1 成功；并发 2 → 2/2；并发 4 → 3/4（出现 429）；并发 8 → 3/8；并发 32 → 3/32
//   429 响应体：{"code":3008,"msg":"user concurrency limit exceeded"}，**无 Retry-After 头**。
//   → 因此保持 ACCOUNT_CONCURRENCY=2（安全），且扫描请求须计入同一计数。
// 用法：bun scripts/diagnostics/probe_concurrency_limit.mjs
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
const WORK = '/tmp/probe-concurrency';
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
const rpc = (method, params, onEvent) => {
  const id = ++seq;
  return new Promise((resolve) => {
    waiting.set(id, { resolve, onEvent });
    child.stdin.write(JSON.stringify({ id, method, params }) + '\n');
  });
};

function one(i, tag) {
  const chunks = [];
  let head = null;
  return rpc('fetch', {
    provider: 'zcode', account: 'zcode', model: 'GLM-5.3-Flash', npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model: 'GLM-5.3-Flash', max_tokens: 8, stream: false,
      messages: [{ role: 'user', content: `${tag} #${i}` }],
    })),
    window: 8 << 20, proxy: 'direct',
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  }).then(() => {
    const text = Buffer.concat(chunks.map((c) => Buffer.from(c, 'base64'))).toString('utf8');
    return { status: head && head.status, headers: head && head.headers, body: text };
  });
}

async function main() {
  await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });

  for (const n of [1, 2, 4, 8]) {
    const t0 = Date.now();
    const res = await Promise.all(Array.from({ length: n }, (_, i) => one(i, 'c' + n).catch((e) => ({ err: String(e) }))));
    const ok = res.filter((r) => r.status === 200).length;
    const codes = {};
    for (const r of res) codes[r.status || 'ERR'] = (codes[r.status || 'ERR'] || 0) + 1;
    console.log(`并发 ${String(n).padEnd(3)} 墙钟 ${String(Date.now() - t0).padEnd(6)}ms  成功 ${ok}/${n}  状态 ${JSON.stringify(codes)}`);
    const r429 = res.find((r) => r.status === 429);
    if (r429) {
      console.log(`   429 retry-after=${r429.headers && r429.headers['retry-after']}  body=${r429.body.slice(0, 110)}`);
    }
    await new Promise((r) => setTimeout(r, 8000));
  }

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

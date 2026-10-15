// probe_trial_model.mjs — 判定 -Trial 后缀在 plugin 通道下的行为
//
// 判定问题：客户端传 GLM-5.3-Flash-Trial 会不会走活动额度？与不传有何差异？
// 结论（2026-10-14 实测）：
//   - 三种 model 名（GLM-5.3-Flash / GLM-5.3-Flash-Trial / GLM-5.3-Trial）都返回 200
//   - 传 -Trial 时上游响应中的 model 字段被**归一**（去掉 -Trial）
//   - 缓存命中行为不同（normal: cache_read=1664；trial: cache_read=192）→ 确实走了不同池
//   → 因此决定「model 完整透传」：客户端不传 -Trial 则无副作用。
// 用法：bun scripts/diagnostics/probe_trial_model.mjs
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
const WORK = '/tmp/probe-trial';
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

async function send(model) {
  const chunks = [];
  let head = null;
  await rpc('fetch', {
    provider: 'zcode', account: 'zcode', model, npm: '@ai-sdk/anthropic',
    url: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic/v1/messages',
    method: 'POST',
    headers: { 'content-type': 'application/json', 'anthropic-version': '2023-06-01' },
    body: Buffer.from(JSON.stringify({
      model, max_tokens: 8, stream: false,
      messages: [{ role: 'user', content: 'hi' }],
    })),
    window: 8 << 20, proxy: 'direct',
  }, (ev) => {
    if (ev.event === 'head') head = ev;
    else if (ev.event === 'chunk') chunks.push(ev.data);
  });
  const text = Buffer.concat(chunks.map((c) => Buffer.from(c, 'base64'))).toString('utf8');
  let modelOut = null;
  let usage = null;
  try { const j = JSON.parse(text); modelOut = j.model; usage = j.usage; } catch { /* 非 JSON */ }
  return { status: head && head.status, modelOut, usage, raw: text.slice(0, 80) };
}

async function main() {
  await rpc('init', {
    authPath, modelsDevPath: '', piPath: '', directory: WORK,
    config: {}, prefer: {}, options: {},
    plugins: [{ spec: '@magpie-community/opencode-zcode-auth', target: PLUGIN_DIR }],
  });

  for (const m of ['GLM-5.3-Flash', 'GLM-5.3-Flash-Trial', 'GLM-5.3-Trial']) {
    const r = await send(m);
    console.log(`请求 model=${m.padEnd(22)} → HTTP ${r.status}  响应 model=${r.modelOut}`);
    if (r.usage) {
      console.log(`    usage: input=${r.usage.input_tokens} output=${r.usage.output_tokens} cache_read=${r.usage.cache_read_input_tokens}`);
    }
    await new Promise((res) => setTimeout(res, 7000));
  }

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

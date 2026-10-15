// probe_slot_naming.mjs — 判定槽位命名方案的可行形态
//
// 判定问题：账号 id 形态不统一（UUID / 名字-随机hex / 含中文），哪些可直接作槽位名？
// 结论（2026-10-14 实测）：5 种形态全部可用（原 id / 中文 id / UUID / 纯数字 / 短哈希）。
//   但方案选用「短哈希 zcode#h<sha1[:10]>」：定长、纯 ASCII、纯函数、增删不影响他人。
// 用法：bun scripts/diagnostics/probe_slot_naming.mjs
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
const WORK = '/tmp/probe-slot';
fs.mkdirSync(WORK, { recursive: true });

const A = JSON.parse(fs.readFileSync('/tmp/acc.json', 'utf8'));
const authPath = path.join(WORK, 'plugin-auth.json');
const REF = () => JSON.stringify({
  jwt: A.jwt, device: A.device_mid || crypto.randomUUID(), site: 'zai',
  base: 'https://zcode.z.ai/api/v1/zcode-plan/anthropic',
});
const sha1 = (s) => crypto.createHash('sha1').update(s).digest('hex').slice(0, 10);

const ID_CN = '账号B-309e57a3';
const ID_UUID = '11111111-2222-3333-4444-555555555555';
const ID_LONG = 'sample-long-id-88634403';

const doc = {
  'zcode': { type: 'oauth', access: 'zcode', refresh: REF() },
  ['zcode#' + ID_CN]: { type: 'oauth', access: 'zcode', refresh: REF() },
  ['zcode#' + ID_UUID]: { type: 'oauth', access: 'zcode', refresh: REF() },
  ['zcode#' + ID_LONG]: { type: 'oauth', access: 'zcode', refresh: REF() },
  ['zcode#0']: { type: 'oauth', access: 'zcode', refresh: REF() },
  ['zcode#h' + sha1(ID_LONG)]: { type: 'oauth', access: 'zcode', refresh: REF() },
};
fs.writeFileSync(authPath, JSON.stringify(doc, null, 2));

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

  const p = await rpc('providers', {});
  const zc = (p.result || []).find((x) => x.id === 'zcode');
  const keys = ((zc && zc.accounts) || []).map((a) => a.key);
  console.log('providers 识别槽位:', keys.length, '个');
  console.log(' ', JSON.stringify(keys));
  console.log('');

  const cases = [
    ['首槽 zcode', 'zcode'],
    ['中文 id', 'zcode#' + ID_CN],
    ['UUID id', 'zcode#' + ID_UUID],
    ['长英文 id', 'zcode#' + ID_LONG],
    ['纯数字序号', 'zcode#0'],
    ['短哈希（方案选用）', 'zcode#h' + sha1(ID_LONG)],
    ['不存在的槽位', 'zcode#nope-does-not-exist'],
  ];
  for (const [tag, acc] of cases) {
    const st = await send(acc);
    console.log(`  ${st === 200 ? '✅' : '❌'} ${tag.padEnd(20)} HTTP ${st}`);
    await new Promise((r) => setTimeout(r, 5000));
  }

  child.kill();
  setTimeout(() => process.exit(0), 300);
}

main().catch((e) => { console.error('ERR', e && e.message ? e.message : e); process.exit(1); });

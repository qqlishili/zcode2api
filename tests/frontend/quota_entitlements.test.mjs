/* 额度页真实 JS 的 DOM 行为；固定时间、隔离数据，不访问网络。 */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {after, test} from 'node:test';
import {Window} from '../../captcha_node/node_modules/happy-dom/lib/index.js';

const now=1792216800;
const html=await readFile(new URL('../../frontend/admin/accounts.html',import.meta.url),'utf8');
const window=new Window({settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));
window.Date.now=()=>now*1000;
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]).join('\n');
window.eval(script.replace(/\(async\(\)=>\{[\s\S]*?\}\)\(\);/,''));
after(()=>window.happyDOM.close());

function render(markup){
  const node=window.document.createElement('div');
  node.innerHTML=markup;
  return node;
}

test('同模型的其他活动有余额：已结算与待生效分别展示',()=>{
  const account={mode:'jwt',status:'active',last_error:null,plan_slots:[
    {pid:'future',name:'Global Build',items:[],pending:[{name:'GLM-5.3-Flash',grant_units:100000000,effective_at:now+3600}]},
    {pid:'available',name:'Weekend Build',pending:[],items:[{name:'GLM-5.3-Flash',total:300000000,used:100000000,remaining:200000000}]},
  ]};
  const node=render(window.quotaCell(account));
  assert.equal(node.querySelectorAll('.plan-card').length,2);
  assert.equal(node.querySelectorAll('.is-pending').length,1);
  assert.match(node.textContent,/未生效/);
  assert.match(node.textContent,/200 M \/ 300 M/);
  assert.equal(render(window.statusCell(account)).querySelector('.badge').textContent,'正常');
});

test('余额到账后的页面刷新不再显示待结算',()=>{
  const account={mode:'jwt',status:'active',quota:{'GLM-5.3-Flash':{total:5000000,used:0,remaining:5000000}},plan_slots:[
    {pid:'activity',name:'活动套餐',pending:[],items:[{name:'GLM-5.3-Flash',total:5000000,used:0,remaining:5000000}]},
  ]};
  const node=render(window.quotaCell(account));
  assert.equal(node.querySelectorAll('.is-pending').length,0);
  assert.match(node.textContent,/5 M \/ 5 M/);
  assert.doesNotMatch(node.textContent,/待结算|未生效|额度未获取/);
});

for(const [error,label] of [['额度已授予，等待生效','待生效'],['额度已授予，等待结算','待结算'],['额度已用完','用完']]){
  test(`账号状态徽章：${label}`,()=>{
    const node=render(window.statusCell({status:'exhausted',last_error:error}));
    assert.equal(node.querySelector('.badge').textContent,label);
  });
}

test('旧数据已有余额的模型不重复展示已授予量，过期套餐不展示',()=>{
  const account={mode:'jwt',quota:{'GLM-5.3':{total:3000000,used:0,remaining:3000000}},plans:[
    {status:'active',entitlements:[{show_name:'GLM-5.3',grant_units:3000000,effective_at:0}]},
    {status:'expired',entitlements:[{show_name:'GLM-5.3-Flash',grant_units:100000000,effective_at:now+3600}]},
  ]};
  const node=render(window.fallbackQuotaHtml(account));
  assert.equal(node.querySelectorAll('.quota-card-item').length,1);
  assert.equal(node.querySelectorAll('.is-pending').length,0);
});

test('领取资格只信本号 preview，已持有活动不会再显示可领取',()=>{
  assert.equal(window.hasUnclaimedActivity({plans:[],claimable_plans:[]}),false);
  assert.equal(window.hasUnclaimedActivity({plans:[],claimable_plans:[{plan_id:'eligible'}]}),true);
  assert.equal(window.hasUnclaimedActivity({plans:[{plan_id:'eligible'}],claimable_plans:[{plan_id:'eligible'}]}),false);
});

for(const [name,effectiveAt,label] of [
  ['未来生效',now+3600,'未生效'],
  ['已经生效',now-3600,'待结算'],
  ['未提供生效时间',0,'待结算'],
]){
  test(`套餐槽位：${name}且余额未生成`,()=>{
    const slot={pid:'activity',name:'活动套餐',items:[],pending:[{name:'GLM-5.3-Flash',grant_units:100000000,effective_at:effectiveAt}]};
    const node=render(window.planGroupHtml(slot));
    assert.equal(node.querySelector('.qci-pct').textContent,label);
    assert.equal(node.querySelector('.qci-val').textContent,'+100 M');
    assert.doesNotMatch(node.textContent,/%|额度未获取/);
  });

  test(`旧数据回退：${name}且余额未生成`,()=>{
    const account={mode:'jwt',quota:{},plans:[{status:'active',plan_id:'activity',name:'活动套餐',entitlements:[{show_name:'GLM-5.3-Flash',grant_units:100000000,effective_at:effectiveAt}]}]};
    const node=render(window.fallbackQuotaHtml(account));
    assert.equal(node.querySelector('.qci-pct')?.textContent,label);
    assert.equal(node.querySelector('.qci-val')?.textContent,'+100 M');
    assert.doesNotMatch(node.textContent,/%|额度未获取/);
  });
}

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

test('统一北京时间，不使用进程本地时区',()=>{
  assert.equal(window.fmtDate(1792249200),'10-17 23:00');
  assert.equal(window.fmtDate(0),'—');
  assert.equal(window.fmtDate('invalid'),'—');
});

for(const value of [1792339199,1792339199000,'2026-10-18T15:59:59Z']){
  test(`旧账号状态与余额项的结束时间统一：${value}`,()=>{
    const status=render(window.statusCell({status:'active',plans:[{ends_at:value}]}));
    assert.match(status.textContent,/有效期 10-18 23:59/);
    const row=render(window.balRowHtml({name:'GLM-5.3',total:3,remaining:3,expires_at:value}));
    assert.match(row.textContent,/至 10-18 23:59/);
  });
}

test('套餐期与额度生效期独立，短结束时间临期提醒保持有效',()=>{
  const node=render(window.planGroupHtml({name:'夜间活动',starts_at:now,quota_start_ts:1792249200,
    quota_start:'10-17 23:00',expire:'10-18 23:59',expire_ts:1792339199,items:[],pending:[]}));
  assert.match(node.textContent,/套餐期 10-17 14:00 ~ 10-18 23:59/);
  assert.match(node.textContent,/额度生效期 10-17 23:00 ~ 10-18 23:59/);
  assert.ok(node.querySelector('.warn-line'));
  assert.ok(node.querySelector('.pending-line'));
});

test('相同区间不重复，缺开始仍保留结束',()=>{
  const same=render(window.planGroupHtml({starts_at:now,quota_start_ts:now,quota_start:'10-17 14:00',expire:'10-18 23:59'}));
  assert.match(same.textContent,/套餐期 10-17 14:00 ~ 10-18 23:59/);
  assert.doesNotMatch(same.textContent,/额度生效期/);
  const unknown=render(window.planGroupHtml({expire:'10-18 23:59'}));
  assert.match(unknown.textContent,/套餐期 — ~ 10-18 23:59/);
});

test('跨年和七天边界按完整时间计算，旧短文字不猜年份',()=>{
  const before=window.Date.now;
  window.Date.now=()=>Date.parse('2026-12-30T12:00:00+08:00');
  try{
    const expiry=Date.parse('2027-01-02T23:59:00+08:00')/1000;
    assert.deepEqual({...window.expireInfo('01-02 23:59',expiry)},{text:'01-02 23:59',soon:true,warn:true});
    const current=window.Date.now()/1000;
    assert.equal(window.expireInfo('01-06 12:00',current+7*86400).warn,true);
    assert.equal(window.expireInfo('01-06 12:00',current+7*86400+1).warn,false);
    assert.deepEqual({...window.expireInfo('01-02 23:59')},{text:'01-02 23:59',soon:false,warn:false});
    assert.equal(window.expireInfo('2027-01-02 23:59').text,'01-02 23:59');
  }finally{window.Date.now=before;}
});

test('已开始升序，未知居中，未来和同时间稳定且不改原数组',()=>{
  const slots=[
    {name:'未来远',starts_at:now+7200},{name:'已开始晚',starts_at:now-10},
    {name:'未知一'},{name:'已开始早',starts_at:now-100},
    {name:'未来近',starts_at:now+3600},{name:'现在',starts_at:now},
    {name:'同时间',starts_at:now-10},{name:'未知二'},
  ];
  const original=JSON.stringify(slots);
  const node=render(window.quotaCell({mode:'jwt',plan_slots:slots}));
  assert.deepEqual([...node.querySelectorAll('.plan-card-name')].map(el=>el.textContent),
    ['已开始早','已开始晚','同时间','现在','未知一','未知二','未来远','未来近']);
  assert.equal(JSON.stringify(slots),original);
});

test('夜间权益未来生效不改变套餐已开始的排序',()=>{
  const node=render(window.quotaCell({mode:'jwt',plan_slots:[
    {name:'未来套餐',starts_at:now+100},
    {name:'夜间活动',starts_at:now-100,quota_start_ts:now+10000,quota_start:'10-17 16:46'},
  ]}));
  assert.deepEqual([...node.querySelectorAll('.plan-card-name')].map(el=>el.textContent),['夜间活动','未来套餐']);
});

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

async function loadClaimAccounts(accounts,preview){
  window.api=async(method,path)=>{
    assert.equal(method,'GET');
    if(path==='/accounts')return {accounts,stats:{total:accounts.length}};
    if(path==='/claim/captcha-config')return {verification_mode:'server'};
    return preview(new URL('http://test'+path).searchParams.get('account_id'));
  };
  await window.load();
}

test('本号 preview 成功返回空时，领取弹窗移除旧候选',async()=>{
  const account={id:'stale',name:'旧候选',mode:'jwt',plans:[],claimable_plans:[{plan_id:'old'}]};
  await loadClaimAccounts([account],()=>({preview:[{plans:[],error:null}]}));
  await window.openClaimModal(account.id,'old');
  assert.deepEqual([...window.document.querySelectorAll('#claim-plan option')].map(o=>o.value),['','']);
  assert.equal(window.document.getElementById('claim-plan-id').value,'');
});

test('领取弹窗切换账号后不保留上一账号活动或手填目标',async()=>{
  const accounts=[
    {id:'eligible',name:'本号可领',mode:'jwt',plans:[],claimable_plans:[{plan_id:'own',name:'本号活动'}]},
    {id:'empty',name:'本号无资格',mode:'jwt',plans:[],claimable_plans:[]},
  ];
  await loadClaimAccounts(accounts,id=>({preview:[{plans:accounts.find(a=>a.id===id).claimable_plans,error:null}]}));
  assert.equal(render(window.claimStripHtml(accounts[0])).querySelectorAll('.claim-strip').length,1);
  assert.equal(window.claimStripHtml(accounts[1]),'');
  await window.openClaimModal('eligible','own');
  const select=window.document.getElementById('claim-account');
  select.value='empty';
  await select.onchange();
  assert.equal(window.document.getElementById('claim-plan-id').value,'');
  assert.ok(![...window.document.querySelectorAll('#claim-plan option')].some(o=>o.value==='own'));
});

test('上一账号的 preview 晚返回，不覆盖当前账号的领取候选',async()=>{
  const accounts=['first','second'].map(id=>({id,name:id,mode:'jwt',plans:[],claimable_plans:[]}));
  let finish;
  await loadClaimAccounts(accounts,id=>id==='first'?new Promise(resolve=>{finish=resolve;}):
    {preview:[{plans:[{plan_id:'second-only'}],error:null}]});
  const accountSelect=window.document.getElementById('claim-account');
  accountSelect.innerHTML=accounts.map(a=>`<option value="${a.id}">${a.name}</option>`).join('');
  accountSelect.value='first';
  const pending=window.loadClaimPlans();
  accountSelect.value='second';
  await window.loadClaimPlans();
  finish({preview:[{plans:[{plan_id:'first-only'}],error:null}]});
  await pending;
  assert.deepEqual([...window.document.querySelectorAll('#claim-plan option')].map(o=>o.value),['','second-only']);
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

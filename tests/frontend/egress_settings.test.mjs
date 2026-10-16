/* 配置页 DOM 行为；复用求解器已有 happy-dom，不访问真实网络。 */
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {Window} from '../../captcha_node/node_modules/happy-dom/lib/index.js';

const html=await readFile(new URL('../../frontend/admin/settings.html',import.meta.url),'utf8');
const window=new Window({settings:{disableCSSFileLoading:true,disableJavaScriptFileLoading:true}});
const policy={chat_mode:'direct',chat_proxy_url:'',claim_mode:'proxy',claim_bindings:{HK:{port:21100,node_id:'a'.repeat(64),epoch:1}}};
let state={active_policy:structuredClone(policy),active_source:'environment',saved_policy:structuredClone(policy),saved_source:'environment',saved_selections:{HK:'old-hk'},pending_restart:false};
let nodes=[{region:'HK',port:21100,name:'HK 候选',selection:'old-hk',selectable:true,reason:'候选'}];
const calls=[],toasts=[];
let failure='';
window.api=async(method,path,payload)=>{
  calls.push({method,path,payload});
  if(path.endsWith('/nodes'))return {nodes,required_regions:['HK','JP','TW']};
  if(method==='GET')return structuredClone(state);
  if(failure)throw new Error(failure);
  if(payload.restore_environment){state={...state,saved_policy:structuredClone(policy),saved_source:'environment',saved_selections:{HK:'old-hk'},pending_restart:false};}
  else{state={...state,saved_policy:{...payload,claim_bindings:policy.claim_bindings},saved_source:'database',saved_selections:payload.claim_bindings,pending_restart:true};}
  return structuredClone(state);
};
window.showToast=(message,type)=>toasts.push({message,type});
// happy-dom 未提供浏览器的 Option 构造器，只在测试夹具补齐此 DOM 接口。
window.Option=function(text,value){const option=window.document.createElement('option');option.textContent=text;option.value=value;return option;};
window.document.write(html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,''));
const script=[...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(match=>match[1]).join('\n');
window.eval(script.replace(/\(async\(\)=>\{[\s\S]*?\}\)\(\);/,''));
const element=id=>window.document.getElementById(id);
try{
  element('admin-key').value='existing-mask';
  await window.loadEgress();
  assert.deepEqual(toasts.filter(toast=>toast.type==='error'),[]);
  assert.equal(element('admin-key').value,'existing-mask');
  assert.match(element('egress-coverage').textContent,/1\/3/);
  element('egress-chat-mode').value='warp';
  window.updateEgressFields();
  await window.saveEgress(false);
  assert.equal(calls.at(-1).payload.chat_proxy_url,'http://127.0.0.1:40000');
  assert.equal(calls.at(-1).payload.claim_bindings.HK,'old-hk');
  assert.match(element('egress-active').textContent,/对话 直连/);
  assert.match(element('egress-pending').textContent,/待重启/);
  await window.loadEgress();
  assert.equal(element('egress-chat-mode').value,'warp');
  failure='节点选择已失效，请刷新节点后重新选择';
  await window.saveEgress(false);
  assert.equal(toasts.at(-1).type,'error');
  assert.equal(element('egress-HK').value,'old-hk');
  assert.match(element('egress-pending').textContent,/待重启/);
  nodes=[{...nodes[0],selection:'replacement-hk'}];
  await window.loadEgress();
  assert.equal(element('egress-HK').value,'old-hk');
  assert.equal(element('egress-HK').selectedOptions[0].disabled,true);
  assert.match(element('egress-coverage').textContent,/0\/3/);
  failure='';
  await window.saveEgress(true);
  assert.deepEqual(JSON.parse(JSON.stringify(calls.findLast(call=>call.method==='PUT').payload)),{restore_environment:true});
  assert.equal(element('admin-key').value,'existing-mask');
  assert.equal(element('egress-save').disabled,false);
  assert.ok(calls.every(call=>call.path.startsWith('/settings/egress')));
  console.log('配置页 DOM 验证通过：加载、独立保存、待重启、重载、失败保留、失效节点、恢复默认');
}finally{
  await window.happyDOM.close();
}

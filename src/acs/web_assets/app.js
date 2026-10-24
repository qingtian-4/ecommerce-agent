'use strict';
let csrf='',sid='',wait=null,card=null,source=null,last=0,busy=true,seenEvents=new Set();
const $=id=>document.getElementById(id);
let refreshVersion=0,expiryTimer=null;
const labels={turn_started:'对话开始',tool_requested:'工具请求',tool_completed:'工具回执',business_state_updated:'业务状态已更新',confirmation_required:'等待页面确认',submission_pending:'正在核实提交',submission_verified:'数据库已核实：模拟待审核',turn_completed:'执行完成',turn_failed:'执行未完成，请查询业务状态'};
const cardLabels={READY:'待确认',REJECTED:'已拒绝',EXPIRED:'已过期',UNKNOWN:'结果未知',PENDING_REVIEW:'待审核'};
const cardReasons={preview_available:'已有有效预览，可打开确认。',awaiting_application_decision:'等待应用端选择接受、拒绝或取消。',confirmation_rejected:'本次确认已拒绝或取消，未创建申请。',preview_expired_or_stale:'预览已过期或被更正失效，请重新生成。',no_valid_preview:'当前没有有效预览。',submission_result_unknown:'结果尚未得到权威数据库确认，只能刷新查询。',submission_verified:'已由业务数据库核实为模拟待审核申请。',session_not_created:'尚未建立演示会话。',operation_in_progress:'正在核对应用操作，请等待或刷新查询，不要重复提交。',database_unavailable:'业务数据库暂不可核实，仅可刷新查询。',http_response_unknown:'确认响应未收到，仅可读取结果；请勿重复提交。'};
const prompts=[['商品咨询','查询P003充电器接口'],['订单查询','查询我的订单'],['1 保存订单','我要退O10024，先保存这个订单的退货草稿；顺便说明七天无理由退货的运费规则。'],['2 补充信息','退这单的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件。请保存草稿，先不要预览。'],['3 商品插问','先插问P003充电器有几个接口？之前退货草稿保留，不用修改。'],['4 更正并预览','回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。请生成预览，不能正式申请。'],['5 政策插问','刚才无理由退货的期限和运费是什么？不要取消或修改草稿。'],['7 取消草稿','取消退货草稿'],['8 最新预览','重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，请生成最新预览，不能正式申请。']];
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
async function api(path,body){const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});let value;try{value=await response.json();}catch{throw new Error('服务响应不可读，请查询状态，勿重复确认');}if(!response.ok){const error=new Error(typeof value.detail==='string'?value.detail:'请求未通过校验');error.status=response.status;error.value=value;throw error;}return value;}
function fields(target,pairs){target.replaceChildren();const list=document.createElement('dl');for(const [name,value]of pairs){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'待补充';list.append(dt,dd);}target.append(list);}
function chat(history){$('chat').replaceChildren();for(const row of history){const div=document.createElement('div'),label=document.createElement('span');div.className='bubble '+row.role;label.className='speaker';label.textContent=row.role==='user'?'你':'客服';div.append(label,document.createTextNode(row.content||'此轮未完成，请查询状态'));$('chat').append(div);}$('chat').scrollTop=$('chat').scrollHeight;}
// This marker describes an unacknowledged HTTP attempt, never business success.
function pendingAttempt(value){
  const key='v3-confirm-attempt-'+sid;
  if(value===undefined){try{return JSON.parse(sessionStorage.getItem(key)||'null');}catch{return null;}}
  if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);
}
function unknownCard(reason){return {state:'UNKNOWN',status:'UNKNOWN',reason,allowed_actions:['refresh'],authoritative:false};}
function controls(){
  const readonly=!card||card.state==='UNKNOWN';
  for(const id of ['send','resume','open','accept','reject','cancel','recover'])
    $(id).disabled=busy||readonly||$(id).dataset.unavailable==='true';
}
function renderCard(value){
  const pending=pendingAttempt();
  if(pending){
    const terminal=value.confirmation&&value.confirmation.wait_id===pending.wait_id&&
      (value.state==='PENDING_REVIEW'&&value.authoritative||
       ['rejected','cancelled','invalidated'].includes(value.confirmation.status));
    if(terminal)pendingAttempt(null);
    else value=unknownCard('http_response_unknown');
  }
  card=value;
  $('card-title').textContent=cardLabels[value.state]||'状态待核对';
  $('card-state').textContent=cardLabels[value.state]||value.state;
  $('card-state').dataset.state=value.state;
  $('card-reason').textContent=cardReasons[value.reason]||'请以服务端返回的状态为准';
  const actions=new Set(value.allowed_actions||[]);
  for(const id of ['open','accept','reject','cancel']){
    $(id).hidden=!actions.has(id);$(id).dataset.unavailable=String(!actions.has(id));
  }
  wait=value.confirmation||null;
  $('confirmation').hidden=!['accept','reject','cancel'].some(a=>actions.has(a));
  $('cancel').textContent=value.state==='EXPIRED'?'关闭失效确认':'取消本次确认';
  $('confirm-note').textContent=value.state==='EXPIRED'?
    '旧确认已失效，关闭它不会创建申请。':'请核对商品、数量和原因。这将创建一份模拟待审核申请。';
  $('recover').hidden=true; // Unknown results have no automatic write/resume action.
  $('refresh').disabled=!actions.has('refresh');
  const p=value.preview;
  if(p)fields($('preview'),[['商品',p.content.product_name],['数量',p.content.quantity],['原因',p.content.reason],['订单',p.content.order_id]]);
  else $('preview').textContent=value.state==='UNKNOWN'?'结果未核实，暂不展示可提交预览。':'尚无最新有效预览。';
  $('expiry').textContent=p?'有效至 '+new Date(p.expires_at).toLocaleString():'';
  $('card-request').textContent=value.request?'模拟申请 '+value.request.request_id+' · '+value.request.status:'';
  clearTimeout(expiryTimer);
  if(p&&Date.parse(p.expires_at)>Date.now())expiryTimer=setTimeout(()=>refresh().catch(e=>notice(e.message)),Date.parse(p.expires_at)-Date.now()+100);
  controls();
}
async function refresh(){
  if(!sid)return;
  const version=++refreshVersion;
  try{
    const pending=pendingAttempt();
    if(pending){
      const receipt=await api('/api/sessions/'+sid+'/receipts/'+pending.wait_id);
      if(receipt.verified)pendingAttempt(null);
    }
    const c=await api('/api/sessions/'+sid+'/return-card');
    if(version!==refreshVersion)return;
    renderCard(c);
  }catch(e){
    if(version!==refreshVersion)return;
    busy=false;renderCard(e.value&&e.value.state==='UNKNOWN'?e.value:unknownCard('database_unavailable'));
    notice(e.message);return;
  }
  try{
    const s=await api('/api/sessions/'+sid);
    if(version!==refreshVersion)return;
    chat(s.history);const d=s.draft;
    fields($('draft'),[['状态',d.status||'尚无草稿'],['订单',d.order_id],['商品明细',d.item_id],['数量',d.quantity],['原因',d.reason],['版本',d.revision]]);
    $('requests').replaceChildren();
    if(!s.requests.length)$('requests').textContent='尚无已核实的模拟申请';
    for(const r of s.requests){const a=document.createElement('article'),strong=document.createElement('strong'),code=document.createElement('code');strong.textContent='模拟待审核 · '+r.status;code.textContent=r.request_id;a.append(strong,code,document.createElement('br'),document.createTextNode(r.order_id+' / 明细'+r.item_id+' / '+r.quantity+'件'));$('requests').append(a);}
    $('orders').replaceChildren();
    for(const o of s.orders){const line=document.createElement('p');line.textContent=o.order_id+' · '+o.status;$('orders').append(line);}
    busy=s.jobs.some(j=>['queued','running'].includes(j.status));controls();
  }catch(e){busy=false;controls();notice('辅助会话信息暂不可用；确认结果以卡片的权威查询为准。');}
}

function renderEvent(row){
  if(seenEvents.has(row.id))return;
  seenEvents.add(row.id);last=Math.max(last,row.id);
  const li=document.createElement('li');li.textContent='#'+row.id+' '+labels[row.type]+(row.data.name?' · '+row.data.name:'')+(row.data.status?' · '+row.data.status:'')+(row.data.reason?' · '+row.data.reason:'');$('trace').append(li);
}
async function connect(){
  if(source)source.close();last=0;seenEvents=new Set();$('trace').replaceChildren();
  try{
    let rows;
    do{
      const response=await fetch('/api/sessions/'+sid+'/events?once=true&after='+last,{credentials:'same-origin'});
      if(!response.ok)throw new Error('进度记录暂不可读');
      rows=(await response.text()).split('\n').filter(l=>l.startsWith('data: ')).map(l=>JSON.parse(l.slice(6)));
      rows.forEach(renderEvent);
    }while(rows.length===100);
  }catch(e){notice('进度记录暂不可读，业务操作不会重发。');}
  source=new EventSource('/api/sessions/'+sid+'/events?after='+last);
  source.onopen=()=>{$('connection').textContent='进度已连接';};
  source.onerror=()=>{$('connection').textContent='进度重连中，任务不会重复执行';};
  for(const kind of Object.keys(labels))source.addEventListener(kind,e=>{
    const row=JSON.parse(e.data);if(seenEvents.has(row.id))return;renderEvent(row);
    if(['turn_completed','turn_failed'].includes(kind)){busy=false;refresh().catch(e=>notice(e.message));}
    if(kind==='turn_failed')notice('执行未完成，请先查询权威状态，不要重复确认。');
  });
}
async function action(path,body={}){
  if(busy||card&&card.state==='UNKNOWN')return;
  const confirming=path==='decision';
  if(confirming&&!card.allowed_actions.includes(body.decision))return;
  const request_key=crypto.randomUUID();
  if(confirming){pendingAttempt({wait_id:body.wait_id,request_key});renderCard(unknownCard('operation_in_progress'));}
  notice('');busy=true;controls();
  try{await api('/api/sessions/'+sid+'/'+path,{...body,request_key});}
  catch(e){
    if(confirming&&e.status>=400&&e.status<500)pendingAttempt(null);
    busy=false;
    if(confirming)renderCard(unknownCard('http_response_unknown'));
    controls();notice(e.message);return;
  }
  await refresh(); // Only GET after a lost or acknowledged operation; never retry POST.
}

$('compose').onsubmit=async e=>{e.preventDefault();const text=$('message').value.trim();if(!text||busy)return;await action('messages',{text});$('message').value='';};$('open').onclick=()=>action('confirmation');for(const [id,decision]of [['accept','accept'],['reject','reject'],['cancel','cancel']])$(id).onclick=()=>wait&&action('decision',{wait_id:wait.wait_id,decision});$('resume').onclick=()=>action('resume');$('recover').onclick=()=>refresh().catch(e=>notice(e.message));$('refresh').onclick=()=>refresh().catch(e=>notice(e.message));for(const [name,text]of prompts){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=name;b.onclick=()=>{$('message').value=text;$('message').focus();};$('prompts').append(b);}
(async()=>{try{const b=await api('/api/bootstrap');csrf=b.csrf;sid=b.session_id;$('runtime-mode').textContent=b.runtime?b.runtime.label:'运行模式未知，请重启服务';$('runtime-notice').textContent=b.runtime?b.runtime.notice:'无法核实模型模式；不要将本页面当作真实模型验收。';if(!sid)sid=(await api('/api/sessions',{})).session_id;await refresh();await connect();}catch(e){renderCard(unknownCard('database_unavailable'));notice(e.message);}})();

'use strict';
let csrf='',sid='',wait=null,card=null,source=null,last=0,seenEvents=new Set();
let sessionReady=false,requestPending=false,executionActive=true,snapshotReadable=false;
let connectionState='connecting',refreshVersion=0,expiryTimer=null;
const $=id=>document.getElementById(id),drafts=new Map();
let outgoing=null,lastUserCount=0;
const historyRefreshed=new Set();
const labels={turn_started:'对话开始',tool_requested:'工具请求',tool_completed:'工具回执',business_state_updated:'业务状态已更新',confirmation_required:'等待页面确认',submission_pending:'正在核实提交',submission_verified:'数据库已核实：模拟待审核',turn_completed:'执行完成',turn_failed:'执行未完成',answer_delta:'正文生成中',answer_discarded:'生成草稿已撤回',answer_completed:'回复已完成',answer_checked:'业务声明检查'};
const toolLabels={get_product:'查询商品规格',search_products:'寻找符合条件的商品',list_orders:'查询您的订单',get_order:'核对订单明细',get_logistics:'查询物流进度',search_knowledge:'查询退货政策',get_return_task:'读取退货草稿',update_return_task:'保存退货信息',cancel_return_task:'取消退货草稿',preview_return:'核对并生成退货预览',get_return_requests:'核实模拟申请状态'};
const cardLabels={READY:'待确认',REJECTED:'已拒绝',EXPIRED:'已过期',UNKNOWN:'结果未知',PENDING_REVIEW:'待审核'};
const cardReasons={preview_available:'已有有效预览，可打开确认。',awaiting_application_decision:'请核对后选择接受、拒绝或取消。',confirmation_rejected:'本次确认已拒绝或取消，未创建申请。',preview_expired_or_stale:'预览已过期或被更正失效，请重新生成。',no_valid_preview:'信息齐全后，可在聊天中要求生成预览。',submission_result_unknown:'提交结果尚未核实。可以继续查询，暂不能修改退货或再次提交。',submission_verified:'已由完整业务回执核实为模拟待审核申请。',session_not_created:'尚未建立演示会话。',operation_in_progress:'正在核对确认结果，请勿重复提交。',database_unavailable:'业务数据库暂不可核实，请刷新查询。',http_response_unknown:'未收到确认响应，正在等待权威回执。不会自动重发提交。'};
function notice(text){$('notice').textContent=text;$('notice').hidden=!text;}
function activity(text){$('activity').textContent=text;}
async function api(path,body){
  const response=await fetch(path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:body===undefined?{}:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:body===undefined?undefined:JSON.stringify(body)});
  let value;
  try{value=await response.json();}catch{const error=new Error('服务响应不可读，请刷新查询，勿重复确认');error.status=response.status;throw error;}
  if(!response.ok){const error=new Error(typeof value.detail==='string'?value.detail:'请求未通过校验');error.status=response.status;error.value=value;throw error;}
  return value;
}
function fields(target,pairs){target.replaceChildren();const list=document.createElement('dl');for(const [name,value]of pairs){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=name;dd.textContent=value??'待补充';list.append(dt,dd);}target.append(list);}
// Restricted Markdown grammar: DOM creation only, no innerHTML, images or embedded HTML.
function inlineMarkdown(target,text){
  const pattern=/(\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s)]+)\))/g;
  let at=0,match;
  while((match=pattern.exec(text))){
    target.append(document.createTextNode(text.slice(at,match.index)));
    if(match[2]){const node=document.createElement('strong');node.textContent=match[2];target.append(node);}
    else if(match[3]){const node=document.createElement('code');node.textContent=match[3];target.append(node);}
    else if(/^https?:\/\//i.test(match[5])){const node=document.createElement('a');node.textContent=match[4];node.href=match[5];node.rel='noopener noreferrer';node.target='_blank';target.append(node);}
    else target.append(document.createTextNode(match[0]));
    at=pattern.lastIndex;
  }
  target.append(document.createTextNode(text.slice(at)));
}
function markdown(target,text){
  const lines=String(text).split('\n');let i=0;
  const cells=line=>line.trim().replace(/^\||\|$/g,'').split('|').map(s=>s.trim());
  while(i<lines.length){
    const line=lines[i];
    if(!line.trim()){i++;continue;}
    if(line.startsWith('```')){const pre=document.createElement('pre'),code=document.createElement('code');let body=[];i++;while(i<lines.length&&!lines[i].startsWith('```'))body.push(lines[i++]);code.textContent=body.join('\n');pre.append(code);target.append(pre);i++;continue;}
    if(line.includes('|')&&i+1<lines.length&&/^\s*\|?\s*:?-{3,}/.test(lines[i+1])&&cells(lines[i+1]).every(c=>/^:?-{3,}:?$/.test(c))){
      const wrap=document.createElement('div'),table=document.createElement('table'),head=document.createElement('tr');wrap.className='markdown-table';
      for(const cell of cells(line)){const th=document.createElement('th');inlineMarkdown(th,cell);head.append(th);}table.append(head);i+=2;
      while(i<lines.length&&lines[i].includes('|')){const tr=document.createElement('tr');for(const cell of cells(lines[i++])){const td=document.createElement('td');inlineMarkdown(td,cell);tr.append(td);}table.append(tr);}wrap.append(table);target.append(wrap);continue;
    }
    const title=line.match(/^#{1,6}\s+(.+)/),list=line.match(/^\s*(?:[-*]|\d+\.)\s+(.+)/);
    if(list){const ordered=/^\s*\d+\./.test(line),node=document.createElement(ordered?'ol':'ul');while(i<lines.length){const item=lines[i].match(ordered?/^\s*\d+\.\s+(.+)/:/^\s*[-*]\s+(.+)/);if(!item)break;const li=document.createElement('li');inlineMarkdown(li,item[1]);node.append(li);i++;}target.append(node);continue;}
    const node=document.createElement(title?'h3':line.startsWith('> ')?'blockquote':'p');inlineMarkdown(node,title?title[1]:line.replace(/^> /,''));target.append(node);i++;
  }
}
function scrollChat(change){const node=$('chat'),stick=node.scrollHeight-node.scrollTop-node.clientHeight<90,top=node.scrollTop;change();node.scrollTop=stick?node.scrollHeight:top;}
function draftNodes(){if(outgoing){const div=document.createElement('div'),label=document.createElement('span');div.className='bubble user outgoing';label.className='speaker';label.textContent='你 · '+outgoing.status;div.append(label,document.createTextNode(outgoing.text));$('chat').append(div);}for(const value of drafts.values()){const div=document.createElement('div'),label=document.createElement('span');div.className='bubble assistant draft';label.className='speaker';label.textContent='正在生成 · 业务表述尚未完成核验';div.append(label,document.createTextNode(value.text));$('chat').append(div);}}
function chat(history){lastUserCount=history.filter(row=>row.role==='user').length;if(outgoing&&lastUserCount>outgoing.before&&history.filter(row=>row.role==='user').at(-1)?.content===outgoing.text)outgoing=null;scrollChat(()=>{$('chat').replaceChildren();for(const row of history){const div=document.createElement('div'),label=document.createElement('span'),body=document.createElement('div');div.className='bubble '+row.role;label.className='speaker';label.textContent=row.role==='user'?'你':row.review_status==='legacy_unchecked'?'客服 · 历史回复未经过本轮核验':'客服';body.className='message-body';if(row.role==='assistant')markdown(body,row.content||'此轮未完成，请查询状态');else body.textContent=row.content;div.append(label,body);$('chat').append(div);}draftNodes();});}
function paintDrafts(){scrollChat(()=>{for(const node of $('chat').querySelectorAll('.draft,.outgoing'))node.remove();draftNodes();});}
function answerEvent(row){const key=row.execution_id+':'+row.data.message_id;if(row.type==='answer_delta')drafts.set(key,{job:row.execution_id,text:row.data.text});if(row.type==='answer_discarded')drafts.delete(key);if(['answer_completed','turn_completed','turn_failed'].includes(row.type))for(const [id,value]of drafts)if(value.job===row.execution_id)drafts.delete(id);}
function pendingAttempt(value){const key='v3-confirm-attempt-'+sid;if(value===undefined){try{return JSON.parse(sessionStorage.getItem(key)||'null');}catch{return null;}}if(value)sessionStorage.setItem(key,JSON.stringify(value));else sessionStorage.removeItem(key);}
function unknownCard(reason){return {state:'UNKNOWN',status:'UNKNOWN',reason,allowed_actions:['refresh'],authoritative:false};}
function controls(){
  const executing=requestPending||executionActive||!sessionReady;
  $('send').disabled=executing||!snapshotReadable;
  $('send').textContent=requestPending?'正在发送…':executionActive?'正在处理…':'发送消息 ↗';
  $('resume').disabled=executing||!snapshotReadable||card?.state==='UNKNOWN';
  for(const id of ['open','accept','reject','cancel','recover'])$(id).disabled=executing||$(id).dataset.unavailable==='true'||!card||card.state==='UNKNOWN';
  $('refresh').disabled=!sessionReady;
  $('query-mode').hidden=card?.state!=='UNKNOWN';
}
function renderCard(value){
  const pending=pendingAttempt();
  if(pending){const terminal=value.confirmation&&value.confirmation.wait_id===pending.wait_id&&
    (value.state==='PENDING_REVIEW'&&value.authoritative||['rejected','cancelled','invalidated'].includes(value.confirmation.status));
    if(terminal)pendingAttempt(null);else value=unknownCard('http_response_unknown');}
  card=value;
  const empty=value.reason==='no_valid_preview';
  $('card-title').textContent=empty?'等待生成预览':cardLabels[value.state]||'状态待核对';
  $('card-state').textContent=empty?'暂无预览':cardLabels[value.state]||value.state;
  $('card-state').dataset.state=value.state;
  $('card-reason').textContent=cardReasons[value.reason]||'请以服务端状态为准';
  const actions=new Set(value.allowed_actions||[]);
  for(const id of ['open','accept','reject','cancel']){$(id).hidden=!actions.has(id);$(id).dataset.unavailable=String(!actions.has(id));}
  wait=value.confirmation||null;
  $('confirmation').hidden=!['accept','reject','cancel'].some(a=>actions.has(a));
  $('cancel').textContent=value.state==='EXPIRED'?'关闭失效确认':'取消本次确认';
  $('confirm-note').textContent=value.state==='EXPIRED'?'旧确认已失效，关闭它不会创建申请。':'请核对商品、数量和原因。这将创建模拟待审核申请。';
  $('recover').hidden=true;
  const preview=value.preview;
  if(preview)fields($('preview'),[['商品',preview.content.product_name],['数量',preview.content.quantity],['原因',preview.content.reason==='no_reason'?'无理由退货':preview.content.reason],['订单',preview.content.order_id]]);
  else $('preview').textContent=value.state==='UNKNOWN'?'结果未核实，暂不展示可提交预览。':value.state==='PENDING_REVIEW'?'当前预览已用于此模拟申请。':'尚无最新有效预览。';
  $('expiry').textContent=preview?'有效至 '+new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',dateStyle:'medium',timeStyle:'medium'}).format(new Date(preview.expires_at))+'（北京时间 UTC+08:00）':'';
  $('card-request').textContent=value.request?'模拟申请 '+value.request.request_id+' · 待审核':'';
  clearTimeout(expiryTimer);if(preview&&Date.parse(preview.expires_at)>Date.now())expiryTimer=setTimeout(()=>refresh().catch(e=>notice(e.message)),Date.parse(preview.expires_at)-Date.now()+100);
  controls();
}
function renderFacts(s){
  chat(s.history);const d=s.draft;
  const statuses={draft:'草稿',submitted:'已提交模拟申请',cancelled:'已取消'};
  const item=s.orders.flatMap(o=>o.items||[]).find(i=>i.id===d.item_id);
  fields($('draft'),[['状态',statuses[d.status]||d.status||'尚无草稿'],['订单',d.order_id],['商品',item?.product_name||s.preview?.content.product_name||d.item_id],['数量',d.quantity],['原因',d.reason==='no_reason'?'无理由退货':d.reason],['版本',d.revision]]);
  $('requests').replaceChildren();if(!s.requests.length)$('requests').textContent='尚无已核实的模拟申请';
  for(const r of s.requests){const a=document.createElement('article'),strong=document.createElement('strong'),code=document.createElement('code');strong.textContent='模拟待审核';code.textContent=r.request_id;a.append(strong,code,document.createElement('br'),document.createTextNode(r.order_id+' / 明细'+r.item_id+' / '+r.quantity+'件'));$('requests').append(a);}
  $('orders').replaceChildren();for(const order of s.orders){const article=document.createElement('article'),title=document.createElement('strong'),status=document.createElement('span');title.textContent=order.order_id;status.textContent=' · '+order.status;article.append(title,status);for(const item of order.items||[]){const line=document.createElement('p');line.textContent=item.product_name+' × '+item.quantity;article.append(line);}$('orders').append(article);}
  $('products').replaceChildren();$('product-results').hidden=!s.products?.length;
  for(const product of s.products||[]){const a=document.createElement('article'),title=document.createElement('strong'),line=document.createElement('p');title.textContent=product.name;line.textContent=product.product_id+' · ¥'+product.current_price;a.append(title,line);const attributes=document.createElement('p');attributes.textContent=(product.attributes||[]).join(' · ');a.append(attributes);$('products').append(a);}
}
async function refresh(){
  if(!sid)return;const version=++refreshVersion;
  const pending=pendingAttempt();
  if(pending)try{const receipt=await api('/api/sessions/'+sid+'/receipts/'+pending.wait_id);if(receipt.verified)pendingAttempt(null);}catch{ /* The independent card read can still reconcile; never replay POST. */ }
  try{const value=await api('/api/sessions/'+sid+'/return-card');if(version!==refreshVersion)return;renderCard(value);}
  catch(error){if(version!==refreshVersion)return;renderCard(error.value?.state==='UNKNOWN'?error.value:unknownCard('database_unavailable'));notice(error.message);}
  try{const s=await api('/api/sessions/'+sid);if(version!==refreshVersion)return;renderFacts(s);snapshotReadable=true;const previous=s.jobs.filter(j=>['message','resume'].includes(j.kind)).at(-1);$('resume').hidden=!(previous&&['failed','interrupted'].includes(previous.status));executionActive=s.jobs.some(j=>['queued','running'].includes(j.status));controls();}
  catch(error){if(version!==refreshVersion)return;snapshotReadable=false;controls();notice('会话数据暂不可读；请刷新查询。不会自动重新提交。');}
}
function renderEvent(row){
  if(seenEvents.has(row.id))return;seenEvents.add(row.id);last=Math.max(last,row.id);answerEvent(row);
  if(row.type==='answer_delta')return;
  const li=document.createElement('li');li.textContent='#'+row.id+' '+labels[row.type]+(row.data.name?' · '+row.data.name:'')+(row.data.status?' · '+row.data.status:'')+(row.data.reason?' · '+row.data.reason:'');$('trace').append(li);
}
function progressEvent(row){
  if(row.type==='turn_started')activity('正在理解您的问题…');
  if(row.type==='tool_requested')activity('正在'+(toolLabels[row.data.name]||'核对业务信息')+'…');
  if(row.type==='tool_completed')activity((toolLabels[row.data.name]||'业务查询')+(row.data.status==='success'?'已有回执，正在整理答复…':'未完成，正在处理工具返回的说明…'));
  if(row.type==='answer_delta')activity('正在生成答复…');
  if(row.type==='answer_checked')activity(row.data.status==='corrected'?'部分业务表述已据权威记录纠正。':'已完成范围内的业务声明检查，正在整理答复…');
  if(row.type==='turn_completed')activity('本轮已完成，可以继续提问。');
  if(row.type==='turn_failed')activity(row.data.reason==='budget_exhausted'?'模型预算或本轮时限已到，已停止新的模型请求；业务状态仍可只读查询。':row.data.reason==='model_response_rejected'?'模型响应未通过协议检查，已停止；请先查询业务状态。':row.data.reason==='model_receipt_unverified'?'模型请求回执或费用用量未核实，已停止新的模型请求；请保留现场核对。':'本轮未完成。请先刷新业务状态，再决定是否恢复对话。');
}
async function connect(){
  if(source)source.close();last=0;seenEvents=new Set();drafts.clear();$('trace').replaceChildren();
  try{let rows;do{const response=await fetch('/api/sessions/'+sid+'/events?once=true&after='+last,{credentials:'same-origin'});if(!response.ok)throw new Error('进度记录暂不可读');rows=(await response.text()).split('\n').filter(l=>l.startsWith('data: ')).map(l=>JSON.parse(l.slice(6)));rows.forEach(renderEvent);}while(rows.length===100);paintDrafts();await refresh();}
  catch{notice('进度记录暂不可读，业务操作不会重发。');}
  source=new EventSource('/api/sessions/'+sid+'/events?after='+last);
  source.onopen=()=>{connectionState='connected';$('connection').textContent='进度已连接';};
  source.onerror=()=>{connectionState='reconnecting';$('connection').textContent='进度重连中，任务不会重复执行';};
  for(const kind of Object.keys(labels))source.addEventListener(kind,e=>{const row=JSON.parse(e.data);if(seenEvents.has(row.id))return;renderEvent(row);paintDrafts();progressEvent(row);if(kind==='answer_delta'&&!historyRefreshed.has(row.execution_id)){historyRefreshed.add(row.execution_id);refresh().catch(e=>notice(e.message));}if(['turn_completed','turn_failed'].includes(kind))refresh().catch(e=>notice(e.message));if(kind==='turn_failed')notice('执行未完成，请查询权威状态，不要重复确认。');});
}
async function action(path,body={}){
  if(requestPending||executionActive||!sessionReady)return false;
  const confirming=path==='decision',consent=['confirmation','decision'].includes(path);
  if(consent&&(!card||card.state==='UNKNOWN'))return false;
  if(confirming&&!card.allowed_actions.includes(body.decision))return false;
  const request_key=crypto.randomUUID();
  if(confirming){pendingAttempt({wait_id:body.wait_id,request_key});renderCard(unknownCard('operation_in_progress'));}
  notice('');requestPending=true;if(path==='messages'){outgoing={text:body.text,before:lastUserCount,status:'发送中'};paintDrafts();}activity('正在发送请求…');controls();
  let accepted=false;
  try{const result=await api('/api/sessions/'+sid+'/'+path,{...body,request_key});accepted=true;if(path==='messages'&&outgoing)outgoing.status='已发送，等待处理';executionActive=['queued','running'].includes(result.status);}
  catch(error){const rejected=error.status>=400&&error.status<500;if(path==='messages'){if(rejected)outgoing=null;else if(outgoing)outgoing.status='发送回执未收到，请刷新查看';paintDrafts();}if(confirming&&rejected)pendingAttempt(null);if(confirming&&!rejected)renderCard(unknownCard('http_response_unknown'));notice(rejected?'请求被拒绝（'+error.status+'）：'+error.message:error.message);activity(rejected?'本次请求未受理，请按提示处理。':'未收到明确回执，正在只读核对状态。');}
  finally{requestPending=false;await refresh();controls();}
  return accepted;
}
const prompts=[['商品咨询','查询P003充电器接口'],['订单查询','查询我的订单'],['1 保存订单','我要退O10024，先保存这个订单的退货草稿；顺便说明七天无理由退货的运费规则。'],['2 补充信息','退这单的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件。请保存草稿，先不要预览。'],['3 商品插问','先插问P003充电器有几个接口？之前退货草稿保留，不用修改。'],['4 更正并预览','回到退货，把同订单商品更正为磁吸充电宝；数量1件，无理由，未拆封且满足全部二次销售条件。请生成预览，不能正式申请。'],['5 政策插问','刚才无理由退货的期限和运费是什么？不要取消或修改草稿。'],['7 取消草稿','取消退货草稿'],['8 最新预览','重新退O10024的Life P3耳机1件，无理由，未拆封且满足政策列出的全部二次销售条件，请生成最新预览，不能正式申请。']];
$('compose').onsubmit=async event=>{event.preventDefault();const text=$('message').value.trim();if(!text)return;const accepted=await action('messages',{text,read_only:card?.state==='UNKNOWN'});if(accepted&&$('message').value.trim()===text)$('message').value='';};
$('open').onclick=()=>action('confirmation');for(const [id,decision]of [['accept','accept'],['reject','reject'],['cancel','cancel']])$(id).onclick=()=>wait&&action('decision',{wait_id:wait.wait_id,decision});
$('resume').onclick=()=>action('resume');$('recover').onclick=()=>refresh().catch(e=>notice(e.message));$('refresh').onclick=()=>refresh().catch(e=>notice(e.message));
for(const [name,text]of prompts){const b=document.createElement('button');b.type='button';b.className='secondary';b.textContent=name;b.onclick=()=>{$('message').value=text;$('message').focus();};$('prompts').append(b);}
(async()=>{try{const b=await api('/api/bootstrap');csrf=b.csrf;sid=b.session_id;$('runtime-mode').textContent=b.runtime?b.runtime.label:'运行模式未知';$('runtime-notice').textContent=b.runtime?b.runtime.notice:'无法核实模型模式，请刷新。';if(!sid)sid=(await api('/api/sessions',{})).session_id;sessionReady=true;await refresh();await connect();if(!executionActive)activity('可以查询商品、订单，或准备退货信息。');}catch(error){renderCard(unknownCard('database_unavailable'));notice(error.message);}})();

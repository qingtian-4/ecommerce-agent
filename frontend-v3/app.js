import { el, btn, dots, thumb, badge, facts, markdown, orderCard, logisticsCard, productGrid, statuses, icon, botIcon } from './render.js';


const $ = id => document.getElementById(id);


const labels = { get_product: '查询商品规格', list_products: '查看商品目录', get_order: '核对订单', list_orders: '查找您的订单', get_logistics: '查询物流', search_knowledge: '核对售后政策', get_return_task: '读取退货草稿', update_return_task: '保存退货信息', cancel_return_task: '取消退货草稿', preview_return: '生成退货预览', get_return_requests: '核对申请状态' };


const eventNames = ['turn_started', 'tool_requested', 'tool_completed', 'business_state_updated', 'confirmation_required', 'submission_pending', 'submission_verified', 'turn_completed', 'turn_failed', 'answer_delta', 'answer_discarded', 'answer_completed', 'answer_checked'];


let sid = '', csrf = '', snapshot = null, card = null, messages = [], human = null, source = null, last = 0, seen = new Set(), posting = false, loading = true, refreshVersion = 0, feedbackOpen = '', feedbackPending = '', selected = null, outgoing = null, selectedOrder = null, expiryTimer = null, outgoingBefore = 0, snapshotReadable = false;


const progress = new Map(), drafts = new Map();


let returnNode = el('section', 'return-card');
returnNode.id = 'return-card';


function error(text, kind = "generic") {
  $('global-error').textContent = text;
  $('global-error').hidden = !text;
  $('global-error').dataset.kind = kind;
}


function active() {
  const jobs = snapshot?.jobs || [];
  const streaming = [...progress].some(([id, p]) => p.node.dataset.status === 'running' && !jobs.some(j => j.id === id && !['queued', 'running'].includes(j.status)));
  return posting || loading || jobs.some(j => ['queued', 'running'].includes(j.status)) || streaming;
}


function endpoint(path) {
  return '/api/sessions/' + sid + path;
}


async function api(path, body) {
  const response = await fetch(path, { method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', headers: body === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: body === undefined ? undefined : JSON.stringify(body) });
  let value;
  try {
    value = await response.json();
  } catch {
    const e = new Error('服务响应无法读取，请只读核对结果');
    e.status = response.status;
    throw e;
  }
  if (!response.ok) {
    const e = new Error(typeof value.detail === 'string' ? value.detail : '请求未通过校验');
    e.status = response.status;
    throw e;
  }
  return value;
}


function pending(value) {
  const key = 'v3-confirm-attempt-' + sid;
  if (value === undefined) {
    try {
      return JSON.parse(sessionStorage.getItem(key) || 'null');
    } catch {
      return null;
    }
  }
  if (value)
    sessionStorage.setItem(key, JSON.stringify(value));
  else
    sessionStorage.removeItem(key);
}


function effectiveCard() {
  if (pending())
    return { state: 'UNKNOWN', reason: 'http_response_unknown', allowed_actions: ['refresh'], authoritative: false };
  return card || { state: 'UNKNOWN', reason: 'database_unavailable', allowed_actions: ['refresh'], authoritative: false };
}


function controls() {
  const busy = active();
  $('send').disabled = busy || !sid || !snapshotReadable;
  $('send').setAttribute('aria-label', busy ? '正在回复' : '发送消息');
  $('send').title = busy ? '本轮执行中，请等待回执' : '发送消息';
  $('resume').hidden = !snapshot?.jobs.some(j => ['failed', 'interrupted'].includes(j.status) && ['message', 'resume'].includes(j.kind));
  $('resume').disabled = busy || effectiveCard().state === 'UNKNOWN';
}


function openDialog(title, content) {
  if ($('context-drawer').open) $('context-drawer').close();
  $('dialog-title').textContent = title;
  $('dialog-content').replaceChildren(content);
  if (!$('dialog').open)
    $('dialog').showModal();
}


function closeDialog() {
  $('dialog').close();
}


function humanService() {
  const box = el('div');
  box.append(el('h3', '', '人工服务暂未接入'), el('p', 'muted', human?.message || '当前没有人工坐席、排队或工单系统，无法转接。'), btn('返回智能客服', closeDialog, 'accent'));
  openDialog('人工服务', box);
}


const actions = { detail: showOrder, logistics: showLogistics, afterSale: afterSale };


async function chooseOrder(oid) {
  selectedOrder = await api(endpoint('/orders/' + encodeURIComponent(oid)));
  selected = oid;
  sessionStorage.setItem('v3-shop-order-' + sid, oid);
  renderContext();
  return selectedOrder;
}


async function showOrder(oid) {
  try {
    const order = await chooseOrder(oid);
    const box = el('div');
    box.append(orderCard(order, actions));
    box.append(facts([['下单日期', order.created_at], ['订单金额', order.total_amount == null ? '未提供' : `¥${order.total_amount}`]]));
    for (const item of order.items || []) {
      const row = el('div', 'context-card');
      row.append(thumb(item), el('strong', '', item.product_name), el('p', 'muted', `明细 ${item.id} · 数量 ${item.quantity}`), btn('退这件商品', () => afterSale(oid, item.id), 'order-button main-action'));
      box.append(row);
    }
    box.append(el('p', 'muted', '这里只展示本人合成订单，查看订单不等于选择了退货商品。'));
    openDialog('订单详情 · ' + oid, box);
  } catch (e) {
    error(e.message);
  }
}


async function showLogistics(oid) {
  try {
    await chooseOrder(oid);
    const data = await api(endpoint('/orders/' + encodeURIComponent(oid) + '/logistics'));
    openDialog('物流进度', logisticsCard(data));
  } catch (e) {
    error(e.message);
  }
}


async function allOrders(page = 1) {
  try {
    const data = await api(endpoint('/orders?page=' + page + '&page_size=3'));
    const box = el('div', 'all-orders');
    box.append(el('p', 'muted', `共 ${data.total} 笔合成订单 · 每页 ${data.page_size} 笔`));
    for (const order of data.orders)
      box.append(orderCard(order, actions));
    const pager = el('div', 'pager');
    const prev = btn('上一页', () => allOrders(page - 1)), next = btn('下一页', () => allOrders(page + 1));
    prev.disabled = page <= 1;
    next.disabled = page * data.page_size >= data.total;
    pager.append(prev, el('span', '', `${page} / ${Math.max(1, Math.ceil(data.total / data.page_size))}`), next);
    box.append(pager);
    openDialog('我的订单', box);
  } catch (e) {
    error(e.message);
  }
}


function field(label, node) {
  const wrap = el('label', 'form-field', label);
  wrap.append(node);
  return wrap;
}


async function afterSale(oid, itemId) {
  if (active())
    return;
  try {
    const order = await chooseOrder(oid);
    const box = el('form');
    box.id = 'return-form';
    box.append(el('p', 'muted', '请选择商品并如实填写。信息将作为你的本轮消息交由原有客服工具校验；此处不会直接提交申请。'));


    const item = el('select');
    item.name = 'item_id';
    for (const row of order.items) {
      const option = el('option', '', row.product_name + ' × ' + row.quantity);
      option.value = row.id;
      option.selected = row.id === itemId;
      item.append(option);
    }
    const qty = el('input');
    qty.name = 'quantity';
    qty.type = 'number';
    qty.min = '1';
    qty.value = '1';
    qty.required = true;
    const reason = el('select');
    reason.name = 'reason';
    for (const [value, name] of [['no_reason', '无理由退货'], ['quality', '质量问题']]) {
      const o = el('option', '', name);
      o.value = value;
      reason.append(o);
    }
    const condition = el('select');
    condition.name = 'condition';
    for (const [value, name] of [['sealed', '未拆封'], ['opened', '已拆封']]) {
      const o = el('option', '', name);
      o.value = value;
      condition.append(o);
    }
    const description = el('textarea');
    description.name = 'description';
    description.rows = 2;
    description.maxLength = 400;
    description.placeholder = '质量问题请如实描述';
    const sale = el('input');
    sale.type = 'checkbox';
    sale.name = 'sale_ok';
    const claim = el('label', 'form-field');
    claim.append(sale, document.createTextNode(' 我确认满足政策列出的全部二次销售条件（无理由退货）或属于非人为质量问题（质量退货）。'));


    box.append(field('退货商品', item), field('数量', qty), field('退货原因', reason), field('商品状态', condition), field('问题描述', description), claim);
    const submit = btn('保存草稿并生成预览', null, 'accent');
    submit.type = 'submit';
    submit.disabled = effectiveCard().state === 'UNKNOWN';
    box.append(submit);


    box.onsubmit = async e => {
      e.preventDefault();
      const row = order.items.find(i => String(i.id) === item.value);
      if (!row)
        return;
      if (!sale.checked) {
        error('请先如实确认售后条件；不确定时可在聊天中询问政策。');
        return;
      }
      if (reason.value === 'quality' && !description.value.trim()) {
        error('质量退货需要描述具体问题。');
        return;
      }
      const text = `我要退订单 ${oid} 的明细 ${row.id}（${row.product_name}），数量 ${qty.value} 件，${reason.value === 'no_reason' ? '无理由退货' : '非人为质量问题：' + description.value.trim()}，${condition.value === 'sealed' ? '未拆封' : '已拆封'}，${reason.value === 'no_reason' ? '满足政策列出的全部二次销售条件' : '确认非人为损坏'}。请保存退货草稿并生成预览，不能提交申请。`;
      closeDialog();
      await send(text);
    };
    openDialog('申请售后 · ' + oid, box);
  } catch (e) {
    error(e.message);
  }
}


function renderContext() {
  const order = selectedOrder;
  const box = $('context-order');
  box.replaceChildren(el('h3', '', '当前关联订单'));
  if (order) {
    const line = el('div', 'order-line');
    line.append(thumb(order.items?.[0] || {}), el('strong', 'prod-name', order.items?.[0]?.product_name || order.order_id));
    box.append(line, facts([['订单编号', order.order_id], ['订单状态', statuses[order.status] || order.status], ['下单时间', order.created_at]]), btn('查看订单详情', () => showOrder(order.order_id), 'outline full-width'));
    if (snapshot?.draft.order_id !== order.order_id)
      box.append(el('p', 'order-meta', '当前仅查看此订单，尚未绑定为退货对象。'));
  }
  else
    box.append(el('p', 'empty', '尚未关联订单。查询后点击订单卡片查看，不自动猜选。'), btn('查看我的订单', () => allOrders(), 'outline full-width'));


  const task = $('context-task'), d = snapshot?.draft || {}, c = effectiveCard();
  task.replaceChildren(el('h3', '', '售后服务进度'));
  if (c.state === 'UNKNOWN')
    task.append(el('p', 'muted', '确认结果待核实，请先只读核对，不会自动重新提交。'));
  const started = !!d.order_id;
  const phase = c.state === 'PENDING_REVIEW' && c.authoritative ? 3 : c.preview ? 2 : started ? 1 : 0;
  for (const [i, title, desc] of [[1, '选择退货商品', '选择要处理的商品和数量'], [2, '核对申请信息', '预览与草稿版本须保持一致'], [3, '明确确认并提交', '仅创建合成数据库待审核申请']]) {
    const step = el('div', 'step ' + (phase > i ? 'done' : phase === i ? 'active' : ''));
    const body = el('div');
    body.append(el('strong', '', title), el('p', '', desc));
    step.append(el('span', 'step-number', phase > i ? '✓' : i), body);
    task.append(step);
  }
  if (started) {
    task.append(el('p', 'selected-order', `草稿订单 ${d.order_id} · 版本 ${d.revision ?? '待核实'}`), btn('查看聊天中的售后卡片', () => {
      if ($('context-drawer').open)
        $('context-drawer').close();
      returnNode.scrollIntoView({ block: 'center' });
    }, 'accent full-width'));
  }
  else
    task.append(btn('选择订单并申请售后', () => allOrders(), 'accent full-width'));
}


function renderReturn() {
  const c = effectiveCard(), d = snapshot?.draft || {};
  const shown = !!(d.order_id || c.preview || c.confirmation || pending());
  returnNode.hidden = !shown;
  returnNode.dataset.state = c.state;
  returnNode.replaceChildren();
  const titles = { READY: '等待核对确认', REJECTED: '本次确认已拒绝', EXPIRED: '预览已失效', UNKNOWN: '提交结果未知', PENDING_REVIEW: '模拟待审核' };
  const title = el('h3', '', '退货草稿与确认');
  title.append(el('span', 'state-tag', titles[c.state] || '状态待核验'));
  returnNode.append(title);
  if (d.order_id)
    returnNode.append(facts([['草稿订单', d.order_id], ['数量', d.quantity], ['退货原因', d.reason === 'no_reason' ? '无理由退货' : d.reason], ['草稿版本', d.revision]]));


  if (c.preview) {
    const p = c.preview;
    returnNode.append(facts([['预览商品', p.content.product_name], ['预览数量', p.content.quantity], ['预览编号', p.preview_id]]));
    returnNode.append(el('p', 'order-meta', '有效至 ' + new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', dateStyle: 'medium', timeStyle: 'medium' }).format(new Date(p.expires_at)) + '（北京时间 UTC+08:00）'));
  }


  const reasons = { preview_available: '请先打开确认等待，核对后才可接受。', awaiting_application_decision: '请核对最新商品、数量、原因与有效期。', confirmation_rejected: '此次确认已拒绝或取消，未创建申请。', preview_expired_or_stale: '预览过期或已被更正失效，请通过聊天生成最新预览。', no_valid_preview: '信息齐全后，请在聊天中要求生成预览。', operation_in_progress: '正在核对执行结果，请勿重复操作。', submission_result_unknown: '尚无完整权威回执，仅允许只读核对，不自动重新提交。', http_response_unknown: '未收到明确HTTP回执，只读核对后再决定，不自动重发。', database_unavailable: '数据库暂不可核实，请稍后只读刷新。', submission_verified: '完整业务回执已核实：仅是合成数据库中的待审核申请。' };
  returnNode.append(el('p', 'muted', reasons[c.reason] || '请以服务端权威状态为准。'));
  if (c.state === 'PENDING_REVIEW' && c.authoritative && c.request?.request_id) {
    const proof = el('p', 'verified-request', '模拟申请编号 ' + c.request.request_id);
    proof.dataset.requestId = c.request.request_id;
    returnNode.append(proof);
  }
  const row = el('div', 'card-actions'), allowed = new Set(c.allowed_actions || []);
  for (const [action, text] of [['open', '检查预览并打开确认'], ['accept', '确认模拟申请'], ['reject', '拒绝'], ['cancel', '取消本次确认']])
    if (allowed.has(action) && c.state !== 'UNKNOWN') {
      const b = btn(text, () => action === 'open' ? business('confirmation') : business('decision', { wait_id: c.confirmation.wait_id, decision: action }), action === 'accept' || action === 'open' ? 'accent' : 'outline');
      b.dataset.action = action;
      b.disabled = active();
      row.append(b);
    }
  row.append(btn(c.state === 'UNKNOWN' ? '只读核对结果' : '刷新业务状态', () => refresh(), 'outline'));
  returnNode.append(row);
}


async function vote(turnId, value, reason = null) {
  if (feedbackPending)
    return;
  feedbackPending = turnId;
  try {
    const result = await api(endpoint('/feedback'), { turn_id: turnId, vote: value, reason, request_key: crypto.randomUUID() });
    if (!result.persisted)
      throw new Error('反馈未确认保存');
    feedbackOpen = '';
    await refresh();
  } catch (e) {
    error('反馈回执未核实：' + e.message + '，请刷新查看。');
  } finally {
    feedbackPending = '';
    render();
  }
}


function feedback(message) {
  const wrap = el('div', 'feedback');
  const saved = message.feedback;
  for (const [value, text] of [['helpful', '有用'], ['unhelpful', '没用']]) {
    const b = btn(text, () => value === 'helpful' ? vote(message.turn_id, value) : (feedbackOpen = feedbackOpen === message.turn_id ? '' : message.turn_id, render()), saved?.vote === value ? 'selected' : '');
    b.prepend(icon(value === 'helpful' ? 'up' : 'down'));
    b.dataset.vote = value;
    b.disabled = feedbackPending === message.turn_id;
    wrap.append(b);
  }
  const transfer = btn('转人工', humanService, '');
  transfer.prepend(icon('headset'));
  wrap.append(transfer);
  if (saved)
    wrap.append(el('span', 'feedback-status', '已保存反馈'));
  if (feedbackOpen === message.turn_id) {
    const reasons = el('div', 'feedback-reasons', '哪里需要改进？');
    for (const [value, text] of [['incorrect', '事实有误'], ['incomplete', '没有解决问题'], ['unclear', '表达不清'], ['other', '其他']])
      reasons.append(btn(text, () => vote(message.turn_id, 'unhelpful', value)));
    wrap.append(reasons);
  }
  return wrap;
}


function attachCards(body, cards) {
  if (cards?.orders?.length) {
    const list = el('section', 'results'), head = el('div', 'results-head', '最近订单');
    head.append(el('span', '', `合成数据 · ${Math.min(3, cards.orders.length)} / ${cards.orders.length} 笔`));
    list.append(head);
    cards.orders.slice(0, 3).forEach((o, i) => list.append(orderCard(o, actions, i > 0)));
    list.append(btn('查看全部订单 ›', () => allOrders(), 'results-tail'));
    body.append(list);
  }
  if (cards?.products?.length)
    body.append(productGrid(cards.products));
  for (const value of cards?.logistics || [])
    body.append(logisticsCard(value));
}


function atLatest() {
  const chat = $('chat');
  return chat.scrollHeight - chat.scrollTop - chat.clientHeight < 90;
}


function updateJumpLatest() {
  $('jump-latest').hidden = atLatest();
}


function render() {
  const chat = $('chat'), stick = atLatest(), top = chat.scrollTop;
  chat.replaceChildren();
  if (!messages.length && !outgoing) {
    const w = el('div', 'welcome');
    w.append(el('h2', '', '你好，有什么可以帮你？'), el('p', 'muted', '查订单、了解物流，或者准备退货草稿。我们会根据实际订单提供下一步操作。'));
    chat.append(w);
  }
  const assistantGroups = [], attached = new Set(), turnGroups = new Map(), users = messages.filter(m => m.role === 'user'), jobs = snapshot?.jobs || [];
  const started = jobs.filter(j => j.kind === 'message' && progress.get(j.id)?.started);
  if (started.length === users.length) {
    let i = -1;
    for (const job of jobs) {
      if (job.kind === 'message' && progress.get(job.id)?.started)
        i++;
      if (i >= 0 && ['message', 'resume'].includes(job.kind) && progress.has(job.id)) {
        const key = users[i]?.turn_id;
        if (!turnGroups.has(key))
          turnGroups.set(key, []);
        turnGroups.get(key).push(job.id);
      }
    }
  }
  for (const message of messages) {
    if (message.role === 'user') {
      chat.append(el('div', 'user-message', message.content));
      for (const id of turnGroups.get(message.turn_id) || []) {
        chat.append(progress.get(id).node);
        attached.add(id);
      }
      continue;
    }
    const row = el('article', 'assistant-message');
    row.dataset.messageId = message.message_id;
    const body = el('div', 'message-body');
    const text = el('div', 'message-text');
    markdown(text, message.content || '此轮未完成，请核对状态。');
    body.append(text);
    attachCards(body, message.cards);
    if (message.complete)
      body.append(feedback(message));
    row.append(botIcon(), body);
    chat.append(row);
    assistantGroups.push(body);
  }


  if (outgoing)
    chat.append(el('div', 'user-message outgoing', outgoing));
  for (const [id, record] of progress)
    if (!attached.has(id))
      chat.append(record.node);
  for (const draft of drafts.values()) {
    const row = el('article', 'assistant-message draft');
    row.setAttribute('aria-busy', 'true');
    const body = el('div', 'message-body');
    body.append(el('p', 'message-note', '生成中 · 业务状态待核验'));
    const text = el('div', 'message-text');
    markdown(text, draft.text);
    body.append(text);
    row.append(botIcon(), body);
    chat.append(row);
  }
  if (active() && !drafts.size && ![...progress.values()].some(p => p.node.dataset.status === 'running')) {
    const p = el('div', 'pending-message', '正在接收问题');
    p.append(dots());
    chat.append(p);
  }
  renderReturn();
  if (!returnNode.hidden) {
    const target = assistantGroups.at(-1) || chat;
    target.append(returnNode);
  }
  chat.scrollTop = stick ? chat.scrollHeight : top;
  updateJumpLatest();
  renderContext();
  controls();
}


function progressEvent(event) {
  let row = progress.get(event.execution_id);
  if (['turn_started', 'tool_requested', 'tool_completed', 'turn_completed', 'turn_failed'].includes(event.type)) {
    if (!row) {
      const node = el('details', 'progress-record'), summary = el('summary'), label = el('span'), list = el('ol');
      node.open = true;
      summary.append(label, dots());
      node.append(summary, list);
      row = { node, label, list, tools: new Map(), started: false };
      progress.set(event.execution_id, row);
    }
    if (event.type === 'turn_started') {
      row.started = true;
      row.node.dataset.status = 'running';
      row.label.textContent = '正在处理您的问题';
    }
    if (['tool_requested', 'tool_completed'].includes(event.type)) {
      let item = row.tools.get(event.data.call_id);
      if (!item) {
        item = el('li');
        row.tools.set(event.data.call_id, item);
        row.list.append(item);
      }
      item.textContent = (labels[event.data.name] || '核对业务信息') + ' · ' + (event.type === 'tool_requested' ? '等待回执' : event.data.status === 'success' ? '已有真实回执' : '工具返回拒绝或错误');
      row.label.textContent = '正在' + (labels[event.data.name] || '整理业务信息');
    }
    if (event.type === 'turn_completed') {
      row.node.dataset.status = 'completed';
      row.node.open = false;
      row.node.hidden = !row.tools.size;
      row.label.textContent = '查看处理摘要 · ' + row.tools.size + ' 项工具调用';
    }
    if (event.type === 'turn_failed') {
      row.node.hidden = false;
      row.node.dataset.status = 'failed';
      row.node.open = true;
      row.label.textContent = '本轮未完成，请核对业务状态';
      row.list.append(el('li', '', '执行未完成：' + (event.data.reason || '请只读核对结果')));
    }
  }
  const key = event.execution_id + ':' + event.data.message_id;
  if (event.type === 'answer_delta')
    drafts.set(key, { job: event.execution_id, text: event.data.text });
  if (event.type === 'answer_discarded')
    drafts.delete(key);
  if (['answer_completed', 'turn_completed', 'turn_failed'].includes(event.type))
    for (const [k, v] of drafts)
      if (v.job === event.execution_id)
        drafts.delete(k);
}


function acceptEvent(event) {
  if (seen.has(event.id))
    return;
  seen.add(event.id);
  last = Math.max(last, event.id);
  progressEvent(event);
  if (event.type !== 'answer_delta')
    $('trace').append(el('li', '', `#${event.id} ${event.type} ${event.data.name || event.data.status || ''}`));
  render();
  if (['turn_completed', 'turn_failed', 'tool_completed', 'submission_verified', 'confirmation_required'].includes(event.type))
    refresh().catch(e => error(e.message));
}


async function connect() {
  if (source)
    source.close();
  last = 0;
  seen = new Set();
  drafts.clear();
  progress.clear();
  $('trace').replaceChildren();
  let rows;
  do {
    const response = await fetch(endpoint('/events?once=true&after=' + last), { credentials: 'same-origin' });
    if (!response.ok)
      throw new Error('进度记录暂不可读');
    rows = (await response.text()).split('\n').filter(l => l.startsWith('data: ')).map(l => JSON.parse(l.slice(6)));
    for (const event of rows)
      acceptEvent(event);
  }
  while (rows.length === 100);
  source = new EventSource(endpoint('/events?after=' + last));
  source.onopen = () => {
    document.body.dataset.stream = 'connected';
    $('connection').textContent = '订单 · 物流 · 退货与售后 · 已连接';
    refresh().catch(e => error(e.message));
  };
  source.onerror = () => {
    document.body.dataset.stream = 'reconnecting';
    $('connection').textContent = '连接重试中，任务不会重复执行';
  };
  for (const name of eventNames)
    source.addEventListener(name, e => acceptEvent(JSON.parse(e.data)));
}


async function refresh() {
  if (!sid)
    return;
  const version = ++refreshVersion;
  const [s, c, m] = await Promise.allSettled([api(endpoint('')), api(endpoint('/return-card')), api(endpoint('/messages'))]);
  if (version !== refreshVersion)
    return;
  if (s.status === 'fulfilled') {
    snapshot = s.value;
    snapshotReadable = true;
  }
  else {
    snapshotReadable = false;
    error(s.reason.message, 'state_read');
  }
  if (c.status === 'fulfilled')
    card = c.value;
  else {
    card = { state: 'UNKNOWN', reason: 'database_unavailable', allowed_actions: ['refresh'], authoritative: false };
    error(c.reason.message, 'state_read');
  }
  if (m.status === 'fulfilled') {
    messages = m.value.messages;
    human = m.value.human_service;
  }
  else
    if (m.reason.status !== 409)
      error(m.reason.message, 'state_read');
  const current = card, attempt = pending();
  if (s.status === 'fulfilled' && c.status === 'fulfilled' && m.status === 'fulfilled' && current.state === 'PENDING_REVIEW' && current.authoritative && ['submission_transport', 'state_read'].includes($('global-error').dataset.kind)) error('');
  if (attempt && ((current.state === 'PENDING_REVIEW' && current.authoritative && current.confirmation?.wait_id === attempt.wait_id) || (['rejected', 'cancelled', 'invalidated'].includes(current.confirmation?.status) && current.confirmation.wait_id === attempt.wait_id)))
    pending(null);
  const users = messages.filter(row => row.role === 'user');
  if (outgoing && users.length > outgoingBefore && users.at(-1)?.content === outgoing)
    outgoing = null;
  loading = false;
  clearTimeout(expiryTimer);
  if (current.preview && Date.parse(current.preview.expires_at) > Date.now())
    expiryTimer = setTimeout(() => refresh().catch(e => error(e.message)), Date.parse(current.preview.expires_at) - Date.now() + 100);
  const contextOid = snapshot?.draft.order_id || selected;
  if (contextOid && selectedOrder?.order_id !== contextOid)
    try {
      const value = await api(endpoint('/orders/' + encodeURIComponent(contextOid)));
      if (version !== refreshVersion)
        return;
      selectedOrder = value;
      selected = contextOid;
    } catch {
      selectedOrder = null;
    }
  render();
}


async function business(path, body = {}) {
  if (active() || !snapshotReadable)
    return false;
  const deciding = path === 'decision', c = effectiveCard();
  if (deciding && !c.allowed_actions.includes(body.decision))
    return false;
  if (path === 'confirmation' && !c.allowed_actions.includes('open'))
    return false;
  const request_key = crypto.randomUUID();
  if (deciding)
    pending({ wait_id: body.wait_id, request_key });
  posting = true;
  error('');
  render();
  let accepted = false;
  try {
    await api(endpoint('/' + path), { ...body, request_key });
    accepted = true;
  } catch (e) {
    if (e.status >= 400 && e.status < 500) {
      if (deciding)
        pending(null);
      if (path === 'messages')
        outgoing = null;
    }
    error(e.status >= 400 && e.status < 500 ? '请求未受理：' + e.message : 'HTTP回执未核实，请只读查询；不会自动重发。', deciding && !(e.status >= 400 && e.status < 500) ? 'submission_transport' : 'request');
  } finally {
    posting = false;
    await refresh();
  }
  return accepted;
}


async function send(text) {
  if (active() || !text.trim())
    return false;
  outgoing = text;
  outgoingBefore = messages.filter(m => m.role === 'user').length;
  render();
  return business('messages', { text, read_only: effectiveCard().state === 'UNKNOWN' });
}


document.querySelector('.logo').replaceChildren(icon('chat'));
document.querySelector('.avatar').replaceChildren(icon('headset'));
$('send').replaceChildren(icon('send'));
$('chat').addEventListener('scroll', updateJumpLatest, { passive: true });
$('chat').addEventListener('toggle', updateJumpLatest, true);
new ResizeObserver(updateJumpLatest).observe($('chat'));
$('jump-latest').onclick = () => {
  $('chat').scrollTop = $('chat').scrollHeight;
  updateJumpLatest();
};


$('compose').onsubmit = async e => {
  e.preventDefault();
  const text = $('message').value.trim();
  if (!text)
    return;
  if (await send(text))
    if ($('message').value.trim() === text)
      $('message').value = '';
};
let composing = false;
$('message').oncompositionstart = () => composing = true;
$('message').oncompositionend = () => composing = false;
$('message').onkeydown = e => {
  if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.metaKey && !e.altKey && !composing && !e.isComposing && e.keyCode !== 229) {
    e.preventDefault();
    if (!e.repeat && !$('send').disabled)
      $('compose').requestSubmit();
  }
};


for (const [name, text] of [['查订单', '帮我查一下最近的订单，有多个订单请先展示候选。'], ['商品咨询', '店里有哪些商品？请按实际商品资料介绍。'], ['查物流', '我想查询物流，请先让我选择订单。'], ['申请退货', '我想申请退货，请先帮我查订单并让我选择。'], ['退货政策', '无理由退货的期限、运费和商品条件是什么？请依据政策说明。']])
  $('quick').append(btn(name, () => {
    $('message').value = text;
    $('message').focus();
  }, ''));


$('nav-orders').onclick = () => allOrders();
$('human').onclick = humanService;
$('help').onclick = () => {
  $('message').value = '请说明退货政策和当前系统的操作范围。';
  $('message').focus();
};
$('close-dialog').onclick = closeDialog;
$('close-context').onclick = () => $('context-drawer').close();
$('mobile-service').onclick = () => {
  $('drawer-content').append($('service-content'));
  $('context-drawer').showModal();
};
$('context-drawer').addEventListener('close', () => {
  if (!matchMedia('(max-width:900px)').matches)
    $('desktop-context').append($('service-content'));
});
matchMedia('(max-width:900px)').addEventListener('change', e => {
  if (!e.matches) {
    if ($('context-drawer').open)
      $('context-drawer').close();
    $('desktop-context').append($('service-content'));
  }
});
$('resume').onclick = () => business('resume');


(async () => {
  try {
    const b = await api('/api/bootstrap');
    csrf = b.csrf;
    sid = b.session_id;
    $('runtime-mode').textContent = b.runtime?.label || '运行模式待核实';
    $('runtime-notice').textContent = b.runtime?.notice || '无法核实模型模式';
    if (!sid)
      sid = (await api('/api/sessions', {})).session_id;
    selected = sessionStorage.getItem('v3-shop-order-' + sid);
    await refresh();
    await connect();
  } catch (e) {
    loading = false;
    error(e.message);
    controls();
  }
})();

export function el(tag, cls = '', text) {
  const n = document.createElement(tag);
  if (cls)
    n.className = cls;
  if (text !== undefined)
    n.textContent = String(text);
  return n;
}


export function btn(text, handler, cls = 'outline') {
  const n = el('button', cls, text);
  n.type = 'button';
  n.onclick = handler;
  return n;
}


export function dots() {
  const n = el('span', 'waiting-dots');
  n.setAttribute('aria-hidden', 'true');
  for (let i = 0;i < 3;i++)
    n.append(el('i'));
  return n;
}


export function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('aria-hidden', 'true');
  const paths = { chat: 'M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3a2 2 0 0 1-2-2V7a3 3 0 0 1 3-3Z M7 9h10 M7 13h6', headset: 'M4 14v-3a8 8 0 0 1 16 0v3 M4 12H3v7h4v-7H4 M20 12h1v7h-4v-7h3 M20 19v2h-5', power: 'M13 2 4 14h7l-1 8 10-13h-7Z', send: 'm3 3 18 9-18 9 4-9-4-9Z M7 12h14', up: 'M8 10v11H3V10h5Zm0 0 5-8c2 0 2 3 1 7h6c2 0 1 3 1 4l-2 7H8' };
  const path = document.createElementNS(svg.namespaceURI, 'path');
  paths.box = 'm3 7 9-4 9 4v10l-9 4-9-4V7Zm0 0 9 4 9-4M12 11v10M7 5l10 4';
  path.setAttribute('d', paths[name === 'down' ? 'up' : name] || paths.chat);
  if (name === 'down')
    path.setAttribute('transform', 'rotate(180 12 12)');
  svg.append(path);
  return svg;
}


export function botIcon() {
  const n = el('span', 'bot-icon');
  n.append(icon('chat'));
  return n;
}


export function thumb(item) {
  const name = item.product_name || item.name || '';
  const kind = /耳机/.test(name) ? 'headset' : /充电/.test(name) ? 'power' : 'box';
  const n = el('div', 'prod-thumb ' + (kind === 'headset' ? 'music' : 'charger'));
  n.append(icon(kind));
  n.setAttribute('role', 'img');
  n.setAttribute('aria-label', '商品类别示意图，非实拍');
  n.title = '类别示意图，非商品实拍';
  return n;
}


export const statuses = { pending: '待处理', shipped: '已发货', delivered: '已签收', cancelled: '已取消', processing: '处理中' };


export function badge(status) {
  return el('span', 'order-state ' + status, statuses[status] || status || '状态未知');
}


export function facts(pairs) {
  const dl = el('dl', 'facts');
  for (const [k, v] of pairs)
    dl.append(el('dt', '', k), el('dd', '', v ?? '待补充'));
  return dl;
}


export function inline(target, text) {
  const pattern = /(\*\*([^*\n]+)\*\*|`([^`\n]+)`|\[([^\]\n]+)\]\(([^\s)]+)\))/g;
  let at = 0, m;
  while ((m = pattern.exec(text))) {
    target.append(document.createTextNode(text.slice(at, m.index)));
    if (m[2])
      target.append(el('strong', '', m[2]));
    else
      if (m[3])
        target.append(el('code', '', m[3]));
      else
        if (/^https?:\/\//i.test(m[5])) {
          const a = el('a', '', m[4]);
          a.href = m[5];
          a.rel = 'noopener noreferrer';
          a.target = '_blank';
          target.append(a);
        }
        else
          target.append(document.createTextNode(m[0]));
    at = pattern.lastIndex;
  }
  target.append(document.createTextNode(text.slice(at)));
}

// Same restricted DOM-only Markdown grammar as the original V3 UI; no innerHTML.

export function markdown(target, text, collapseOrders = true) {
  const sections = String(text).split(/\n\s*\n/);
  for (const section of sections) {
    const lines = section.split('\n');
    const ids = new Set(section.match(/O\d{5,}/g) || []);
    const isOrderTable = lines.some(l => l.includes('|') && l.includes('订单'));
    if (collapseOrders && (isOrderTable || (ids.size > 3 && lines.some(l => /^\s*[-·*|\d]/.test(l))))) {
      const details = el('details', 'source-answer');
      details.append(el('summary', '', '订单文字明细（展开原文）'));
      const body = el('div', 'source-body');
      renderLines(body, lines);
      details.append(body);
      target.append(details);
    }
    else
      renderLines(target, lines);
  }
}


function renderLines(target, lines) {
  let i = 0;
  const cells = l => l.trim().replace(/^\||\|$/g, '').split('|').map(s => s.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) {
      i++;
      continue;
    }
    if (line.startsWith('```')) {
      const pre = el('pre'), code = el('code');
      const body = [];
      i++;
      while (i < lines.length && !lines[i].startsWith('```'))
        body.push(lines[i++]);
      code.textContent = body.join('\n');
      pre.append(code);
      target.append(pre);
      i++;
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && cells(lines[i + 1]).every(c => /^:?-{3,}:?$/.test(c))) {
      const wrap = el('div', 'markdown-table'), table = el('table'), head = el('tr');
      for (const cell of cells(line)) {
        const th = el('th');
        inline(th, cell);
        head.append(th);
      }
      table.append(head);
      i += 2;
      while (i < lines.length && lines[i].includes('|')) {
        const row = el('tr');
        for (const cell of cells(lines[i++])) {
          const td = el('td');
          inline(td, cell);
          row.append(td);
        }
        table.append(row);
      }
      wrap.append(table);
      target.append(wrap);
      continue;
    }
    const title = line.match(/^#{1,6}\s+(.+)/);
    const p = el(title ? 'h3' : 'p');
    inline(p, title ? title[1] : line);
    target.append(p);
    i++;
  }
}


export function orderCard(order, actions, compact = false) {
  const node = el(compact ? 'details' : 'article', 'order-card' + (compact ? ' compact' : ''));
  node.dataset.orderId = order.order_id;
  const line = el('div', 'order-line');
  const first = order.items?.[0] || {};
  const body = el('div', 'order-copy');
  body.append(el('strong', 'prod-name', first.product_name || '订单商品'), el('p', 'order-meta', `订单 ${order.order_id} · ${order.created_at || '日期待查询'}`));
  if (order.items?.length > 1)
    body.append(el('p', 'order-meta', `以及另外 ${order.items.length - 1} 件商品`));
  line.append(thumb(first), body, badge(order.status));
  if (compact) {
    const summary = el('summary');
    summary.append(line);
    node.append(summary);
  }
  else
    node.append(line);
  const buttons = el('div', 'order-buttons');
  for (const [text, fn] of [['查看详情', actions.detail], ['查物流', actions.logistics], ['申请售后', actions.afterSale]]) {
    const unsupported = text === '申请售后' && order.items?.length && order.items.every(i => i.policy_scope?.return === false);
    const consultation = unsupported && order.items.every(i => i.policy_scope?.consultation === true);
    const button = btn(unsupported ? consultation ? '仅咨询，暂不支持办理' : '暂无适用售后政策' : text, () => fn(order.order_id), text === '申请售后' ? 'order-button main-action' : 'order-button');
    button.disabled = Boolean(unsupported);
    buttons.append(button);
  }
  node.append(buttons);
  return node;
}


export function logisticsCard(value) {
  const node = el('section', 'logistics-card');
  node.append(el('h3', '', `物流 · ${value.order_id || ''}`));
  if (!value.found) {
    node.append(el('p', 'muted', value.reason || '暂无物流轨迹'));
    return node;
  }
  node.append(el('p', 'muted', value.carrier || '物流公司未提供'));
  const list = el('ol', 'timeline');
  for (const step of [...(value.trace || [])].reverse()) {
    const item = el('li');
    item.append(el('strong', '', step.text || step.status || step.description || '物流更新'), el('p', 'order-meta', step.time || step.at || ''));
    list.append(item);
  }
  node.append(list);
  return node;
}


export function productGrid(products) {
  const grid = el('div', 'product-grid');
  for (const p of products) {
    const card = el('article', 'product-card');
    card.append(thumb(p), el('strong', '', p.name || p.product_name || p.product_id), el('p', 'price', p.current_price == null ? '价格待查询' : `¥${p.current_price}`));
    if (p.attributes)
      card.append(el('p', 'order-meta', Array.isArray(p.attributes) ? p.attributes.join(' · ') : p.attributes));
    if (p.policy_notice)
      card.append(el('p', 'muted', p.policy_notice));
    grid.append(card);
  }
  return grid;
}

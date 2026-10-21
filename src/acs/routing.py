"""Finite workflow routing; uncertainty keeps the original tool-calling path."""
from __future__ import annotations

import re

KNOWLEDGE = {'search_knowledge', 'get_knowledge_documents'}
SCOPES = {
    'presales': KNOWLEDGE | {'get_product', 'list_products'},
    'orders': {'get_order', 'list_orders'},
    'logistics': KNOWLEDGE | {'get_order', 'list_orders', 'get_logistics'},
    'returns': KNOWLEDGE | {'get_order', 'list_orders', 'get_return_task',
                           'update_return_task', 'cancel_return_task', 'preview_return',
                           'get_return_requests'},
    'price_policy': KNOWLEDGE | {'get_order', 'list_orders', 'get_price_history'},
}


def route_request(text: str, available: list[str], has_task: bool) -> dict:
    """Tool scope is a workflow hint, never a replacement for business authorization."""
    candidates = []
    if any(w in text for w in ('物流', '快递', '发货', '签收', '配送')):
        candidates.append('logistics')
    if any(w in text for w in ('订单', 'SKU', '颜色')) and '颜色限制' not in text:
        candidates.append('orders')
    if any(w in text for w in ('退货', '退款', '售后', '草稿', '审核', '申请')) or re.search(
        r'(?:我想|我要|改成|更正).*退', text
    ):
        candidates.append('returns')
    if any(w in text for w in ('规格', '参数', '推荐', '购买', '型号')) or (
        '商品' in text and '商品明细' not in text
    ):
        candidates.append('presales')
    if '价保' in text or '价格保护' in text:
        candidates.append('price_policy')
    ambiguous = any(w in text for w in ('不是', '不要', '不想', '如果', '假如', '他说',
                                        '“', '”', '"', '改地址', '取消订单', '转人工'))
    # A specific after-sales correction remains actionable; unexplained "继续" stays open.
    conditional_lookup = ('物流' in text and '退货' in text
                          and '如果已经签收' in text and '看看' in text)
    fallback = (ambiguous and not conditional_lookup) or not candidates
    intent = 'unknown' if fallback else candidates[0] if len(candidates) == 1 else 'mixed'
    allowed = set(available) if fallback else set().union(*(SCOPES[c] for c in candidates))
    # A bare policy question must not be stranded by missing the narrower business vocabulary.
    if '政策' in text or '规则' in text:
        allowed |= KNOWLEDGE
    return {'version': 1, 'intent': intent, 'candidates': candidates,
            'confidence': 'fallback' if fallback else 'high',
            'allowed_tools': [n for n in available if n in allowed],
            'basis': 'uncertain_expression' if fallback else 'explicit_business_terms',
            'existing_task': has_task, 'task_action': 'preserve_until_authorized_tool'}

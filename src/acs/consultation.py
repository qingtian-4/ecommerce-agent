"""Bounded, source-rendered consultation after a protected local operation.

Model prose is never split or promoted to evidence. Only explicitly requested local
product parameters and return-policy text from actual current-turn reads are rendered.
"""
from __future__ import annotations

import html
import json
import re

from .knowledge_context import load_source_documents


def consultation_reply(message, trace, settings):
    if not any(e.type == 'write_verification' or (e.type == 'tool_call' and e.label in {
            'update_return_task','cancel_return_task','preview_return'}) for e in trace):
        return None
    policy_names = ('退货政策','退换货政策','退货期限','退货运费','退货规则')
    policy_requested = any(s in message for s in policy_names)
    policy_terms = '(?:'+'|'.join(policy_names)+')'
    policy_clauses = [s.strip() for s in re.split(r'[，,。；;！？!?]',message)
                      if any(t in s for t in policy_names)]
    explicit_policy = bool(policy_clauses) and all(re.fullmatch(
        r'(?:请)?(?:同时)?说明'+policy_terms+r'(?:和'+policy_terms+r')*',s)
        for s in policy_clauses)
    parameters_requested = '参数' in message or '规格' in message
    # A deliberately small positive presentation protocol, not general intent parsing.
    # Selective, negated or quoted clauses are left unresolved rather than treating
    # every mentioned ID as a requested product.
    clauses = [s.strip() for s in re.split(r'[，,。；;！？!?]', message)
               if '参数' in s or '规格' in s]
    catalog_id = r'[A-Za-z][A-Za-z0-9_-]*'
    entry = catalog_id+r'(?:的)?(?:参数|规格)?'
    product_list = entry+r'(?:和'+entry+r')*'
    explicit_products = bool(clauses) and all(re.fullmatch(
        r'(?:请)?(?:同时)?介绍'+product_list, clause) for clause in clauses)
    requested = set(re.findall(catalog_id, ' '.join(clauses)))
    if not policy_requested and not parameters_requested:
        return None
    reads, pending = {}, None
    for event in trace:
        if event.type == 'tool_call':
            try:
                args = json.loads(event.detail.get('arguments','{}'))
            except (ValueError,TypeError):
                args = None
            pending = (event.label,args)
            if isinstance(args,dict):
                reads[(event.label,json.dumps(args,sort_keys=True))] = (args,None)
        elif pending and event.label == pending[0] and event.type == 'tool_result':
            if isinstance(pending[1],dict):
                key = (event.label,json.dumps(pending[1],sort_keys=True))
                reads[key] = (pending[1],event.detail.get('result'))
            pending = None
        elif pending and event.label == pending[0] and event.type == 'error':
            pending = None
    blocks, sources = [], []
    policy_ok = not policy_requested
    products = []
    policy_contents = set()
    for (name,_),(args,payload) in reads.items():
        if not isinstance(payload,dict) or any(k in payload for k in ('error','error_type')):
            continue
        if explicit_policy and name in {'get_knowledge_documents','search_knowledge'}:
            documents = payload.get('documents')
            if not isinstance(documents,list) or not all(isinstance(d,dict) for d in documents):
                continue
            try:
                trusted = next(d for d in load_source_documents(settings)
                               if d['document_id']=='return_policy')
            except (OSError,ValueError,StopIteration):
                continue
            for doc in documents:
                if doc.get('document_id') != trusted['document_id']:
                    continue
                content = doc.get('content')
                whole = (name == 'get_knowledge_documents'
                         and payload.get('complete_documents') is True
                         and doc.get('hash') == trusted['hash'] and content == trusted['content']
                         and 'return_policy' in args.get('document_ids',[]))
                # A retrieved fragment can support only its own text, not the whole policy.
                fragment = (name == 'search_knowledge' and payload.get('found') is True
                            and isinstance(content,str) and bool(content.strip())
                            and content in trusted['content'])
                if not (whole or fragment) or content in policy_contents:
                    continue
                policy_contents.add(content)
                quoted = '\n'.join('> '+html.escape(s) for s in content.splitlines())
                blocks.append(('政策依据（完整原文及适用条件，不代表当前订单已办理）：\n'
                               if whole else '政策片段（条件可能缺失，咨询尚未核实）：\n')+quoted)
                sources.append({'tool':name,'document_id':'return_policy',
                                'hash':trusted['hash'],'complete_document':whole})
                policy_ok = policy_ok or whole
        if parameters_requested and explicit_products and name == 'get_product':
            pid = args.get('product_id')
            if (not isinstance(pid,str) or pid not in requested or not re.search(
                    r'(?<![A-Za-z0-9_])'+re.escape(pid)+r'(?![A-Za-z0-9_])',message)
                    or payload.get('found') is not True or payload.get('product_id') != pid
                    or any(payload.get(k,False) is not False
                           for k in ('partial','truncated','has_more'))
                    or any(not isinstance(payload.get(k),str) or not payload[k].strip()
                           for k in ('name','brand','category'))
                    or not isinstance(payload.get('attributes'),list) or not payload['attributes']
                    or not all(isinstance(a,str) for a in payload['attributes'])):
                continue
            facts = {k:payload[k] for k in ('product_id','name','brand','category','attributes')}
            products.append((message.index(pid),pid,facts))
    for _,pid,facts in sorted(products):
        blocks.append('商品参数依据（SQL get_product '+html.escape(pid)+'）：\n'+
                      html.escape(json.dumps(facts,ensure_ascii=False)))
        sources.append({'tool':'get_product','product_id':pid})
    missing = requested - {pid for _,pid,_ in products}
    parameters_ok = not parameters_requested or bool(products) and not missing
    if not policy_ok or not parameters_ok:
        blocks.append('部分咨询尚未核实：没有与本轮请求匹配的可信资料'
                      +('（'+','.join(sorted(missing))+'）' if missing else '')
                      +'；未用模型原文补全。')
    return '\n\n咨询依据（与操作回执分开）：\n'+'\n\n'.join(blocks), {
        'verified':policy_ok and parameters_ok,
        'scope':'consultation_only','sources':sources,
        'note':'原模型余文未作为事实放行；不改变业务状态或操作回执。',
    }

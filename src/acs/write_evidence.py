"""Receipt-bound write replies. Text identifies a claim, never proves execution or authority."""
from __future__ import annotations

import re

# Finite diagnostic grammar, not a general semantic reviewer. Quoted/hypothetical prose is
# excluded; the actual decision below requires a registry-verified structured receipt.
CLAIMS = {
    'address': r'(?:address.{0,35}(?:has been|is now) (?:updated|changed)|'
               r"(?:I've|I have) (?:updated|changed).{0,25}address|已(?:经)?修改.{0,12}地址)",
    'cancel': r'(?:已(?:经)?取消|(?:have|has been|is now) cancel(?:led|ed))',
    'draft': r'(?:草稿已(?:保存|记录|更新)|已(?:经)?(?:保存|记录|更新).{0,12}草稿)',
    'application': r'(?:已(?:经)?提交.{0,12}申请|申请已(?:创建|提交))',
    'refund': r'(?:退款已(?:到账|完成)|已(?:经)?退款|refund.{0,30}(?:issued|credited|completed))',
    'modify': r'(?:order.{0,30}has been (?:updated|modified)|已(?:经)?修改.{0,10}订单)',
}
NOTICE = ('尚未核实该操作完成：没有与目标业务状态匹配的成功回执。'
          '请查询实际状态；本次不会自动重复写入。')


def completion_claims(text):
    # Recognition is deliberately bounded; do not treat quoted examples as user authorization.
    plain = re.sub(r'“[^”]*”|‘[^’]*’|"[^"\n]*"', '', text)
    found = {kind for kind, pattern in CLAIMS.items() if re.search(pattern, plain, re.I)}
    if 'address' in found:
        found.discard('modify')
    return found


def verify(registry, name, arguments, payload, error, user_id):
    try:
        return registry.verify_mutation(name, arguments, payload, error, user_id)
    except Exception as exc:
        return {'verified':False,'outcome':'readback_failed','tool':name,
                'error_type':type(exc).__name__,'claims':[]}


def guarded_answer(text, receipts):
    claims = completion_claims(text)
    # DSML is content, never a Function Call, even if it visually resembles an SDK envelope.
    pseudo_call = 'DSML' in text or '<tool_call' in text
    latest = {r.get('scope', r.get('target', r.get('tool', 'unknown'))):r
              for r in receipts}
    verified = [r for r in latest.values() if r['verified']]
    if not receipts and not pseudo_call and not claims:
        return text, None
    # An operation kind alone cannot authorize arbitrary targets, fields or old revisions.
    # Render only the current structured receipt summaries, never the model's completion prose.
    summaries = [r['summary'] for r in verified if r.get('summary')]
    rendered = '\n'.join(summaries)
    accepted = (bool(summaries) and not pseudo_call
                and all(r['verified'] for r in latest.values())
                and claims <= {c for r in verified for c in r['claims']})
    if not accepted:
        rendered = ('部分完成声明尚未核实；仅能确认下列实际回执：\n'+rendered
                    if rendered else NOTICE)
    explanations = [r['explanation'] for r in latest.values()
                    if not r['verified'] and r.get('explanation')]
    if explanations:
        rendered += '\n'+'\n'.join(explanations)
    detail = {'verified': accepted,
              'outcome':'receipt_rendered' if summaries else 'unsupported_completion',
              'claims':sorted(claims),'receipts':list(latest.values()),'model_answer':text}
    return rendered, detail

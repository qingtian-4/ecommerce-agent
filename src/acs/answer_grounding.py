"""Scoped, deterministic presentation checks. SQL receipts remain authoritative.

This is not a general semantic verifier: unknown paraphrases are not certified.
Raw model messages/Graph history are never rewritten by this projection.
"""

from __future__ import annotations

import json
import re
from contextlib import closing
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field

from .cs_tools import catalog_progress


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: dict = Field(default_factory=dict)
    previews: list[dict] = Field(default_factory=list)
    valid_preview: dict | None = None
    authoritative_submission: dict | None = None
    submission_unknown: bool = False
    created_preview_id: str | None = None
    checked_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())
    known_orders: list[str] = Field(default_factory=list)
    known_products: list[str] = Field(default_factory=list)
    catalog_queries: list[dict] = Field(default_factory=list)


class Finding(BaseModel):
    predicate: str
    action: str


class Review(BaseModel):
    text: str
    findings: list[Finding]
    coverage: str = "scoped_rules_only"


def collect_evidence(gateway, root, sid, uid, tid):
    from .local_submission import read_submission_receipt

    # Reuse owner checks, ReturnService validity and M6 receipt validation.
    with closing(gateway.store._connect()) as conn:
        gateway.store._require_owner(conn, sid, uid)
        task = gateway.store._task(conn, sid) or {}
        previews = [
            dict(r)
            for r in conn.execute(
                "SELECT preview_id,revision,expires_at FROM return_previews "
                "WHERE session_id=? ORDER BY created_at,rowid",
                (sid,),
            )
        ]
        has_waits = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='local_confirmation_waits'"
        ).fetchone()
        waits = (
            [
                dict(r)
                for r in conn.execute(
                    "SELECT wait_id,status FROM local_confirmation_waits WHERE session_id=?", (sid,)
                )
            ]
            if has_waits
            else []
        )
    # A preview created this turn is not a prior historical preview even if it
    # expires or becomes ineligible before presentation.
    with closing(gateway.store._connect()) as conn:
        catalogs = catalog_progress([dict(r) for r in conn.execute(
            "SELECT name,arguments,result,error FROM local_receipts "
            "WHERE session_id=? AND turn_id=? ORDER BY rowid", (sid, tid))])
        generated = [
            json.loads(r[0])
            for r in conn.execute(
                "SELECT result FROM local_receipts WHERE session_id=? AND turn_id=? "
                "AND name='preview_return' AND error IS NULL ORDER BY rowid",
                (sid, tid),
            )
        ]
    created = next((p.get("preview_id") for p in reversed(generated) if p.get("ready")), None)
    verified, unknown = None, False
    for wait in waits:
        if wait["status"] in {"accepted", "issued"}:
            try:
                receipt = read_submission_receipt(root, sid, uid, wait["wait_id"])
                if receipt and receipt["request"]["request_id"] == task.get("request_id"):
                    verified = receipt["request"]
                elif wait["status"] == "issued":
                    unknown = True
            except (ValueError, RuntimeError, KeyError, TypeError):
                unknown = True
    return Evidence(
        task=task,
        previews=previews,
        valid_preview=gateway.service.latest_preview(sid, uid),
        authoritative_submission=verified,
        submission_unknown=unknown,
        created_preview_id=created,
        known_orders=[o["order_id"] for o in gateway.registry.db.list_orders(uid)],
        known_products=[p["product_id"] for p in gateway.registry.db.list_products()],
        catalog_queries=catalogs,
    )


def _qualified(text, start, end):
    """Negation, hypotheticals and reported speech do not assert an operation."""
    prefix = re.split(r"[，,]|但是|然而|不过", text[:start])[-1]
    suffix = re.split(r"[，,]|但是|然而|不过", text[end:])[0]
    if re.search(r"用户(?:说|提到|问)|历史回答(?:说|声称)", prefix + text[start:end]):
        return True
    if re.search(r"[？?]|是否真实|是不是|吗\s*$", suffix):
        return True
    if re.search(
        r"如果|假设|若|是否|并非|并未|没有|尚未|不能|不要|未曾|不是|"
        r"用户(?:说|提到|问)|历史回答(?:说|声称)",
        prefix,
    ):
        return True
    for left, right in [("“", "”"), ("「", "」"), ('"', '"'), ("`", "`")]:
        for span in re.finditer(re.escape(left) + ".*?" + re.escape(right), text, re.S):
            if span.start() <= start and end <= span.end():
                return True
    return False


def _units(answer):
    """Sentence boundaries inside a quotation do not end its protection scope."""
    pairs = {"“": "”", "「": "」", '"': '"', "`": "`"}
    end_quote, start = None, 0
    for index, char in enumerate(answer):
        if end_quote:
            if char == end_quote:
                end_quote = None
        elif char in pairs:
            end_quote = pairs[char]
        elif char in "。！？；\n":
            yield answer[start:index + 1]
            start = index + 1
    if start < len(answer):
        yield answer[start:]


def review_answer(answer, evidence):
    unfinished = [q for q in evidence.catalog_queries if not q["complete"]]
    if unfinished:
        counts = "；".join(f"已核验{q['seen_count']}/{q['total_count']}个候选" for q in unfinished)
        return Review(text=f"目录查询尚未完成：{counts}。当前页面不能代表完整目录。"
                      "请继续分页查询或缩小商品条件；不能据此声称已查全或没有其他匹配商品。",
                      findings=[Finding(predicate="catalog_query_incomplete", action="withheld")])
    findings = []

    def change(unit, pattern, predicate, replacement):
        def replace(match):
            if _qualified(unit, match.start(), match.end()):
                return match.group()
            findings.append(Finding(predicate=predicate, action="corrected"))
            return replacement(match) if callable(replacement) else replacement

        return re.sub(pattern, replace, unit)

    output = []
    # Keep clauses, consultations, Markdown and tool/native messages intact.
    for unit in _units(answer):
        older = [p for p in evidence.previews if p["preview_id"] != evidence.created_preview_id]
        if not older:
            unit = change(
                unit,
                r"(?:此前|之前|旧|原来|历史)[^。！？；\n（）]*?预览"
                r"[^。！？；\n（）]*?(?:已经|已)(?:失效|过期|作废)",
                "historical_preview_missing",
                "此前没有可核实的历史预览",
            )
        elif (
            len(older) == 1
            and evidence.valid_preview
            and older[0]["preview_id"] == evidence.valid_preview["preview_id"]
        ):
            unit = change(
                unit,
                r"(?:此前|之前|旧|原来|历史)[^。！？；\n（）]*?预览"
                r"[^。！？；\n（）]*?(?:已经|已)(?:失效|过期|作废)",
                "historical_preview_still_valid",
                "此前的预览目前仍有效，请以当前卡片为准",
            )
        elif all(
            datetime.fromisoformat(p["expires_at"]) > datetime.fromisoformat(evidence.checked_at)
            for p in older
        ):
            unit = change(
                unit,
                r"(?:此前|之前|旧|历史)[^。！？；\n（）]*?预览"
                r"[^。！？；\n（）]*?(?:已经|已)过期",
                "historical_expiry_not_supported",
                "历史预览的过期时间尚未到；是否可用请以当前卡片核验为准",
            )
        if not evidence.authoritative_submission:
            unit = change(
                unit,
                r"(?:已经|已)(?:为您|帮您|为你|帮你)?"
                r"(?:成功)?(?:提交|创建|生成)(?:了)?(?:一份)?(?:模拟)?"
                r"(?:退货|售后)申请",
                "submission_not_verified",
                "尚未核实申请提交；请以页面确认后的权威回执为准",
            )
        unit = change(
            unit,
            r"(?:退款(?:已经|已)完成|(?:我)?(?:已经|已)(?:为您|帮您)?"
            r"(?:完成审核|办理换货|完成退款|执行退款))",
            "unsupported_capability",
            "本演示不具备审核、退款或换货办理能力",
        )
        unit = change(
            unit,
            r"我(?:可以|能够|能)(?:直接)?(?:帮您|为您|替您)?"
            r"(?:直接提交(?:退货|售后)申请|提交(?:退货|售后)申请|退款|审核|办理换货)",
            "unsupported_capability",
            "聊天不能代为提交、审核、退款或换货；提交须由页面明确确认",
        )
        if not evidence.task or evidence.task.get("status") != "draft":
            unit = change(
                unit,
                r"(?:已经|已)(?:为您|帮您)?(?:成功)?保存(?:了)?(?:退货)?草稿",
                "draft_not_verified",
                "当前没有可核实的待处理退货草稿",
            )
        if not evidence.valid_preview:
            unit = change(
                unit,
                r"(?:已经|已)(?:为您|帮您)?(?:成功)?生成(?:了)?"
                r"(?:最新|新的|有效)?(?:退货)?预览",
                "preview_not_verified",
                "当前没有可核实的有效退货预览",
            )
        if evidence.valid_preview and re.search(r"预览有效期|预览有效至|预览过期时间", unit):
            expires = datetime.fromisoformat(evidence.valid_preview["expires_at"])
            expires = expires.astimezone(ZoneInfo("Asia/Shanghai"))
            unit = change(
                unit,
                r"(?:预览有效期|预览有效至|预览过期时间)(?:[：:]\s*(?:至)?\s*|(?:至|为)\s*)"
                r"(?:\d{4}[-/]\d{1,2}[-/]\d{1,2}[ T])?\d{1,2}:\d{2}(?::\d{2})?"
                r"(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?(?:左右)?(?:（[^）]*）)?",
                "expiry_timezone_projection",
                f"预览有效至：{expires:%Y-%m-%d %H:%M:%S}（北京时间 UTC+08:00）",
            )
        if "订单号" in unit:
            for match in list(re.finditer(r"订单号[：:]\s*(O\d+)", unit)):
                if match[1] not in evidence.known_orders:
                    unit = change(
                        unit,
                        re.escape(match.group()),
                        "order_not_verified",
                        "订单号：未核实，请查询本人订单",
                    )
        if re.search(r"已经全部处理妥当|已经处理妥当|都办好了|全部搞定", unit):
            findings.append(
                Finding(predicate="business_paraphrase_unverified", action="unverified")
            )
        output.append(unit)
    text = "".join(output)
    # Only draft/preview labelled quantity fields, never product stock/specification text.
    scope, seen_fields = False, False
    lines = []
    for line in text.splitlines(keepends=True):
        plain = re.sub(r"^[#*\s]+|[*\s]+$", "", line)
        header = re.fullmatch(
            r"(?:当前|最新)?(?:退货)?(?:草稿|预览)(?:状态|信息|详情|内容)?"
            r"(?:[（(:：].*)?",
            plain,
        )
        field = re.match(
            r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:订单号|商品|商品编号|商品明细|"
            r"明细|数量|原因|退货原因|商品状况|二次销售|状态|版本|政策)[：:]",
            line,
        )
        if header and not _qualified(line, 0, len(line)):
            scope, seen_fields = True, False
        elif not line.strip():
            if seen_fields:
                scope = False
        elif scope and field:
            seen_fields = True
        else:
            scope = False
        if scope and evidence.task.get("order_id"):
            order = re.search(r"订单号[：:]\s*(O\d+)", line)
            if (
                order
                and not _qualified(line, order.start(), order.end())
                and order[1] != evidence.task["order_id"]
            ):
                line = line[: order.start(1)] + evidence.task["order_id"] + line[order.end(1) :]
                findings.append(Finding(predicate="draft_order_mismatch", action="corrected"))
        if scope and evidence.task.get("quantity") is not None:
            match = re.search(r"数量[：:]\s*(\d+)", line)
            if (
                match
                and not _qualified(line, match.start(), match.end())
                and int(match[1]) != evidence.task["quantity"]
            ):
                line = (
                    line[: match.start(1)] + str(evidence.task["quantity"]) + line[match.end(1) :]
                )
                findings.append(Finding(predicate="quantity_mismatch", action="corrected"))
        lines.append(line)
    # UTC read time is evidence metadata, not an inferred business timestamp.
    return Review(text="".join(lines), findings=findings)

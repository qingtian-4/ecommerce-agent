"""Evidence contract: local corrections, no broad response replacement."""

import pytest

from acs.answer_grounding import Evidence, review_answer


def evidence(**overrides):
    return Evidence(
        task={"status": "draft", "revision": 3, "order_id": "O10024", "item_id": 5, "quantity": 1},
        previews=[{"preview_id": "new", "revision": 3, "expires_at": "2026-10-25T04:00:00+00:00"}],
        valid_preview={"preview_id": "new", "expires_at": "2026-10-25T04:00:00+00:00"},
        known_orders=["O10024"],
        created_preview_id="new",
        known_products=["P003", "P004"],
        **overrides,
    )


def test_frozen_historical_preview_claim_is_locally_corrected():
    raw = "商品支持PD快充。已生成最新预览（此前耳机版本的预览已失效）。\n运费由买家承担。"
    result = review_answer(raw, evidence())
    assert "此前耳机版本的预览已失效" not in result.text
    assert "没有" in result.text and "历史预览" in result.text
    assert "商品支持PD快充" in result.text and "运费由买家承担" in result.text
    assert result.findings[0].predicate == "historical_preview_missing"
    assert review_answer(result.text, evidence()).text == result.text


@pytest.mark.parametrize(
    "text",
    [
        "没有旧预览，不能说此前耳机版本的预览已失效。",
        "如果旧预览已失效，请重新生成。",
        "假设之前的预览已过期，可以重新预览吗？",
        "用户说“此前耳机版本的预览已失效”，我尚未核实。",
        "历史回答声称旧预览已失效，这句话缺少依据。",
        "旧预览并未失效。商品支持65W。",
        "我没有创建售后申请。",
        "请不要提交售后申请。",
        "“已提交退货申请”只是引用，不是执行结果。",
    ],
)
def test_negative_hypothetical_and_quoted_expressions_are_preserved(text):
    assert review_answer(text, evidence()).text == text


def test_actual_previous_preview_can_be_described_as_superseded():
    e = evidence()
    e.previews.insert(
        0, {"preview_id": "old", "revision": 2, "expires_at": "2026-10-25T04:00:00+00:00"}
    )
    assert review_answer("旧预览已失效。商品功率65W。", e).text == "旧预览已失效。商品功率65W。"


def test_unverified_submission_is_never_displayed_as_success():
    result = review_answer("P003是65W。已提交退货申请。运费由买家承担。", evidence())
    assert "已提交退货申请" not in result.text and "尚未核实" in result.text
    assert "P003是65W" in result.text and "运费由买家承担" in result.text


def test_authoritative_receipt_can_support_a_submission_claim():
    e = evidence(authoritative_submission={"request_id": "R1", "status": "pending_review"})
    assert review_answer("已提交退货申请，等待审核。", e).text == "已提交退货申请，等待审核。"


@pytest.mark.parametrize("text", ["退款已完成。", "我已完成审核。", "已为您办理换货。"])
def test_unavailable_operations_do_not_become_capabilities(text):
    r = review_answer(text, evidence())
    assert r.text != text and any(x.predicate == "unsupported_capability" for x in r.findings)


def test_expiry_projection_is_explicitly_timezone_bound():
    r = review_answer("预览有效期：12:52左右。\n商品：磁吸充电宝。", evidence())
    assert "2026-10-25 12:00:00" in r.text and "UTC+08:00" in r.text
    assert "商品：磁吸充电宝" in r.text


def test_structured_fields_must_match_authoritative_business_objects():
    r = review_answer("当前退货草稿\n订单号：O99999\n数量：9\n商品咨询：65W、2C1A。", evidence())
    assert "订单号：O99999" not in r.text and "数量：9" not in r.text
    assert "65W、2C1A" in r.text


def test_unknown_natural_language_is_reported_not_invented_as_verified():
    r = review_answer("您的那件东西已经全部处理妥当。", evidence())
    assert r.text == "您的那件东西已经全部处理妥当。"
    assert r.coverage == "scoped_rules_only"


def test_only_new_preview_is_not_history_when_no_longer_currently_valid():
    e = evidence()
    e.valid_preview = None
    result = review_answer("此前耳机版本的预览已失效。", e)
    assert "此前耳机版本的预览已失效" not in result.text
    assert result.findings[0].predicate == "historical_preview_missing"


def test_draft_order_binding_is_stricter_than_same_user_ownership():
    e = evidence()
    e.known_orders.append("O10027")
    result = review_answer("当前退货草稿\n订单号：O10027\n商品咨询：其他订单号：O10027。", e)
    assert "订单号：O10024" in result.text
    assert "商品咨询：其他订单号：O10027" in result.text
    assert any(f.predicate == "draft_order_mismatch" for f in result.findings)


def test_unrelated_negative_clause_does_not_hide_an_affirmative_false_operation():
    result = review_answer("没有旧预览，已提交退货申请。", evidence())
    assert "已提交退货申请" not in result.text
    assert "没有旧预览" in result.text


@pytest.mark.parametrize(
    "text", ["我可以直接提交退货申请。", "我可以为您退款。", "我能够替您审核。"]
)
def test_model_cannot_promise_application_or_admin_capabilities(text):
    result = review_answer(text, evidence())
    assert result.text != text
    assert any(f.predicate == "unsupported_capability" for f in result.findings)


def test_preview_expiry_reason_must_be_time_supported():
    e = evidence()
    e.checked_at = "2026-10-25T03:00:00+00:00"
    e.previews.insert(
        0, {"preview_id": "old", "revision": 2, "expires_at": "2026-10-25T04:00:00+00:00"}
    )
    result = review_answer("旧预览已过期，商品功率65W。", e)
    assert "旧预览已过期" not in result.text and "65W" in result.text
    assert any(f.predicate == "historical_expiry_not_supported" for f in result.findings)


def test_business_field_scope_ends_before_another_valid_order_section():
    e = evidence()
    e.known_orders.append("O10027")
    raw = "当前退货草稿\n订单号：O10024\n数量：1\n\n其他订单信息\n订单号：O10027\n数量：1"
    assert review_answer(raw, e).text == raw


def test_real_pilot_expiry_without_colon_is_projected_without_harming_consultation():
    raw = "核验日期：2026-10-25；预览有效期至 2026-10-25 03:12。运费由买家承担。"
    result = review_answer(raw, evidence())
    assert "UTC+08:00" in result.text and "2026-10-25 12:00:00" in result.text
    assert "核验日期：2026-10-25" in result.text and "运费由买家承担" in result.text


def test_expiry_question_is_not_an_asserted_deadline():
    raw = "预览有效期为多久？商品是否支持65W？"
    assert review_answer(raw, evidence()).text == raw


def test_existing_valid_preview_cannot_be_described_as_invalid():
    e = evidence()
    e.created_preview_id = None
    result = review_answer("旧预览已失效。商品功率65W。", e)
    assert "旧预览已失效" not in result.text and "仍有效" in result.text
    assert "65W" in result.text
    assert any(f.predicate == "historical_preview_still_valid" for f in result.findings)


@pytest.mark.parametrize("raw,predicate", [
    ("已提交退货申请，您还有其他问题吗？", "submission_not_verified"),
    ("退款已完成，您还需要帮助吗？", "unsupported_capability"),
    ("用户说需要核对，已提交退货申请，您还有问题吗？", "submission_not_verified"),
    ("“已提交退货申请”只是引用，但是已提交退货申请，您还需要帮助吗？", "submission_not_verified"),
    ("没有生成预览，但已提交退货申请，您还有问题吗？", "submission_not_verified"),
])
def test_assertion_is_not_qualified_by_an_unrelated_clause(raw, predicate):
    result = review_answer(raw, evidence())
    assert result.text != raw
    assert any(f.predicate == predicate for f in result.findings)
    assert "您" in result.text


@pytest.mark.parametrize("raw", [
    "尚未提交退货申请，您还有其他问题吗？",
    "如果已提交退货申请，请勿重复提交。",
    "假设退款已完成，您会怎样查询？",
    "已提交退货申请吗，请先核对？",
    "退款已完成是否真实，仍需核实。",
    "用户说已提交退货申请，您能核对吗？",
    "“已提交退货申请，您还有其他问题吗？”只是引用。",
    "用户说“退款已完成”，这不构成真实退款凭证。",
    "没有旧预览。\n如果旧预览已失效，请刷新；我没有提交退货申请。",
])
def test_qualifiers_remain_local_and_quotes_stay_protected(raw):
    assert review_answer(raw, evidence()).text == raw


def test_quoted_paragraph_does_not_shield_the_next_assertion():
    raw = "用户说“已提交退货申请”。\n退款已完成，您还需要帮助吗？"
    result = review_answer(raw, evidence())
    assert "用户说“已提交退货申请”" in result.text
    assert any(f.predicate == "unsupported_capability" for f in result.findings)



def test_multiline_quotation_preserves_both_claims_but_not_later_assertion():
    quote = "“已提交退货申请，\n退款已完成”只是引用。"
    raw = quote + "\n已提交退货申请，您还有其他问题吗？"
    result = review_answer(raw, evidence())
    assert quote in result.text
    assert [f.predicate for f in result.findings] == ["submission_not_verified"]

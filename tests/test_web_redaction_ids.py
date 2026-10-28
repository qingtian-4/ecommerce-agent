"""A numeric substring inside an opaque SQL receipt is not a phone number."""
from acs.web_service import scrub


def test_opaque_receipt_is_preserved_and_real_phone_is_still_redacted():
    receipt = "R25c14629715446dfb0176368e2987fa8"
    assert scrub(receipt) == receipt
    assert "13800138001" not in scrub("联系电话13800138001，请回拨。")
    assert "[已隐藏]" in scrub("联系电话13800138001，请回拨。")

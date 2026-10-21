"""Seed defaults must use the same business date as live order queries."""

from datetime import UTC, date, datetime, timedelta

import pytest

from acs import database, db_seed
from acs.config import Settings


@pytest.mark.parametrize("hour", [15, 16])
@pytest.mark.parametrize("reference_date", [None, date(2026, 1, 5)])
def test_seed_and_order_query_share_business_clock(tmp_path, monkeypatch, hour, reference_date):
    # UTC hosts differ from Shanghai after 16:00 UTC, regardless of the test runner's TZ.
    instant = datetime(2026, 10, 21, hour, 30, tzinfo=UTC)

    class HostDate(date):
        @classmethod
        def today(cls):
            return instant.date()

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    monkeypatch.setattr(db_seed, "date", HostDate)
    monkeypatch.setattr(database, "datetime", FrozenDateTime)
    settings = Settings(_env_file=None, db_path=tmp_path / "business.db",
                        reference_date=reference_date)
    db_seed.build(settings.db_path, reference_date=reference_date)

    order = database.BusinessDB(settings).get_order("O10024", "USER001")
    expected = reference_date or instant.astimezone(database.ZoneInfo("Asia/Shanghai")).date()
    assert order["today"] == expected.isoformat()
    assert order["created_at"] == (expected - timedelta(days=4)).isoformat()
    assert order["delivered_days_ago"] == 2

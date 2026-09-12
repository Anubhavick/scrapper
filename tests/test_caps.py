from datetime import datetime, timezone

from leadgen.send.caps import can_send, start_of_day_utc


def test_can_send_below_cap() -> None:
    assert can_send(sent_today=10, daily_cap=50) is True


def test_cannot_send_at_or_above_cap() -> None:
    assert can_send(sent_today=50, daily_cap=50) is False
    assert can_send(sent_today=51, daily_cap=50) is False


def test_start_of_day_utc_zeroes_time_and_converts_timezone() -> None:
    # 2026-01-05 23:30 IST (UTC+5:30) is 2026-01-05 18:00 UTC.
    from datetime import timedelta, tzinfo

    class IST(tzinfo):
        def utcoffset(self, dt):
            return timedelta(hours=5, minutes=30)

        def dst(self, dt):
            return timedelta(0)

        def tzname(self, dt):
            return "IST"

    now = datetime(2026, 1, 5, 23, 30, tzinfo=IST())
    result = start_of_day_utc(now)
    assert result == datetime(2026, 1, 5, 0, 0, 0, tzinfo=timezone.utc)

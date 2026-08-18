from datetime import datetime
from zoneinfo import ZoneInfo


TZ = ZoneInfo("Asia/Shanghai")


def test_open_during_weekday_hours(business_hours):
    status = business_hours.status(datetime(2026, 8, 17, 10, 30, tzinfo=TZ))
    assert status.is_open is True
    assert "18:00" in status.message


def test_closed_after_weekday_hours(business_hours):
    status = business_hours.status(datetime(2026, 8, 17, 20, 0, tzinfo=TZ))
    assert status.is_open is False
    assert status.next_open_at == datetime(2026, 8, 18, 9, 0, tzinfo=TZ)


def test_closed_sunday_points_to_monday(business_hours):
    status = business_hours.status(datetime(2026, 8, 16, 12, 0, tzinfo=TZ))
    assert status.is_open is False
    assert "周一" in status.message
    assert "09:00" in status.message


def test_saturday_uses_special_hours(business_hours):
    status = business_hours.status(datetime(2026, 8, 15, 16, 30, tzinfo=TZ))
    assert status.is_open is True
    assert "17:00" in status.message


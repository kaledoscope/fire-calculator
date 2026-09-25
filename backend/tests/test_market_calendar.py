"""交易日历测试。

日历是「要不要重新抓数据」的判据，所以它错了不会崩，只会**静默地读旧数据**
—— 这类错误最难发现，必须靠测试钉住。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from backend.market_calendar import (
    easter_sunday,
    is_trading_day,
    last_completed_trading_day,
    nyse_holidays,
    previous_trading_day,
)

# 美东 12:00 / 22:00 对应的 UTC（EDT 期间 UTC-4，EST 期间 UTC-5）。
# 用固定时刻构造，**不依赖运行测试的机器时区**。


def et_noon(day: date) -> datetime:
    """该日美东中午（盘中）。"""
    from zoneinfo import ZoneInfo

    return datetime(day.year, day.month, day.day, 12, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(
        timezone.utc
    )


def et_evening(day: date) -> datetime:
    """该日美东 20:00（已收盘、数据已结算）。"""
    from zoneinfo import ZoneInfo

    return datetime(day.year, day.month, day.day, 20, 0, tzinfo=ZoneInfo("America/New_York")).astimezone(
        timezone.utc
    )


# ══════════════════════════════════════════════════════════════════
# 复活节 —— 唯一不按星期几排列的锚点
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "year,expected",
    [
        (2024, date(2024, 3, 31)),
        (2025, date(2025, 4, 20)),
        (2026, date(2026, 4, 5)),
        (2027, date(2027, 3, 28)),
        (2030, date(2030, 4, 21)),
    ],
)
def test_easter_sunday(year: int, expected: date):
    assert easter_sunday(year) == expected


# ══════════════════════════════════════════════════════════════════
# 休市日
# ══════════════════════════════════════════════════════════════════


def test_2026_holidays_match_published_calendar():
    """2026 年 NYSE 休市日，逐条对照公开日历。"""
    assert nyse_holidays(2026) == {
        date(2026, 1, 1),    # 元旦（周四）
        date(2026, 1, 19),   # 马丁·路德·金日
        date(2026, 2, 16),   # 总统日
        date(2026, 4, 3),    # 耶稣受难日
        date(2026, 5, 25),   # 阵亡将士纪念日
        date(2026, 6, 19),   # 六月节（周五）
        date(2026, 7, 3),    # 独立日 7/4 是周六 → 前移周五
        date(2026, 9, 7),    # 劳动节
        date(2026, 11, 26),  # 感恩节
        date(2026, 12, 25),  # 圣诞节（周五）
    }


def test_new_year_on_saturday_is_not_observed():
    """元旦的特殊规则：落在周六时 NYSE **不**在 12/31 补休。

    其他节日都是「周六→前一个周五」，唯独元旦例外。用通用规则处理
    会把 2021-12-31 误判成休市日。
    """
    assert date(2022, 1, 1).weekday() == 5  # 确认那天确实是周六
    assert date(2021, 12, 31) not in nyse_holidays(2021)
    assert is_trading_day(date(2021, 12, 31))


def test_new_year_on_sunday_shifts_to_monday():
    assert date(2023, 1, 1).weekday() == 6
    assert date(2023, 1, 2) in nyse_holidays(2023)


def test_juneteenth_only_from_2022():
    """六月节 2022 年才成为 NYSE 休市日，之前不能凭空多放一天。"""
    assert date(2021, 6, 18) not in nyse_holidays(2021)  # 2021-06-19 是周六
    assert is_trading_day(date(2021, 6, 18))
    assert date(2022, 6, 20) in nyse_holidays(2022)  # 2022-06-19 是周日 → 周一


def test_weekends_are_not_trading_days():
    assert not is_trading_day(date(2026, 9, 26))  # 周六
    assert not is_trading_day(date(2026, 9, 27))  # 周日
    assert is_trading_day(date(2026, 9, 25))     # 周五


def test_previous_trading_day_skips_weekend():
    assert previous_trading_day(date(2026, 9, 28)) == date(2026, 9, 25)  # 周一 → 周五
    assert previous_trading_day(date(2026, 9, 25)) == date(2026, 9, 24)


def test_previous_trading_day_skips_holiday():
    """感恩节 2026-11-26（周四）→ 前一个交易日是周三。"""
    assert previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)


# ══════════════════════════════════════════════════════════════════
# 「最近一个已收盘的交易日」—— 缓存新鲜度的判据
# ══════════════════════════════════════════════════════════════════


def test_intraday_does_not_count_today_as_completed():
    """盘中时，今天还没收盘，最近收完的是**上一个**交易日。

    这是最容易写错、代价又最大的一处：若盘中就把今天算作已有，
    整个交易日都会拿着昨天的数据当最新的用。
    """
    friday = date(2026, 9, 25)
    assert last_completed_trading_day(et_noon(friday)) == date(2026, 9, 24)


def test_after_close_counts_today():
    friday = date(2026, 9, 25)
    assert last_completed_trading_day(et_evening(friday)) == friday


def test_weekend_returns_friday():
    assert last_completed_trading_day(et_noon(date(2026, 9, 26))) == date(2026, 9, 25)
    assert last_completed_trading_day(et_evening(date(2026, 9, 27))) == date(2026, 9, 25)


def test_holiday_returns_previous_session():
    good_friday = date(2026, 4, 3)
    assert not is_trading_day(good_friday)
    assert last_completed_trading_day(et_evening(good_friday)) == date(2026, 4, 2)


def test_settlement_buffer_after_close():
    """刚收盘那几分钟数据还没结算完，仍算作没收盘 —— 免得空抓一次。"""
    from zoneinfo import ZoneInfo

    friday = date(2026, 9, 25)
    just_closed = datetime(
        2026, 9, 25, 16, 5, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(timezone.utc)
    assert last_completed_trading_day(just_closed) == date(2026, 9, 24)

    settled = datetime(
        2026, 9, 25, 16, 45, tzinfo=ZoneInfo("America/New_York")
    ).astimezone(timezone.utc)
    assert last_completed_trading_day(settled) == friday

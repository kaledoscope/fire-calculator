"""market_calendar.py —— 美股交易日历（纯计算，零 IO）。

**用途只有一个**：判断缓存里的最新 K 线是不是已经够新了，从而决定
「这次请求要不要真的去打 API」（SRS FR-005 的性能要求）。

设计上有意做得**宁可保守**：

    把一个休市日误判成交易日 → 多打一次 API，下一个交易日照常自愈
    把一个交易日误判成休市日 → 一直以为缓存是新的，**永远读旧数据**

后者是对用户可见的错误，前者只是浪费一次请求。所以日历**刻意不收录
临时休市**（飓风、国葬、9/11 之后的停市……）。这类事件无法预测，
一旦发生，代价是当天多打几次 API —— 这个方向的错是安全的。

本模块不读系统时间：`now` 一律由调用方传入。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# 美股常规收盘时间（美东）。16:00 收盘，但数据源通常要到 16:15~16:30
# 才把所有成交结算完。留个缓冲，免得刚收盘就以为新 K 线该有了。
_CLOSE_HOUR_ET = 16
_SETTLE_MINUTES = 30


# ══════════════════════════════════════════════════════════════════
# 复活节 —— 其他移动节日的锚点
# ══════════════════════════════════════════════════════════════════


def easter_sunday(year: int) -> date:
    """复活节主日（Anonymous Gregorian algorithm）。

    耶稣受难日（Good Friday）是它往前两天，这是唯一一个**不按星期几
    排列**的 NYSE 休市日，所以必须单独算出来。
    """
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    lam = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lam) // 451
    month, day = divmod(h + lam - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """某月第 n 个星期 `weekday`（0=周一）。n 为负表示倒数第 |n| 个。"""
    if n > 0:
        first = date(year, month, 1)
        offset = (weekday - first.weekday()) % 7
        return first + timedelta(days=offset + 7 * (n - 1))

    if month == 12:
        last = date(year, 12, 31)
    else:
        last = date(year, month + 1, 1) - timedelta(days=1)
    offset = (last.weekday() - weekday) % 7
    return last - timedelta(days=offset + 7 * (-n - 1))


def _observed(day: date) -> date:
    """周六 → 前一个周五；周日 → 后一个周一；否则当天。"""
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> set[date]:
    """该年 NYSE 全天休市的日期。

    ⚠️ 元旦有一条特殊规则：如果 1 月 1 日是**周六**，NYSE **不在**
    前一个周五（12/31）补休 —— 那天照常交易。所以元旦不能用通用的
    `_observed`，要单独处理。
    """
    holidays: set[date] = set()

    # 元旦
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:  # 周日 → 周一补休
        holidays.add(date(year, 1, 2))
    elif new_year.weekday() != 5:  # 周六不补休
        holidays.add(new_year)

    holidays.add(_nth_weekday(year, 1, 0, 3))  # 马丁·路德·金日：1 月第 3 个周一
    holidays.add(_nth_weekday(year, 2, 0, 3))  # 总统日：2 月第 3 个周一
    holidays.add(easter_sunday(year) - timedelta(days=2))  # 耶稣受难日
    holidays.add(_nth_weekday(year, 5, 0, -1))  # 阵亡将士纪念日：5 月最后一个周一
    if year >= 2022:  # 六月节 2022 年才成为 NYSE 休市日
        holidays.add(_observed(date(year, 6, 19)))
    holidays.add(_observed(date(year, 7, 4)))  # 独立日
    holidays.add(_nth_weekday(year, 9, 0, 1))  # 劳动节：9 月第 1 个周一
    holidays.add(_nth_weekday(year, 11, 3, 4))  # 感恩节：11 月第 4 个周四
    holidays.add(_observed(date(year, 12, 25)))  # 圣诞节

    return holidays


def is_trading_day(day: date) -> bool:
    """周末与 NYSE 全天休市日之外，都算交易日。

    含"宁可保守"的取舍：临时休市不算，见模块 docstring。
    """
    return day.weekday() < 5 and day not in nyse_holidays(day.year)


def previous_trading_day(day: date) -> date:
    """严格早于 `day` 的上一个交易日。"""
    cursor = day - timedelta(days=1)
    while not is_trading_day(cursor):
        cursor -= timedelta(days=1)
    return cursor


def last_completed_trading_day(now_utc: datetime) -> date:
    """最近一个**已经收盘**的交易日（美东时间口径）。

    这里是「要不要重新抓数据」的判据，所以必须区分两种情况：

        交易日盘中（比如周二上午）  → 周二尚未收盘，最近收完的是**周一**
        交易日盘后（周二 17:00 后） → 周二已收盘，就是**周二**

    不这么分的话，盘中会把「今天的 K 线」当成已有的，于是**整个交易日
    都读着昨天的数据还以为是最新的** —— 这正是用户要避免的那种问题。
    """
    from zoneinfo import ZoneInfo

    now_et = now_utc.astimezone(ZoneInfo("America/New_York"))
    today = now_et.date()

    if not is_trading_day(today):
        return previous_trading_day(today)

    settle = now_et.replace(
        hour=_CLOSE_HOUR_ET, minute=_SETTLE_MINUTES, second=0, microsecond=0
    )
    if now_et >= settle:
        return today
    return previous_trading_day(today)

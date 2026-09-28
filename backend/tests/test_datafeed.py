"""datafeed 测试。

**一个网络包都不发**：传输层是可注入的（`Transport = Callable[[str], str]`），
这里全部换成假的。所以既能覆盖「抓取失败」这种真实环境里很难复现的路径，
也不会让测试结果随行情、随数据源限流而飘。
"""

from __future__ import annotations

import json
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from backend import datafeed
from backend.datafeed import (
    CacheEntry,
    _annualised_dividend_growth,
    _annualised_price_growth,
    _cache_path,
    _parse_money,
    _payments_per_year,
    _trailing_annual_series,
    derive_params,
    get_quote,
    read_cache,
    search_symbols,
    write_cache,
)
from backend.models import ParamSource


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """重试的退避在测试里不必真等 —— 否则每个失败用例白等几百毫秒。

    重试本身仍然照跑（调用次数照数），只是不睡。
    """
    monkeypatch.setattr(datafeed, "_sleep", lambda _seconds: None)

# ══════════════════════════════════════════════════════════════════
# 假的传输层
# ══════════════════════════════════════════════════════════════════


def _price_payload(prices: list[tuple[date, float]]) -> str:
    """按 Nasdaq historical 的真实结构造响应体。"""
    rows = [
        {"date": d.strftime("%m/%d/%Y"), "close": f"${c:,.2f}"} for d, c in prices
    ]
    return json.dumps({"data": {"tradesTable": {"rows": rows}}})


def _sa_price_payload(prices: list[tuple[date, float]]) -> str:
    """按 stockanalysis 的真实结构造响应体：`[[毫秒时间戳, 收盘价], …]`。

    时间戳取当日 **UTC 零点** —— 和真接口一样。别在这里用本地时区，
    那样会把这个测试想验证的时区折算问题一起复制进假数据里，
    于是测试和实现同错，反而测不出东西。
    """
    rows = [
        [int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000), c]
        for d, c in prices
    ]
    return json.dumps({"status": "ok", "data": rows})


def _dividend_payload(dividends: list[tuple[date, float]]) -> str:
    rows = [{"dt": d.isoformat(), "amt": f"{a:.4f}"} for d, a in dividends]
    return json.dumps({"data": {"history": rows}})


# stockanalysis 用 URL 里的前缀区分 ETF 与个股，这里换回本项目的 asset_class 叫法
_PREFIX_TO_CLASS = {v: k for k, v in datafeed._PREFIX.items()}


#: 「info 接口自报的现价」的默认行为：跟随价格序列的末价。
#: 真实的数据源就是这样 —— 它自己的历史末价和现价本来就说的是同一件事。
AUTO_PRICE = object()


class FakeSource:
    """一个可控的假数据源。记录每一次调用，供断言「有没有发请求」。"""

    def __init__(
        self,
        *,
        prices: dict[str, list[tuple[date, float]]] | None = None,
        dividends: list[tuple[date, float]] | None = None,
        name: str | None = "Fake Fund",
        expense_ratio: str | None = "0.06%",
        self_price: object = AUTO_PRICE,
        search: list[dict] | None = None,
        failing: bool = False,
    ) -> None:
        # prices 按 assetclass 分开给：用来验证「先试 etf 再试 stocks」
        self.prices = prices or {}
        self.dividends = dividends or []
        self.name = name
        self.expense_ratio = expense_ratio
        #: info 接口自报的现价。传具体字符串可造出「自相矛盾」的数据源；
        #: 传 None 表示这个源不给现价（stockanalysis 就是这种）。
        self.self_price = self_price
        self.search = search or []
        self.failing = failing
        self.calls: list[str] = []

    def _reported_price(self) -> str | None:
        if self.self_price is AUTO_PRICE:
            for series in self.prices.values():
                if series:
                    return f"${series[-1][1]:,.2f}"
            return None
        return self.self_price  # type: ignore[return-value]

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        if self.failing:
            raise urllib.error.URLError("network down")

        # ── stockanalysis：价格与股息都在这个域上，先按路径区分 ──────
        # 顺序要紧：股息那条是兜底，放前面会把历史价格的请求也吞掉。
        if "stockanalysis" in url:
            if "/history?" in url:
                prefix = url.split("/symbol/")[1].split("/")[0]
                rows = self.prices.get(_PREFIX_TO_CLASS.get(prefix, "etf"))
                if rows is None:
                    return json.dumps({"status": "ok", "data": []})
                return _sa_price_payload(rows)
            return _dividend_payload(self.dividends)

        if "/historical?" in url:
            asset_class = url.split("assetclass=")[1].split("&")[0]
            rows = self.prices.get(asset_class)
            if rows is None:
                # 数据源对不存在的 assetclass 给空表，而不是报错
                return json.dumps({"data": {"tradesTable": {"rows": []}}})
            return _price_payload(rows)

        if "/info?" in url:
            return json.dumps(
                {
                    "data": {
                        "companyName": self.name,
                        "primaryData": {
                            "expenseRatio": self.expense_ratio,
                            "lastSalePrice": self._reported_price(),
                        },
                    }
                }
            )

        if "autocomplete" in url:
            return json.dumps({"data": self.search})

        raise AssertionError(f"假数据源收到了预期外的 URL：{url}")

    @property
    def network_calls(self) -> int:
        return len(self.calls)


def _geometric_prices(
    start: date, years: float, growth: float, price: float = 100.0, step: int = 7
) -> list[tuple[date, float]]:
    """严格按年化 `growth` 增长的价格序列（每 step 天一档）。"""
    out: list[tuple[date, float]] = []
    total_days = int(years * 365.25)
    for offset in range(0, total_days + 1, step):
        when = start + timedelta(days=offset)
        out.append((when, price * (1.0 + growth) ** (offset / 365.25)))
    return out


def _prices_ending_on(end: date, years: float, growth: float) -> list[tuple[date, float]]:
    """末日恰好落在 `end` 的价格序列。

    缓存测试必须让最后一根 K 线落在指定的交易日上，否则「缓存新鲜不新鲜」
    就成了碰运气 —— 序列长度取整会让末日漂移几天。
    """
    span = int(years * 365.25)
    span -= span % 7  # 与 step 对齐，保证 end 本身是序列里的一点
    return _geometric_prices(end - timedelta(days=span), years=span / 365.25, growth=growth)


def _quarterly(start: date, count: int, amount: float) -> list[tuple[date, float]]:
    return [(start + timedelta(days=91 * i), amount) for i in range(count)]


def _year_of_payments(
    year: int, count: int = 12, amount: float = 0.25, day: int = 15
) -> list[tuple[date, float]]:
    """某一年里均匀铺 `count` 笔派息，**全部落在本年内**。

    派息频率的判据是「完整自然年里有几笔」，所以测试数据必须让每一年的
    笔数是确定的 —— 用固定的 30 天/91 天步长会跨年漂移，某年 12 笔、
    下一年 13 笔，测出来的频率就成了碰运气。
    """
    step = 360 // count
    return [
        (date(year, 1, day) + timedelta(days=step * i), amount) for i in range(count)
    ]


def _years_of_payments(
    first: int, last: int, count: int = 12, base: float = 0.25, growth: float = 0.0
) -> list[tuple[date, float]]:
    """`first`..`last` 每个自然年 `count` 笔，金额逐年按 `growth` 递增。"""
    out: list[tuple[date, float]] = []
    for year in range(first, last + 1):
        out += _year_of_payments(year, count, base * (1.0 + growth) ** (year - first))
    return out


# ══════════════════════════════════════════════════════════════════
# 金额解析
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "text,expected",
    [
        ("$33.10", 33.10),
        ("1,234.5", 1234.5),
        ("0.06%", 0.06),
        ("  12.5  ", 12.5),
        ("N/A", None),
        ("n/a", None),
        ("--", None),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_parse_money(text, expected):
    assert _parse_money(text) == expected


# ══════════════════════════════════════════════════════════════════
# 缓存路径 —— 标的代码是用户输入，会被拼进文件路径
# ══════════════════════════════════════════════════════════════════


def test_cache_path_never_escapes_cache_dir(tmp_path: Path):
    """路径穿越必须被挡住。

    白名单有两条防线：斜杠被滤掉（于是穿越失效），或者滤完为空/超长时
    直接拒绝。哪条生效都行，**唯独不能落到缓存目录外面** —— 所以这里
    对两种结果都放行，只钉住「没逃出去」这个不变量。
    """
    contained, rejected = [], []
    for hostile in ["../../etc/passwd", "..%2F..%2Fetc", "a/../../b", "/etc/passwd"]:
        try:
            path = _cache_path(hostile, datafeed.SOURCE_NASDAQ, tmp_path)
        except ValueError:
            rejected.append(hostile)
            continue
        assert path.parent == tmp_path, f"{hostile} 逃出了缓存目录"
        contained.append(hostile)

    assert contained and rejected, "两条防线都应被覆盖到，否则这个测试是空转的"


def test_cache_path_filters_separators(tmp_path: Path):
    assert _cache_path("a/../../b", datafeed.SOURCE_NASDAQ, tmp_path).name == (
        "A....B.nasdaq.json"
    )


def test_cache_path_normalises_case(tmp_path: Path):
    assert _cache_path("schd", datafeed.SOURCE_NASDAQ, tmp_path) == _cache_path(
        "SCHD", datafeed.SOURCE_NASDAQ, tmp_path
    )


def test_two_sources_never_share_a_cache_file(tmp_path: Path):
    """两个来源**必须各存各的**。

    只存一份就得挑一个丢一个：要么丢了 stockanalysis 那份快数据（下次
    输入又得等），要么丢了 Nasdaq 那份好数据（永远换不成）。
    """
    fast = _cache_path("SCHD", datafeed.SOURCE_STOCKANALYSIS, tmp_path)
    slow = _cache_path("SCHD", datafeed.SOURCE_NASDAQ, tmp_path)
    assert fast != slow
    assert fast.name == "SCHD.stockanalysis.json"
    assert slow.name == "SCHD.nasdaq.json"


def test_source_survives_a_roundtrip(tmp_path: Path):
    """来源要落盘 —— 重启后仍得知道这份数据是谁给的。"""
    write_cache(_entry(), tmp_path)
    loaded = read_cache("SCHD", datafeed.SOURCE_NASDAQ, tmp_path)
    assert loaded is not None and loaded.source == datafeed.SOURCE_NASDAQ


@pytest.mark.parametrize("bad", ["", "   ", "///", "AAAAAAAAAAAAA"])
def test_cache_path_rejects_unusable_symbols(bad: str, tmp_path: Path):
    with pytest.raises(ValueError):
        _cache_path(bad, datafeed.SOURCE_NASDAQ, tmp_path)


# ══════════════════════════════════════════════════════════════════
# 价格年化增长
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("growth", [0.0, 0.05, 0.08, -0.03])
def test_price_growth_recovers_exact_geometric_rate(growth: float):
    """严格几何增长 → 回归必须还原出原来的 g。

    这是 `_annualised_price_growth` 的**定义性检验**：它算的不是别的，
    就是「这条序列的年化增长率」，人造一条已知答案的序列即可钉死。
    """
    prices = _geometric_prices(date(2016, 1, 4), years=10, growth=growth)
    assert _annualised_price_growth(prices) == pytest.approx(growth, abs=1e-9)


def test_price_growth_is_robust_to_endpoint_spike():
    """端点噪声不该带偏结果 —— 这正是用回归而非「首尾 CAGR」的理由。"""
    prices = _geometric_prices(date(2016, 1, 4), years=10, growth=0.08)
    spiked = prices[:-1] + [(prices[-1][0], prices[-1][1] * 1.4)]  # 末点虚高 40%
    naive_cagr = (spiked[-1][1] / spiked[0][1]) ** (
        365.25 / (spiked[-1][0] - spiked[0][0]).days
    ) - 1.0
    assert naive_cagr > 0.11  # 首尾法被带偏了
    assert _annualised_price_growth(spiked) == pytest.approx(0.08, abs=0.003)


def test_price_growth_handles_degenerate_input():
    assert _annualised_price_growth([]) == 0.0
    assert _annualised_price_growth([(date(2026, 1, 2), 100.0)]) == 0.0


# ══════════════════════════════════════════════════════════════════
# 派息频率与滚动年度合计
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "gap_days,expected",
    [(30, 12), (91, 4), (182, 2), (365, 1)],
)
def test_payments_per_year_inference(gap_days: int, expected: int):
    dates = [date(2016, 1, 4) + timedelta(days=gap_days * i) for i in range(12)]
    assert _payments_per_year(dates) == expected


def test_payments_per_year_survives_a_mixed_cadence():
    """回归测试：MAIN 式的「月度 + 季度补充」= 16 笔/年。

    12 笔月度（每月 15 日）+ 4 笔季度补充（3/6/9/12 月的 28 日）。
    早年按**派息间隔的中位数**反推，这个间隔分布给出 13（既有 13 天的
    密集段，也有 31 天的稀疏段），再被 `min(12, …)` 压成 12。于是末尾
    那个「最近 12 笔」的窗口只覆盖 9 个月 —— TTM 少算一个季度，
    股息率 7.8% 被报成 5.9%。

    数完整自然年的笔数就没有这个问题：一年里派了几笔是数出来的，
    与间隔怎么分布无关。
    """
    dates: list[date] = []
    for year in range(2022, 2026):
        dates += [date(year, month, 15) for month in range(1, 13)]
        dates += [date(year, month, 28) for month in (3, 6, 9, 12)]
    dates.sort()

    assert len(dates) == 64
    assert _payments_per_year(dates) == 16

    # 频率对了，年度窗口才正好是一年 —— 16 笔 × 0.25 = 4.00，而不是 3.00
    series = _trailing_annual_series([(d, 0.25) for d in dates], per_year=16)
    assert series[-1][1] == pytest.approx(4.00)


def test_payments_per_year_ignores_partial_years():
    """首年从数据起点开始、末年还没过完，两者都是半截，数进去只会数少。"""
    dates = (
        [date(2022, 11, 15), date(2022, 12, 15)]  # 2022 只有 2 笔
        + [d for y in (2023, 2024) for d, _ in _year_of_payments(y)]
        + [date(2025, 1, 15), date(2025, 2, 15), date(2025, 3, 15)]  # 2025 只有 3 笔
    )
    assert _payments_per_year(dates) == 12


def test_payments_per_year_ignores_a_one_off_special_dividend():
    """偶有一次特别派息只影响当年，不该改变频率判断。"""
    dates = [date(2016, 1, 4) + timedelta(days=91 * i) for i in range(10)]
    dates.insert(4, dates[3] + timedelta(days=3))  # 一次插进来的额外派息
    dates.sort()
    assert _payments_per_year(dates) == 4


def test_payments_per_year_ties_break_toward_the_smaller():
    """众数打平时取小的：多出来的那一笔更可能是特别派息。

    VFLO 的 2024 年有 13 笔、2025 年 12 笔 —— 真值是 12 笔/年，
    多出来的是年底额外的一次。
    """
    dates = (
        [d for d, _ in _year_of_payments(2023)]
        + [d for d, _ in _year_of_payments(2024, count=13)]
        + [d for d, _ in _year_of_payments(2025)]
        + [date(2026, 1, 15), date(2026, 2, 15)]  # 末年半截，不参与计数
    )
    dates.sort()
    assert _payments_per_year(dates) == 12


def test_trailing_annual_series_sums_complete_windows():
    dividends = _quarterly(date(2020, 1, 15), 8, 0.25)
    series = _trailing_annual_series(dividends, per_year=4)
    # 前 3 笔还没攒够一整年，只能就着已有的累加
    assert [v for _, v in series[:4]] == pytest.approx([0.25, 0.50, 0.75, 1.00])
    # 之后每一笔都是完整 4 笔
    assert all(v == pytest.approx(1.00) for _, v in series[3:])


def test_trailing_window_does_not_overcount_short_year():
    """回归测试：364 天里挤进 5 笔季度派息时，不能按 5 笔算。

    实测 SCHD 的 2025-09-24 与 2026-09-23 相隔 364 天，用「过去 365 天」
    这个日历窗口会把 5 笔都圈进来 —— 股息率凭空高 25%，增长率彻底失真。
    按「最近 4 笔」则永远是完整的一个年度。
    """
    stamps = [
        date(2025, 9, 24),
        date(2025, 12, 24),
        date(2026, 3, 25),
        date(2026, 6, 24),
        date(2026, 9, 23),
    ]
    # 确认这组数据确实能触发原 bug：最早一笔距最新一笔只有 364 天
    assert (stamps[-1] - stamps[0]).days == 364

    dividends = [(d, 0.25) for d in stamps]
    assert _payments_per_year(stamps) == 4

    trailing = _trailing_annual_series(dividends, per_year=4)[-1][1]
    assert trailing == pytest.approx(1.00)  # 4 笔，不是 5 笔

    naive = sum(a for d, a in dividends if (stamps[-1] - d).days < 365)
    assert naive == pytest.approx(1.25)  # 日历窗口会多算一笔


# ══════════════════════════════════════════════════════════════════
# 股息年化增长
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("growth", [0.0, 0.03, 0.06])
def test_dividend_growth_recovers_exact_geometric_rate(growth: float):
    """派息按年化 g 增长 → 滚动年度合计之比也应还原出 g。"""
    base = date(2016, 1, 15)
    dividends = [
        (base + timedelta(days=91 * i), 0.25 * (1.0 + growth) ** (91 * i / 365.25))
        for i in range(24)
    ]
    result = _annualised_dividend_growth(dividends, max_years=15)
    assert result is not None
    rate, years = result
    assert rate == pytest.approx(growth, abs=1e-9)
    # 跨度一并返回：调用方要拿它判断这个增长率可不可信
    assert years == pytest.approx(4.98, abs=0.05)


def test_dividend_growth_flat_payments_is_zero():
    """派息一直没变 —— 这是**测出来**的 0，和「测不了」不是一回事。"""
    result = _annualised_dividend_growth(_quarterly(date(2016, 1, 15), 24, 0.25), 15)
    assert result is not None
    rate, years = result
    assert rate == pytest.approx(0.0)
    assert years > 1.0


def test_dividend_growth_returns_none_when_no_comparable_window():
    """凑不出两个完整年度就返回 None，不拿半截窗口硬算。

    **必须和 0.0 分开**：0.0 的意思是「测过了，就是不涨」，
    None 的意思是「测不了」。早先两者共用一个 0.0，
    界面于是把「不知道」显示成「不增长」。
    """
    assert _annualised_dividend_growth(_quarterly(date(2025, 1, 15), 3, 0.25), 15) is None
    assert _annualised_dividend_growth([], 15) is None


def test_dividend_growth_span_is_the_measurement_window_not_the_history():
    """返回的跨度是**量增长用的区间**，比派息记录的总长要短。

    这是 VFLO 那类新基金的关键：3 年派息记录听着够，但每年 12 笔，
    要凑两个完整年度去对比，最早那个窗口只能落在 2 年多以前 ——
    真正量出增长率的区间自始至终只有那么长。
    """
    dividends = _years_of_payments(2023, 2026)  # 4 个自然年 × 12 笔
    result = _annualised_dividend_growth(dividends, max_years=15)
    assert result is not None
    _, years = result

    history = (dividends[-1][0] - dividends[0][0]).days / 365.25
    assert history == pytest.approx(3.90, abs=0.05)
    assert years == pytest.approx(3.00, abs=0.05)
    # 月度派息要攒够 12 笔才开得了一个窗口，所以量增长的区间比派息记录
    # 整整短一年。VFLO 就是这个差：记录 3.17 年，量增长的只有 2.24 年。
    assert history - years == pytest.approx(0.90, abs=0.05)


# ══════════════════════════════════════════════════════════════════
# derive_params
# ══════════════════════════════════════════════════════════════════


def test_derive_params_forces_expense_ratio_to_zero():
    """A23：抓取模式下费用率必须为 0 —— 历史价格已含费，再扣一次是重复计算。"""
    prices = _geometric_prices(date(2016, 1, 4), years=10, growth=0.08)
    params = derive_params(prices, [], lookback_years=10)
    assert params.expense_ratio == 0.0
    assert params.source is ParamSource.FETCHED
    assert params.lookback_years == 10


def test_derive_params_dividend_yield_uses_trailing_twelve_months():
    prices = _geometric_prices(date(2016, 1, 4), years=10, growth=0.0, price=100.0)
    dividends = _quarterly(date(2016, 2, 15), 40, 0.25)  # 每年 1.00
    params = derive_params(prices, dividends, lookback_years=10)
    assert params.dividend_yield == pytest.approx(0.01)  # 1.00 / 100.00


def test_derive_params_lookback_window_shrinks_price_history():
    """回看窗口只作用于价格增长：前 5 年涨、后 5 年不动，取 4 年应只见「不动」。"""
    early = _geometric_prices(date(2016, 1, 4), years=5, growth=0.15, price=100.0)
    pivot = early[-1][0]
    flat = early[-1][1]
    late = [(pivot + timedelta(days=7 * i), flat) for i in range(1, 261)]
    prices = early + late

    assert derive_params(prices, [], lookback_years=4).price_growth == pytest.approx(0.0)
    # 同一份数据，窗口拉长到 15 年就能看见那 15% 的前半段
    assert derive_params(prices, [], lookback_years=15).price_growth > 0.05


def test_derive_params_rejects_empty_prices():
    with pytest.raises(ValueError):
        derive_params([], [], lookback_years=10)


# ══════════════════════════════════════════════════════════════════
# 缓存读写
# ══════════════════════════════════════════════════════════════════


def _entry(
    symbol: str = "SCHD",
    last_bar: date = date(2026, 9, 24),
    source: str = datafeed.SOURCE_NASDAQ,
) -> CacheEntry:
    return CacheEntry(
        symbol=symbol,
        source=source,
        asset_class="etf",
        last_bar_date=last_bar,
        prices=[(date(2026, 9, 23), 30.0), (last_bar, 30.5)],
        dividends=[(date(2026, 9, 23), 0.2665)],
        name="Schwab US Dividend Equity ETF",
        expense_ratio=0.0006,
        fetched_at="2026-09-24T20:00:00+00:00",
    )


def test_cache_roundtrip(tmp_path: Path):
    write_cache(_entry(), tmp_path)
    loaded = read_cache("SCHD", datafeed.SOURCE_NASDAQ, tmp_path)
    assert loaded is not None
    assert loaded.last_bar_date == date(2026, 9, 24)
    assert loaded.prices == [(date(2026, 9, 23), 30.0), (date(2026, 9, 24), 30.5)]
    assert loaded.expense_ratio == 0.0006
    assert read_cache("schd", datafeed.SOURCE_NASDAQ, tmp_path) is not None  # 大小写不敏感


def test_cache_freshness_boundary():
    """判据是「≥ 最近一个已收盘交易日」，等于也算命中。"""
    entry = _entry(last_bar=date(2026, 9, 24))
    assert entry.is_fresh_for(date(2026, 9, 24))
    assert entry.is_fresh_for(date(2026, 9, 23))  # 缓存比要求更新
    assert not entry.is_fresh_for(date(2026, 9, 25))


def test_corrupt_cache_is_treated_as_absent(tmp_path: Path):
    """缓存坏了不是错误，只是「没有缓存」—— 重抓一次就好，别把整个请求打挂。"""
    path = _cache_path("SCHD", datafeed.SOURCE_NASDAQ, tmp_path)
    path.write_text("{ this is not json", encoding="utf-8")
    assert read_cache("SCHD", datafeed.SOURCE_NASDAQ, tmp_path) is None


def test_missing_cache_returns_none(tmp_path: Path):
    assert read_cache("NOPE", datafeed.SOURCE_NASDAQ, tmp_path) is None


# ══════════════════════════════════════════════════════════════════
# get_quote —— 用户要求的「以天缓存」核心行为
# ══════════════════════════════════════════════════════════════════

# 2026-09-25（周五）盘中：最近一个已收盘的交易日是 09-24（周四）
FRIDAY_INTRADAY = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)


def test_fresh_cache_sends_zero_network_requests(tmp_path: Path):
    """★ 用户的核心要求：最近一个工作日没变，就不要再打 API。

    这是整个缓存设计存在的理由，所以单独钉一条。
    """
    write_cache(_entry(last_bar=date(2026, 9, 24)), tmp_path)
    source = FakeSource()

    result = get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert result.available
    assert result.from_cache is True
    assert source.network_calls == 0
    assert result.as_of == "2026-09-24"
    assert result.params is not None
    assert result.last_price == 30.5  # 前端要靠它填「当前股价」


def test_repeated_calls_never_touch_network(tmp_path: Path):
    """反复切标的、改参数都不该产生请求 —— 判据在一天之内是常量。"""
    write_cache(_entry(last_bar=date(2026, 9, 24)), tmp_path)
    source = FakeSource()
    for _ in range(5):
        get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)
    assert source.network_calls == 0


def test_stale_cache_triggers_fetch_and_rewrites(tmp_path: Path):
    """缓存停在 09-22，而最近收完的是 09-24 —— 这时才该真的去打 API。"""
    write_cache(_entry(last_bar=date(2026, 9, 22)), tmp_path)
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices}, dividends=_quarterly(date(2022, 3, 15), 18, 0.25))

    result = get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert source.network_calls > 0
    assert result.from_cache is False
    assert result.as_of == prices[-1][0].isoformat()
    # 数据源把价格写成两位小数的字符串（'$153.64'），所以回来的是分精度
    assert result.last_price == pytest.approx(prices[-1][1], abs=0.01)
    # 抓完要落盘，下次才不用再抓
    assert read_cache("SCHD", datafeed.SOURCE_NASDAQ, tmp_path).last_bar_date == prices[-1][0]


def test_network_failure_falls_back_to_stale_cache(tmp_path: Path):
    """抓不到时退回旧缓存，并**明确告诉用户这是旧的** —— 不能装作是最新的。"""
    write_cache(_entry(last_bar=date(2026, 9, 20)), tmp_path)
    result = get_quote(
        "SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=FakeSource(failing=True), cache_dir=tmp_path
    )

    assert result.available is True
    assert result.as_of == "2026-09-20"
    assert result.reason is not None and "缓存" in result.reason


def test_network_failure_without_cache_reports_unavailable(tmp_path: Path):
    result = get_quote(
        "SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=FakeSource(failing=True), cache_dir=tmp_path
    )
    assert result.available is False
    assert result.params is None
    assert result.reason is not None


def test_unknown_symbol_reports_unavailable(tmp_path: Path):
    source = FakeSource(prices={})  # 两个 assetclass 都返回空表
    result = get_quote("ZZZZ", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)
    assert result.available is False
    assert source.network_calls == 2  # etf / stocks 各试一次


# ══ 网络故障 vs 代码写错：两件事的话必须说得不一样 ═══════════════════
#
# 这两种情况都表现为「拿不到价格」，但对用户的含义正好相反。早先共用一句
# 「可能是代码写错，或数据源暂时不可达」，于是数据源一抖，界面就去指控用户
# 填错了代码 —— 用户会真的去改，而代码本来是对的。


def test_unreachable_blames_the_network_not_the_symbol(tmp_path: Path):
    result = get_quote(
        "VTI", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=FakeSource(failing=True), cache_dir=tmp_path
    )
    assert result.reason is not None
    assert "不可达" in result.reason
    # 关键：一个字都没收到，就**没有依据**说代码写错了。
    # 断言查的是「有没有指控代码」，不是「有没有提到代码」——
    # 文案里出现「这不代表代码有问题」是对的，不该被判失败。
    assert "写错" not in result.reason
    assert "查不到这个代码" not in result.reason


def test_unknown_symbol_blames_the_symbol_not_the_network(tmp_path: Path):
    """数据源明确答了「没有这个标的」，这时候才该怀疑代码。"""
    result = get_quote(
        "ZZZZ", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=FakeSource(prices={}), cache_dir=tmp_path
    )
    assert result.reason is not None
    assert "代码" in result.reason
    assert "不可达" not in result.reason


def test_unreachable_still_tries_the_second_asset_class(tmp_path: Path):
    """即使第一次不可达也照样试第二次。

    某个 assetclass 的端点单独抽风是可能的；为省一次超时就把本来能成功的
    抓取变成失败，不划算。这里两个都不可达，所以最终仍报「不可达」。
    """
    source = FakeSource(failing=True)
    result = get_quote("QQQ", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    historical = [u for u in source.calls if "/historical?" in u]
    tried = {u.split("assetclass=")[1].split("&")[0] for u in historical}
    assert tried == {"etf", "stocks"}, f"两个 assetclass 都该试过，实际试了 {tried}"
    assert result.reason is not None and "不可达" in result.reason


def test_flaky_etf_endpoint_still_recovers_via_stocks(tmp_path: Path):
    """etf 端点抽风、stocks 正常时，必须靠第二次拿到数据 —— 不能直接放弃。"""

    class EtfOnlyFlaky(FakeSource):
        def __call__(self, url: str) -> str:
            if "/historical?" in url and "assetclass=etf" in url:
                self.calls.append(url)
                raise urllib.error.URLError("etf 端点超时")
            return super().__call__(url)

    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.10)
    source = EtfOnlyFlaky(prices={"stocks": prices})
    result = get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert result.available is True
    assert result.asset_class == "stocks"


def test_transport_failure_is_retried_before_giving_up(tmp_path: Path):
    """偶发超时重试一次就好的情况很常见；放弃的代价是冤枉用户的代码。"""

    class FlakyOnce(FakeSource):
        def __call__(self, url: str) -> str:
            if len(self.calls) == 0:
                self.calls.append(url)
                raise urllib.error.URLError("第一次超时")
            return super().__call__(url)

    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.10)
    source = FlakyOnce(prices={"etf": prices})
    result = get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert result.available is True, "第一次超时后应当重试并成功"


def test_empty_symbol_is_rejected_without_network(tmp_path: Path):
    source = FakeSource()
    result = get_quote("   ", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)
    assert result.available is False
    assert source.network_calls == 0


def test_resolve_prefers_etf_then_falls_back_to_stocks(tmp_path: Path):
    """本项目面向 ETF 投资者，先试 etf；个股才走到第二个。"""
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.12)

    # ETF：第一个就中，不该多打一次请求
    only_etf = FakeSource(prices={"etf": prices})
    assert get_quote(
        "SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=only_etf, cache_dir=tmp_path
    ).asset_class == "etf"
    historical = [u for u in only_etf.calls if "/historical?" in u]
    assert len(historical) == 1 and "assetclass=etf" in historical[0]

    # 个股：etf 拿到空表，才退到 stocks
    only_stocks = FakeSource(prices={"stocks": prices})
    assert get_quote(
        "NVDA", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=only_stocks, cache_dir=tmp_path
    ).asset_class == "stocks"
    historical = [u for u in only_stocks.calls if "/historical?" in u]
    assert len(historical) == 2
    assert "assetclass=etf" in historical[0] and "assetclass=stocks" in historical[1]


def test_expense_ratio_is_reported_but_not_computed(tmp_path: Path):
    """管理费只作展示：`expense_ratio_info` 有值，而参与计算的 params 里是 0。"""
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices}, expense_ratio="0.06%")

    result = get_quote("SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert result.expense_ratio_info == pytest.approx(0.0006)
    assert result.params.expense_ratio == 0.0


def test_force_bypasses_fresh_cache(tmp_path: Path):
    write_cache(_entry(last_bar=date(2026, 9, 24)), tmp_path)
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices})

    result = get_quote(
        "SCHD", source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path, force=True
    )

    assert source.network_calls > 0
    assert result.from_cache is False


def test_lookback_change_reuses_same_cache(tmp_path: Path):
    """缓存一次抓满 15 年，所以 10 年 ↔ 15 年切换**不需要重新抓**。"""
    prices = _prices_ending_on(date(2026, 9, 24), years=15, growth=0.09)
    source = FakeSource(prices={"etf": prices}, dividends=_quarterly(date(2012, 3, 15), 58, 0.25))

    first = get_quote("SCHD", 10, source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)
    calls_after_first = source.network_calls
    second = get_quote("SCHD", 15, source=datafeed.SOURCE_NASDAQ, now=FRIDAY_INTRADAY, transport=source, cache_dir=tmp_path)

    assert source.network_calls == calls_after_first  # 没有新请求
    assert second.from_cache is True
    assert second.lookback_years == 15
    # 窗口不同 → 提取出的参数可以不同，但数据源是同一份
    assert first.as_of == second.as_of


def test_after_close_same_day_cache_goes_stale(tmp_path: Path):
    """收盘结算后，当天就成了「最近一个已收盘交易日」，缓存随即过期。

    这条保证了数据**当天就能更新**，而不是要等到第二天。
    """
    write_cache(_entry(last_bar=date(2026, 9, 24)), tmp_path)
    after_close = datetime(2026, 9, 25, 21, 0, tzinfo=timezone.utc)  # 美东 17:00
    source = FakeSource(prices={"etf": _prices_ending_on(date(2026, 9, 25), 5, 0.09)})

    result = get_quote("SCHD", now=after_close, transport=source, cache_dir=tmp_path)

    assert source.network_calls > 0
    assert result.from_cache is False


# ══════════════════════════════════════════════════════════════════
# search_symbols
# ══════════════════════════════════════════════════════════════════


def test_search_symbols_parses_results():
    source = FakeSource(
        search=[
            {"symbol": "SCHD", "name": "Schwab US Dividend Equity ETF", "asset": "etf"},
            {"symbol": "SCHG", "name": "Schwab US Large-Cap Growth ETF", "asset": "etf"},
        ]
    )
    results = search_symbols("sch", transport=source)
    assert [r["symbol"] for r in results] == ["SCHD", "SCHG"]
    assert results[0]["asset"] == "ETF"


def test_search_symbols_empty_query_sends_nothing():
    source = FakeSource()
    assert search_symbols("   ", transport=source) == []
    assert source.network_calls == 0


def test_search_symbols_survives_failure():
    assert search_symbols("sch", transport=FakeSource(failing=True)) == []


# ══════════════════════════════════════════════════════════════════
# 两个来源：stockanalysis 快路径 + Nasdaq 覆盖
# ══════════════════════════════════════════════════════════════════


def test_fast_path_never_touches_nasdaq(tmp_path: Path, universe):
    """★ 快路径的**全部意义**就在这一条：一次 Nasdaq 都不碰。

    Nasdaq 的 info 接口实测要几十秒，顺手调它一下就把快路径拖成慢的，
    先渲染的意义直接归零。名称因此改从**离线清单**里取 —— 零网络开销。
    """
    universe(["SCHD"])
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices})

    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )

    assert result.available is True
    assert result.source == datafeed.SOURCE_STOCKANALYSIS
    assert not [u for u in source.calls if "nasdaq" in u], "快路径不该碰 Nasdaq"
    assert result.name == "Some Fund Name"  # 来自离线清单，不是网络
    assert result.expense_ratio_info is None


def test_fast_path_falls_back_to_the_symbol_without_a_list(tmp_path: Path, universe):
    """没有清单时名称回落成代码本身 —— 宁可显示得朴素，也不能空着。"""
    universe(None)
    source = FakeSource(prices={"etf": _geometric_prices(date(2021, 9, 24), 5, 0.09)})

    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )

    assert result.name == "SCHD"


def test_slow_path_reports_nasdaq_as_the_source(tmp_path: Path):
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices})

    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )

    assert result.source == datafeed.SOURCE_NASDAQ
    assert result.name == "Fake Fund"  # 慢路径才拿得到名称与费率
    assert result.expense_ratio_info == pytest.approx(0.0006)


def test_each_source_keeps_its_own_cache(tmp_path: Path):
    """两个来源各存各的 —— 缺一份就得牺牲一头。

    丢了 stockanalysis 那份，下次输入又得等；丢了 Nasdaq 那份，
    就永远换不成更好的数据。
    """
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09)
    source = FakeSource(prices={"etf": prices})

    for which in datafeed.SOURCES:
        get_quote(
            "SCHD",
            source=which,
            now=FRIDAY_INTRADAY,
            transport=source,
            cache_dir=tmp_path,
        )

    fast = read_cache("SCHD", datafeed.SOURCE_STOCKANALYSIS, tmp_path)
    slow = read_cache("SCHD", datafeed.SOURCE_NASDAQ, tmp_path)
    assert fast is not None and fast.source == datafeed.SOURCE_STOCKANALYSIS
    assert slow is not None and slow.source == datafeed.SOURCE_NASDAQ
    # 后写的 Nasdaq 没有踩掉先写的 stockanalysis
    assert fast.last_bar_date == slow.last_bar_date


def test_one_sources_cache_does_not_satisfy_the_other(tmp_path: Path):
    """只有 Nasdaq 的缓存时，快路径仍然要自己去抓 —— 不能拿慢的那份充数，
    否则用户下一次输入仍要等几十秒，快路径等于没做。"""
    write_cache(_entry(source=datafeed.SOURCE_NASDAQ), tmp_path)
    source = FakeSource(prices={"etf": _geometric_prices(date(2021, 9, 24), 5, 0.09)})

    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )

    assert result.from_cache is False
    assert source.network_calls > 0


def test_unknown_source_is_rejected_without_network(tmp_path: Path):
    source = FakeSource()
    result = get_quote(
        "SCHD",
        source="bloomberg",
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )
    assert result.available is False
    assert source.network_calls == 0


def test_nasdaq_keeps_stockanalysis_dividends_when_its_own_fetch_fails(tmp_path: Path):
    """股息只有一个来源（stockanalysis），没有「Nasdaq 的股息」这回事。

    所以 Nasdaq 这次没抓到股息时必须沿用快路径那份 —— 否则界面上一秒
    还显示对的股息率会突然变成 0：**为了换个更好的源，反而把数据换坏了**。
    """
    prices = _geometric_prices(date(2021, 9, 24), years=5, growth=0.09, price=100.0)
    dividends = _quarterly(date(2022, 3, 15), 18, 0.25)

    fast = get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": prices}, dividends=dividends),
        cache_dir=tmp_path,
    )
    assert fast.params is not None and fast.params.dividend_yield > 0

    class DividendEndpointDown(FakeSource):
        def __call__(self, url: str) -> str:
            if "dividend" in url:
                self.calls.append(url)
                raise urllib.error.URLError("股息端点挂了")
            return super().__call__(url)

    slow = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=DividendEndpointDown(prices={"etf": prices}),
        cache_dir=tmp_path,
    )

    assert slow.params is not None
    # 只比到千分之一：两个源的价格精度不同（Nasdaq 给到分，stockanalysis
    # 给全精度），分母差一点点，收益率自然差一点点。这条要钉的是
    # 「股息没被抹成 0」，不是「两个源逐位相同」。
    assert slow.params.dividend_yield == pytest.approx(
        fast.params.dividend_yield, rel=1e-3
    )
    assert slow.params.dividend_yield > 0


# ══ stockanalysis 的解析 ═════════════════════════════════════════════


def _sa_transport(rows: list[list[float]]) -> datafeed.Transport:
    payload = json.dumps({"status": "ok", "data": rows})

    def _get(_url: str) -> str:
        return payload

    return _get


def _ms(when: datetime) -> int:
    return int(when.timestamp() * 1000)


def test_stockanalysis_bar_dates_are_read_in_utc():
    """时间戳按 **UTC** 折算日期。

    本机在 UTC+8。若误用本地时区，美东盘中/收盘那一刻会被算到第二天，
    最新一根 K 线的日期就比实际晚一天，缓存新鲜度跟着一起判错。

    这里特意挑 23:00Z：UTC 下是 09-24，UTC+8 下就成了 09-25。
    """
    rows = [[_ms(datetime(2026, 9, 24, 23, 0, tzinfo=timezone.utc)), 30.5]]
    prices = datafeed._fetch_prices_sa(
        "SCHD", "etf", 15, _sa_transport(rows), date(2026, 9, 25)
    )
    assert prices == [(date(2026, 9, 24), 30.5)]


def test_stockanalysis_drops_bars_outside_the_window():
    """回看窗口外的老点与「未来」的点都要剔掉 —— 后者会让缓存误判成新鲜。"""
    rows = [
        [_ms(datetime(2010, 1, 4, tzinfo=timezone.utc)), 10.0],  # 太老（窗口 1 年）
        [_ms(datetime(2026, 9, 23, tzinfo=timezone.utc)), 30.0],
        [_ms(datetime(2026, 9, 24, tzinfo=timezone.utc)), 30.5],
        [_ms(datetime(2026, 12, 31, tzinfo=timezone.utc)), 99.0],  # 未来
    ]
    prices = datafeed._fetch_prices_sa(
        "SCHD", "etf", 1, _sa_transport(rows), date(2026, 9, 25)
    )
    assert prices == [(date(2026, 9, 23), 30.0), (date(2026, 9, 24), 30.5)]


def test_stockanalysis_tolerates_junk_rows():
    """未公开接口，形状随时可能变。畸形行跳过即可，不该把整次抓取带崩。"""
    rows = [
        "not-a-row",
        [None, 30.0],
        [_ms(datetime(2026, 9, 24, tzinfo=timezone.utc))],
        [_ms(datetime(2026, 9, 24, tzinfo=timezone.utc)), 0.0],  # 价格为 0
        [_ms(datetime(2026, 9, 24, tzinfo=timezone.utc)), 30.5],
    ]
    prices = datafeed._fetch_prices_sa(
        "SCHD", "etf", 15, _sa_transport(rows), date(2026, 9, 25)
    )
    assert prices == [(date(2026, 9, 24), 30.5)]


def test_stockanalysis_empty_payload_means_symbol_not_found():
    assert (
        datafeed._fetch_prices_sa(
            "ZZZZ", "etf", 15, _sa_transport([]), date(2026, 9, 25)
        )
        is None
    )


# ══════════════════════════════════════════════════════════════════
# 代码清单（离线快照）
# ══════════════════════════════════════════════════════════════════


@pytest.fixture
def universe(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """装一份假的代码清单。`None` 表示**没有清单**这个状态。"""

    def _install(symbols: list[str] | None) -> None:
        path = tmp_path / "symbols_us.txt"
        if symbols is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(
                "\n".join(f"{s}\tSome Fund Name" for s in symbols) + "\n",
                encoding="utf-8",
            )
        monkeypatch.setattr(datafeed, "SYMBOL_UNIVERSE", path)
        datafeed._load_universe.cache_clear()  # lru_cache，不clear就串味

    yield _install
    datafeed._load_universe.cache_clear()


def test_known_symbol_is_true_for_listed_codes(universe):
    universe(["SCHD", "VTI", "NVDA"])
    assert datafeed.is_known_symbol("SCHD") is True
    assert datafeed.is_known_symbol("schd") is True  # 大小写不敏感


def test_unknown_symbol_is_false_when_a_list_exists(universe):
    universe(["SCHD"])
    assert datafeed.is_known_symbol("ZZZZ") is False


def test_missing_list_yields_none_not_false(universe):
    """没有清单时必须返回 None —— 返回 False 等于宣称「所有代码都不存在」，
    会把每一个用户都冤枉一遍。"""
    universe(None)
    assert datafeed.is_known_symbol("SCHD") is None


def test_known_flag_rides_along_on_the_result(tmp_path: Path, universe):
    universe(["SCHD", "VTI"])
    result = get_quote(
        "ZZZZ",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={}),
        cache_dir=tmp_path,
    )
    assert result.known is False


def test_known_is_none_when_there_is_no_list(tmp_path: Path, universe):
    universe(None)
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": _geometric_prices(date(2021, 9, 24), 5, 0.09)}),
        cache_dir=tmp_path,
    )
    assert result.known is None


def test_universe_file_missing_is_none_not_empty(tmp_path: Path, universe):
    """空清单和没有清单是两回事：前者会把所有代码判成不存在。"""
    universe([])
    assert datafeed.is_known_symbol("SCHD") is None


# ══ 「没有答复」与「答复就是没有」—— 前端要拿它决定该等还是该报错 ═══


class _HttpError:
    """永远回同一个 HTTP 状态码的传输层。"""

    def __init__(self, code: int) -> None:
        self.code = code
        self.calls: list[str] = []

    def __call__(self, url: str) -> str:
        self.calls.append(url)
        raise urllib.error.HTTPError(url, self.code, "boom", {}, None)  # type: ignore[arg-type]


def test_stockanalysis_404_means_no_such_symbol(tmp_path: Path):
    """实测：e/ZZZZZZ 回 404，而 e/ZZZZ 回 200 空表。两种都是「查无此标的」。

    404 若被当成网络故障，用户拼错一个字母就要白等两轮超时，最后还被告知
    「网络不可达」—— 于是他去查自己的网，而问题在代码上。
    """
    source = _HttpError(404)
    result = get_quote(
        "ZZZZZZ",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )
    assert result.available is False
    assert result.failure == "unknown_symbol"
    assert "不可达" not in (result.reason or "")


def test_404_is_not_retried(tmp_path: Path):
    """明确的答复不该重试 —— 重试只是把「查无此标的」拖慢一倍。"""
    source = _HttpError(404)
    get_quote(
        "ZZZZZZ",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )
    # 两个 assetclass 各一次，不该因为重试翻成 4 次
    assert len(source.calls) == 2


def test_server_error_is_still_retried(tmp_path: Path):
    """500 是「接口抽风」，不是答复 —— 这个才该重试。"""
    source = _HttpError(500)
    get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=source,
        cache_dir=tmp_path,
    )
    assert len(source.calls) == 4  # 2 个 assetclass × 2 次重试


def test_unreachable_is_labelled_as_such(tmp_path: Path):
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(failing=True),
        cache_dir=tmp_path,
    )
    assert result.failure == "unreachable"


def test_bad_source_is_labelled_as_such(tmp_path: Path):
    result = get_quote(
        "SCHD",
        source="bloomberg",
        now=FRIDAY_INTRADAY,
        transport=FakeSource(),
        cache_dir=tmp_path,
    )
    assert result.failure == "bad_source"


def test_success_carries_no_failure_label(tmp_path: Path):
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": _geometric_prices(date(2021, 9, 24), 5, 0.09)}),
        cache_dir=tmp_path,
    )
    assert result.available is True and result.failure is None


# ══ 数据源自相矛盾时，不采信它 —— 实测踩到的坑 ═══════════════════
#
# Nasdaq 给 VFLO 的历史序列末价是 80243.348，而**同一时刻它自己的 info 接口**
# 说现价 $51.55 —— 相差 1556 倍。那条坏数据经 SOURCE_RANK 覆盖掉了
# stockanalysis 的正确值（$51.61），于是：
#   当前股价 80243 → 初始市值 99×80243 = $7.94M
#   股息率 = 年派息/收盘价 = 0.24/80243 = 0.00%
#   price_growth 23.0% → 期末单价 $1,793,852 → 期末总值 $61.68M（合理值约 $2.3M）
# 一个数错，四处遭殃。所以判定依据只有一个：**这个源自己的两份响应**对不上。


def test_price_agreement_uses_relative_difference():
    # 相差 1500 倍这种量级才是要抓的；700 美元的 VOO 和 30 美元的 SCHD 各自自洽
    assert datafeed._prices_agree(100.0, 100.0)
    assert datafeed._prices_agree(100.0, 120.0)  # 差 20%，在容差内
    assert not datafeed._prices_agree(100.0, 140.0)  # 差 40%，超了
    assert not datafeed._prices_agree(80243.348, 51.55)  # 实测那条
    # 非正数没有「量级」可言，一律判不一致
    assert not datafeed._prices_agree(0.0, 10.0)
    assert not datafeed._prices_agree(10.0, -1.0)


def test_self_contradicting_source_is_rejected_as_bad_data(tmp_path: Path):
    """历史末价与自报现价相差 1556 倍 —— 这份数据不能用。"""
    prices = _prices_ending_on(date(2026, 9, 24), 3, 0.2)
    scaled = [(d, c * 1555.0) for d, c in prices]  # 量纲错了的那一份
    transport = FakeSource(prices={"etf": scaled}, self_price="$51.55")

    result = get_quote(
        "VFLO",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=transport,
        cache_dir=tmp_path,
    )

    assert result.available is False
    assert result.failure == "bad_data"
    # 说清楚是**数据**的问题，不是用户填错了代码
    assert "现价" in (result.reason or "")


def test_bad_data_is_never_written_to_cache(tmp_path: Path):
    """坏数据一旦落盘，就得靠它自然过期才能清掉 —— 而「新鲜」是按交易日判的，
    也就是用户会整整一天看着那个错数，还没法靠刷新绕开。所以不入库。"""
    scaled = [(d, c * 1555.0) for d, c in _prices_ending_on(date(2026, 9, 24), 3, 0.2)]
    get_quote(
        "VFLO",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": scaled}, self_price="$51.55"),
        cache_dir=tmp_path,
    )
    assert read_cache("VFLO", datafeed.SOURCE_NASDAQ, tmp_path) is None


def test_consistent_source_still_passes_the_guard(tmp_path: Path):
    """守卫不能把正常数据也拦下来 —— 自报现价与末价本来就该基本一致。"""
    prices = _prices_ending_on(date(2026, 9, 24), 5, 0.09)
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": prices}, self_price=f"${prices[-1][1]:,.2f}"),
        cache_dir=tmp_path,
    )
    assert result.available is True and result.failure is None


def test_source_without_a_quoted_price_is_not_second_guessed(tmp_path: Path):
    """stockanalysis 不给现价，无从自校 —— 不能因此判它可疑。"""
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_STOCKANALYSIS,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": _geometric_prices(date(2021, 9, 24), 5, 0.09)}, self_price=None),
        cache_dir=tmp_path,
    )
    assert result.available is True


def test_a_corrupt_cache_file_is_not_served(tmp_path: Path):
    """只在抓取时把关是不够的：一份坏数据只要落过盘，就会一直被端上来。
    缓存条目存下了写入时的自报现价，所以读的时候也能判。"""
    # 好数据：末价恰好 51.55（VFLO 的真实量级），自报现价也是 51.55
    base = _prices_ending_on(date(2026, 9, 24), 3, 0.2)
    good = [(d, c * (51.55 / base[-1][1])) for d, c in base]
    write_cache(
        CacheEntry(
            symbol="VFLO",
            source=datafeed.SOURCE_NASDAQ,
            asset_class="etf",
            last_bar_date=good[-1][0],
            prices=[(d, c * 1555.0) for d, c in good],  # 落盘的是量纲错了的那份
            dividends=[],
            name="VictoryShares Free Cash Flow ETF",
            self_price=51.55,  # 写入时说现价 51.55，历史却是 8 万
            fetched_at="2026-09-25T11:11:08+00:00",
        ),
        tmp_path,
    )
    # 缓存虽然是「新鲜」的，也不该被端上来
    result = get_quote(
        "VFLO",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(prices={"etf": good}, self_price="$51.55"),
        cache_dir=tmp_path,
    )
    assert result.available is True
    assert result.last_price == pytest.approx(51.55, abs=0.01)  # 用的是重抓回来的好数据
    assert result.from_cache is False


def test_legacy_cache_without_a_self_price_is_still_served(tmp_path: Path):
    """老缓存没有 self_price 字段 —— 无从判断就不判断，照常给它用。"""
    prices = _prices_ending_on(date(2026, 9, 24), 3, 0.2)
    entry = CacheEntry(
        symbol="SCHD",
        source=datafeed.SOURCE_NASDAQ,
        asset_class="etf",
        last_bar_date=prices[-1][0],
        prices=prices,
        dividends=[],
        fetched_at="2026-09-25T11:11:08+00:00",
    )
    assert entry.self_price is None
    write_cache(entry, tmp_path)
    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(failing=True),
        cache_dir=tmp_path,
    )
    assert result.available is True and result.from_cache is True


# ══ 回看窗口不够长时，如实回报实际跨度 ═════════════════════════════


def test_history_years_reports_the_window_actually_used():
    """VFLO 只有 3.26 年历史，请求 15 年也只能用它全部的数据。

    参数本身没错（本来就该「退回成立以来」），错的是**不说** ——
    界面会拿「回看 15 年」去描述一份只有 3 年的数据。
    """
    prices = _geometric_prices(date(2023, 6, 22), 3.26, 0.2)
    for requested in (10, 15):
        params = derive_params(prices, [], requested)
        assert params.lookback_years == requested  # 用户要的
        assert params.history_years == pytest.approx(3.26, abs=0.05)  # 实际用的


def test_history_years_matches_the_window_when_history_is_long_enough():
    prices = _geometric_prices(date(2006, 9, 25), 20, 0.08)
    params = derive_params(prices, [], 10)
    assert params.history_years == pytest.approx(10.0, abs=0.05)


# ══ 已宣告但还没派发的股息不能进 TTM ═══════════════════════════════


def test_derive_params_drops_dividends_dated_after_the_last_price_bar():
    """回归测试：数据源连**将来**的派息一起返回。

    实测 MAIN 的响应里有三笔（抓取当天 2026-09-25，回来的是 10-08 / 11-06 /
    12-08）。收进来的话，TTM 就把还没到账的钱算成了已到账 —— 股息率虚高，
    用来对比的跨度也被拉长。
    """
    prices = _prices_ending_on(date(2026, 9, 25), years=5, growth=0.05)
    paid = _quarterly(date(2025, 12, 24), 4, 0.25)  # 最近一年，已派发
    declared = [
        (date(2026, 10, 8), 10.0),  # 已宣告、还没派 —— 一笔巨额特别分红
        (date(2026, 11, 6), 10.0),
        (date(2026, 12, 8), 10.0),
    ]

    params = derive_params(prices, paid + declared, 10)

    # 只认已派发的 4 笔：1.00 / 100 附近的收盘价 ≈ 1%
    assert params.dividend_yield == pytest.approx(
        sum(a for _, a in paid) / prices[-1][1]
    )
    assert params.dividend_yield < 0.02  # 未过滤时会是 30% 上下


def test_derive_params_bounds_dividends_by_the_bar_not_by_today():
    """边界取最后一根 K 线，不取 `date.today()`。

    两个理由，这个测试卡的是第二个：
      1. 同一份缓存隔几天读，结果必须一样 —— 用今天过滤就会变；
      2. 抓取路径和读缓存路径都走 `derive_params`，边界收在这里两条都干净。
    所以「晚于最后一根 K 线」的派息一律不算，哪怕它早于今天。
    """
    prices = _prices_ending_on(date(2026, 9, 25), years=5, growth=0.05)
    paid = _quarterly(date(2025, 12, 24), 4, 0.25)
    after_the_bar = [(date(2026, 9, 26), 50.0)]  # 晚一天：早于今天，但晚于 K 线

    params = derive_params(prices, paid + after_the_bar, 10)

    assert params.dividend_yield == pytest.approx(
        sum(a for _, a in paid) / prices[-1][1]
    )


# ══ 测量区间不够长就不给股息增长率 ═════════════════════════════════


def test_derive_params_refuses_dividend_growth_from_a_short_window():
    """回归测试（VFLO）：派息记录够 3 年，**量增长的区间**不够。

    这组数据刻意造成 VFLO 的形状 —— 首末年都是半截，中间三年是完整的
    月度派息：

        派息记录  3.75 年   ← 看着够长
        测量区间  2.75 年   ← 真正拿去算增长的只有这么点

    差在哪儿：月度派息要攒够 12 笔才开得了一个窗口，最早那个窗口只能落在
    一年前。VFLO 那段区间正好是它的建仓爬坡期（首笔 $0.00285），拿爬坡
    起点去比现在，年化出 26.6% —— 那不是「股息涨得快」，是把爬坡外推 30 年。

    **门槛必须挂在测量区间上，不能挂在记录长度上** —— 挂错了这个用例就
    拦不住（记录 3.75 > 3，一路放行）。
    """
    prices = _geometric_prices(date(2022, 7, 15), 3.75, 0.2)
    dividends = (
        _year_of_payments(2022, count=12, amount=0.01)[6:]  # 2022 下半年 6 笔
        + _year_of_payments(2023, count=12, amount=0.02)
        + _year_of_payments(2024, count=12, amount=0.04)
        + _year_of_payments(2025, count=12, amount=0.08)
        + _year_of_payments(2026, count=12, amount=0.16)[:4]  # 2026 上半年 4 笔
    )
    history = (dividends[-1][0] - dividends[0][0]).days / 365.25
    assert history == pytest.approx(3.75, abs=0.05)  # 记录够长

    params = derive_params(prices, dividends, 10)

    assert params.dividend_growth_span_years == pytest.approx(2.75, abs=0.05)
    assert params.dividend_growth == 0.0  # 没有拿那 2.75 年去外推
    assert params.dividend_growth_insufficient_history is True


def test_derive_params_reports_dividend_growth_when_the_window_is_long_enough():
    """窗口够长就照常报 —— 门槛不能误伤有真实历史的标的。"""
    prices = _geometric_prices(date(2016, 1, 4), 10, 0.08)
    dividends = _years_of_payments(2018, 2025, count=12, base=0.25, growth=0.05)

    params = derive_params(prices, dividends, 10)

    assert params.dividend_growth_insufficient_history is False
    assert params.dividend_growth == pytest.approx(0.05, abs=1e-3)
    assert params.dividend_growth_span_years is not None
    assert params.dividend_growth_span_years >= 3.0


def test_derive_params_does_not_flag_a_non_payer():
    """不派息的标的**不该**被标记为「历史不足」。

    「不派息」和「派息史太短」是两件事：前者是标的的性质，不是缺陷，
    界面弹一句「历史不足，请留意」只会让人以为数据出了问题。
    """
    prices = _geometric_prices(date(2016, 1, 4), 10, 0.08)

    params = derive_params(prices, [], 10)

    assert params.dividend_yield == 0.0
    assert params.dividend_growth == 0.0
    assert params.dividend_growth_insufficient_history is False
    assert params.dividend_growth_span_years is None


def test_cached_symbol_as_name_is_upgraded_from_the_list(tmp_path: Path, universe):
    """早期缓存把名称回落成了代码本身，读到时就该顺手换成清单里那份。

    否则用户会一直看到「VOO」这种不像名字的名字，直到缓存自然过期 ——
    而他什么都没做错，不该为此等上一天。
    """
    universe(["VOO"])
    write_cache(_entry(symbol="VOO"), tmp_path)  # 这份缓存的 name 是 "SCHD"
    loaded = read_cache("VOO", datafeed.SOURCE_NASDAQ, tmp_path)
    assert loaded is not None
    loaded.name = "VOO"  # 模拟早期版本写下的「名字就是代码」
    write_cache(loaded, tmp_path)

    result = get_quote(
        "VOO",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(),
        cache_dir=tmp_path,
    )

    assert result.from_cache is True
    assert result.name == "Some Fund Name"
    assert result.name != "VOO"


def test_a_real_cached_name_is_left_alone(tmp_path: Path, universe):
    """清单里的名字只是兜底，盖不过数据源给的真名。"""
    universe(["SCHD"])
    write_cache(_entry(), tmp_path)  # name = "Schwab US Dividend Equity ETF"

    result = get_quote(
        "SCHD",
        source=datafeed.SOURCE_NASDAQ,
        now=FRIDAY_INTRADAY,
        transport=FakeSource(),
        cache_dir=tmp_path,
    )

    assert result.name == "Schwab US Dividend Equity ETF"


def test_name_for_symbol_reads_the_bundled_list(universe):
    universe(["SCHD", "NVDA"])
    assert datafeed.name_for_symbol("schd") == "Some Fund Name"
    assert datafeed.name_for_symbol("NOPE") is None


def test_name_for_symbol_is_none_without_a_list(universe):
    universe(None)
    assert datafeed.name_for_symbol("SCHD") is None

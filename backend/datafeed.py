"""datafeed.py —— 行情抓取与日缓存（SRS FR-005，第 5 期）。

════════════════════════════════════════════════════════════════════
本模块是**唯一**碰网络的模块之一（另一个是 fx.py）。它不参与计算：
抓回来 → 落缓存 → 提取参数，算数的部分一行都不在这里。
════════════════════════════════════════════════════════════════════

## 缓存策略（核心）

按**标的**缓存到 `data/cache/{SYMBOL}.json`，新鲜度判据是
「缓存里的最新 K 线日期 ≥ 最近一个已收盘的美股交易日」：

    最新 K 线已覆盖最近收完的那一天  → 直接读缓存，**一个请求都不发**
    否则                            → 只在这时才真的打 API

所以盘中反复改参数、来回切标的，都不会产生网络请求 —— 因为
「最近一个已收盘的交易日」在一天之内是个**常量**。

判据交给 `market_calendar`（纯计算、可单测），这里只负责 IO。
"""

from __future__ import annotations

import json
import math
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable

from backend.market_calendar import last_completed_trading_day
from backend.models import AssetParams, ParamSource, QuoteResult

# ══════════════════════════════════════════════════════════════════
# 常量
# ══════════════════════════════════════════════════════════════════

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"

# 美股代码清单快照（`scripts/build_symbol_universe.py` 生成）。
# 放在 backend/data/ 而不是外层 data/：它是**随代码走的静态资产**，不是
# 运行态缓存 —— 运行态的 data/cache、data/configs 都不入库，这份要入库。
SYMBOL_UNIVERSE = Path(__file__).resolve().parent / "data" / "symbols_us.txt"

# 一次抓满 15 年。这样用户把回看窗口从 10 年改成 15 年时**不需要再抓**
# —— 缓存本来就有。请求数一样是一次，差别只是响应体大一点。
MAX_YEARS = 15

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
# ⚠️ 下面三个数是**一起**定的，不能各调各的：
#
#     最坏等待 ≈ len(_ASSET_CLASSES) × _RETRIES × _TIMEOUT
#              = 2 × 2 × 12 ≈ 48 秒
#
# 加了重试就必须把单次超时压下来，否则最坏情况直接翻倍 ——
# 用户盯着转圈等 100 秒，比不等还糟。改任何一个数前先算这条式子。
_TIMEOUT = 12

# 数据传输层重试。数据源偶发超时很常见，而**代价极不对称**：
# 多试一次的代价是几秒，放弃的代价是让用户看到「可能是代码写错」，
# 于是他去翻自己的代码、换标的，而其实什么都没错。
_RETRIES = 2
_RETRY_BACKOFF = 0.6


class Unreachable(Exception):
    """传输层没拿到任何响应（超时、连不上、DNS 失败）。

    必须和「连上了，但那儿没有这个标的」分开。两者都表现为「拿不到价格」，
    但对用户的含义正好相反：前者该怪网络、该重试，后者才是代码写错。
    早先两者共用一个 None，结果网络一抖就去指控用户填错了代码。
    """


class Unavailable(Exception):
    """数据源**明确答复**「没有这个标的」。

    与 `Unreachable` 的区别是「有没有收到答复」，不是「拿没拿到数据」。
    stockanalysis 对不存在的代码回 404，那就是一句答复 —— 不能当成网络故障
    去重试，否则用户输错一个字母要白等两轮超时，最后还被告知「网络不可达」。
    """

_NASDAQ_PRICES = (
    "https://api.nasdaq.com/api/quote/{symbol}/historical"
    "?assetclass={ac}&fromdate={start}&todate={end}&limit=9999"
)
_NASDAQ_INFO = "https://api.nasdaq.com/api/quote/{symbol}/info?assetclass={ac}"
_NASDAQ_SEARCH = "https://api.nasdaq.com/api/autocomplete/slookup/{limit}?search={q}"
_SA_DIVIDENDS = "https://stockanalysis.com/api/symbol/{prefix}/{symbol}/dividend"
_SA_HISTORY = "https://stockanalysis.com/api/symbol/{prefix}/{symbol}/history?type=chart"

# ── 两个价格来源 ────────────────────────────────────────────────
#
# 实测（本机，2026-09）：stockanalysis ≈ 1.2 秒且稳定；Nasdaq ≈ 50 秒，
# 一半的请求直接超时，连 QQQ / SPY 这种最常见的代码都会挂。
#
# 所以前端**并行**请求两者：谁先回谁先渲染，Nasdaq 后到且成功就覆盖。
# 用户立刻看到数（来自 stockanalysis），稍后自动换成 Nasdaq 那份。
#
# 为什么还要留着慢的：stockanalysis 的历史端点是他家官网渲染图表自用的
# **未公开接口**，没有稳定性承诺。Nasdaq 的数据结构更规范，能拿到就用它。
SOURCE_STOCKANALYSIS = "stockanalysis"
SOURCE_NASDAQ = "nasdaq"
SOURCES = (SOURCE_STOCKANALYSIS, SOURCE_NASDAQ)

# 展示优先级：数字大的覆盖数字小的。Nasdaq 后到要能盖掉 stockanalysis，
# 反过来则不行 —— 否则慢的那份回来时会把已经显示的好数据换掉。
_SOURCE_RANK = {SOURCE_STOCKANALYSIS: 1, SOURCE_NASDAQ: 2}

# 数据源对 ETF 与个股用不同的 assetclass 取值。
_ASSET_CLASSES = ("etf", "stocks")
_PREFIX = {"etf": "e", "stocks": "s"}

# 「历史末价」与「该源自报的现价」允许差多少。超出即判这个源前后矛盾、不可信。
#
# 实测踩到的坑：Nasdaq 给 VFLO 的历史序列末价是 80243.348，而**同一时刻它自己的
# info 接口**说现价 $51.55 —— 相差 1556 倍。这条坏数据经 SOURCE_RANK 覆盖掉了
# stockanalysis 的正确值（$51.61），于是「当前股价 80243」一路把股息率压成 0.00%、
# 把股价年增长算成 23%，期末总值从 $2.3M 变成 $61.7M。
#
# 25% 是给「最后一根 K 线是上一交易日收盘、而现价是盘中」留的余量 ——
# 正常波动远到不了这个数。
PRICE_SANITY_TOLERANCE = 0.25


# ══════════════════════════════════════════════════════════════════
# 传输层 —— 可注入，测试时换成假的，一个网络包都不发
# ══════════════════════════════════════════════════════════════════

Transport = Callable[[str], str]


def _http_get(url: str) -> str:
    """默认传输：stdlib urllib。刻意不引入 requests / httpx 这类运行时依赖。"""
    request = urllib.request.Request(
        url, headers={"User-Agent": _UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return response.read().decode("utf-8", errors="replace")


def _sleep(seconds: float) -> None:
    """退避用。抽成函数是为了让测试能把它换掉，不必真等。"""
    time.sleep(seconds)


def _try_transport(
    transport: Transport, url: str, *, not_found_codes: tuple[int, ...] = ()
) -> str | None:
    """抓一次；失败先重试，仍失败返回 None —— 抓不到不是崩溃，是要**降级**。

    `not_found_codes` 里的状态码**不算失败**，而是「数据源明确说没这个标的」，
    直接抛 `Unavailable`，不重试也不计入网络故障。默认空着，因为对 Nasdaq
    来说 404 也可能只是接口抽风；哪个端点该这么解读由调用方声明。
    """
    for attempt in range(_RETRIES):
        try:
            return transport(url)
        except urllib.error.HTTPError as exc:
            # HTTPError 是 URLError 的子类，必须先接住
            if exc.code in not_found_codes:
                raise Unavailable(url) from exc
            if attempt + 1 < _RETRIES:
                _sleep(_RETRY_BACKOFF * (attempt + 1))
        except (urllib.error.URLError, OSError, ValueError):
            if attempt + 1 < _RETRIES:
                _sleep(_RETRY_BACKOFF * (attempt + 1))
    return None


# ══════════════════════════════════════════════════════════════════
# 缓存
# ══════════════════════════════════════════════════════════════════


@dataclass
class CacheEntry:
    """一个标的**在某个来源上**的缓存内容。"""

    symbol: str
    source: str
    asset_class: str
    last_bar_date: date
    prices: list[tuple[date, float]]
    dividends: list[tuple[date, float]]
    name: str | None = None
    expense_ratio: float | None = None
    fetched_at: str | None = None
    self_price: float | None = None
    """写入时**这个来源自己报的现价**（Nasdaq 的 info 接口有，stockanalysis 没有）。

    存下来是为了让「这份历史数据可不可信」这件事**在读缓存时也能判** ——
    否则一份坏数据只要落过盘，就会一直被端上来，直到它自然过期为止。
    """

    def to_json(self) -> dict:
        return {
            "symbol": self.symbol,
            "source": self.source,
            "asset_class": self.asset_class,
            "last_bar_date": self.last_bar_date.isoformat(),
            "fetched_at": self.fetched_at,
            "name": self.name,
            # ⚠️ 只作展示。A23：ETF 管理费已从基金资产每日扣除，
            #    已经体现在历史价格里，**绝不能**再进计算。
            "expense_ratio": self.expense_ratio,
            "self_price": self.self_price,
            "prices": [[d.isoformat(), c] for d, c in self.prices],
            "dividends": [[d.isoformat(), a] for d, a in self.dividends],
        }

    @classmethod
    def from_json(cls, raw: dict) -> CacheEntry:
        return cls(
            symbol=raw["symbol"],
            source=raw.get("source", SOURCE_NASDAQ),
            asset_class=raw["asset_class"],
            last_bar_date=date.fromisoformat(raw["last_bar_date"]),
            prices=[(date.fromisoformat(d), float(c)) for d, c in raw["prices"]],
            dividends=[(date.fromisoformat(d), float(a)) for d, a in raw["dividends"]],
            name=raw.get("name"),
            expense_ratio=raw.get("expense_ratio"),
            fetched_at=raw.get("fetched_at"),
            # 老缓存没有这个字段 —— 得到 None，校验时跳过，等它自然过期。
            self_price=raw.get("self_price"),
        )

    def is_fresh_for(self, wanted: date) -> bool:
        """缓存是否已覆盖 `wanted`（最近一个已收盘交易日）。"""
        return self.last_bar_date >= wanted

    def is_self_consistent(self) -> bool:
        """历史末价与该源自报的现价是否对得上。

        对不上说明**这个来源自己前后矛盾** —— 这时不该猜哪个数对，两个都不用。
        没有自报现价（stockanalysis，或老缓存）时无从判断，一律放行。
        """
        if self.self_price is None or not self.prices:
            return True
        return _prices_agree(self.prices[-1][1], self.self_price)


def _cache_path(symbol: str, source: str, cache_dir: Path) -> Path:
    """缓存文件路径。**按来源分开存** —— 两个来源都要留着。

    只存一份的话就得挑一个丢一个：要么丢了 stockanalysis 那份快数据
    （下次输入又得等），要么丢了 Nasdaq 那份好数据（永远换不成）。

    标的代码先过一遍白名单 —— 它来自用户输入，会被拼进文件路径。
    不走白名单的话 `../../etc/passwd` 这种就是一发路径穿越。
    """
    safe = "".join(ch for ch in symbol.upper() if ch.isalnum() or ch in ".-")
    if not safe or len(safe) > 12:
        raise ValueError(f"标的代码不合法：{symbol!r}")
    return cache_dir / f"{safe}.{source}.json"


def read_cache(
    symbol: str, source: str, cache_dir: Path | None = None
) -> CacheEntry | None:
    path = _cache_path(symbol, source, cache_dir or CACHE_DIR)
    if not path.is_file():
        return None
    try:
        return CacheEntry.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, ValueError, OSError):
        # 缓存坏了不是错误，只是「没有缓存」—— 重新抓一次就好。
        return None


def write_cache(entry: CacheEntry, cache_dir: Path | None = None) -> None:
    directory = cache_dir or CACHE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    path = _cache_path(entry.symbol, entry.source, directory)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(
        json.dumps(entry.to_json(), ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    tmp.replace(path)  # 原子替换，避免半个文件被读到


# ══════════════════════════════════════════════════════════════════
# 代码清单（离线快照）
# ══════════════════════════════════════════════════════════════════


@lru_cache(maxsize=1)
def _load_universe() -> dict[str, str] | None:
    """读 `data/symbols_us.txt` → `{代码: 名称}`。**只读一次**，之后是内存查表。

    文件不存在就返回 None，而**不是**空字典 —— 两者含义完全不同：空的
    等于「所有代码都不存在」，会把每个用户都骂一遍。None 才是「无从判断」。

    名称一起读进来是有用的：快路径**故意不调 Nasdaq 的 info 接口**（那个
    要几十秒），于是没有名字可填。清单里的名称正好补上这一块，且零网络开销。

    重新生成见 `scripts/build_symbol_universe.py`。
    """
    if not SYMBOL_UNIVERSE.is_file():
        return None
    try:
        text = SYMBOL_UNIVERSE.read_text(encoding="utf-8")
    except OSError:
        return None

    universe: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):  # 前几行是给人看的元信息
            continue
        symbol, _, name = line.partition("\t")
        symbol = symbol.strip().upper()
        if symbol:
            universe[symbol] = name.strip() or symbol
    return universe or None


def is_known_symbol(symbol: str) -> bool | None:
    """这个代码在不在离线清单里。None = 没有清单，无从判断。

    判据刻意是**软**的：清单是快照，会过时，新上市的代码必然不在里面。
    所以调用方只该拿它出一句「可能不存在，请确认」的提示，绝不能据此
    拒绝用户 —— 手输参数的通道永远开着。
    """
    universe = _load_universe()
    if universe is None:
        return None
    return symbol.strip().upper() in universe


def name_for_symbol(symbol: str) -> str | None:
    """从离线清单里取标的名称。没有清单、或清单里没有，都返回 None。"""
    universe = _load_universe()
    if universe is None:
        return None
    return universe.get(symbol.strip().upper())


# ══════════════════════════════════════════════════════════════════
# 抓取：价格（Nasdaq）
# ══════════════════════════════════════════════════════════════════


def _parse_money(text: str | None) -> float | None:
    """'$33.10' / '1,234.5' / '0.06%' → float。取不到返回 None。"""
    if not text:
        return None
    cleaned = text.strip().replace("$", "").replace(",", "").replace("%", "")
    if cleaned in ("", "N/A", "n/a", "--"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _prices_agree(a: float, b: float) -> bool:
    """两个价格是不是同一个量级。用**相对差**而不是绝对差 —— 要判的是
    「相差 1500 倍」这种事，而 $700 的 VOO 与 $30 的 SCHD 各自都该自洽。"""
    if a <= 0 or b <= 0:
        return False
    return abs(a - b) / b <= PRICE_SANITY_TOLERANCE


def _fetch_prices_nasdaq(
    symbol: str, asset_class: str, years: int, transport: Transport, today: date
) -> list[tuple[date, float]] | None:
    """Nasdaq 的日线收盘价。

    返回 None 表示「数据源答了，但这里没有这个标的」；
    抛 `Unreachable` 表示「压根没连上」。调用方需要区别对待这两件事。
    """
    end = today
    start = today - timedelta(days=int(years * 365.25) + 5)
    url = _NASDAQ_PRICES.format(
        symbol=symbol.upper(),
        ac=asset_class,
        start=start.isoformat(),
        end=end.isoformat(),
    )
    raw = _try_transport(transport, url)
    if raw is None:
        raise Unreachable(url)

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None

    rows = (((payload.get("data") or {}).get("tradesTable") or {}).get("rows")) or []
    if not rows:
        return None

    prices: list[tuple[date, float]] = []
    for row in rows:
        close = _parse_money(row.get("close"))
        stamp = row.get("date")
        if close is None or close <= 0 or not stamp:
            continue
        # Nasdaq 给的是 MM/DD/YYYY
        try:
            month, day, year = (int(p) for p in stamp.split("/"))
            prices.append((date(year, month, day), close))
        except (ValueError, TypeError):
            continue

    prices.sort(key=lambda p: p[0])
    return prices or None


def _fetch_prices_sa(
    symbol: str, asset_class: str, years: int, transport: Transport, today: date
) -> list[tuple[date, float]] | None:
    """stockanalysis.com 的日线收盘价 —— **快路径**，实测 1.2 秒。

    与 Nasdaq 那个的区别只在解析：这里给的是 `[毫秒时间戳, 收盘价]` 数组，
    而不是 `tradesTable.rows` 那种带表头的对象。时间戳按 **UTC** 折算日期，
    不能按本地时区 —— 本机在 UTC+8，用本地时区会把美东收盘那一刻算到
    第二天去，最新一根 K 线的日期就比实际晚一天，缓存新鲜度跟着判错。

    这是他家官网渲染图表自用的**未公开接口**，没有稳定性承诺。所以它只是
    快路径：解析不出东西一律当「这儿没有」，由 Nasdaq 那份兜底。
    """
    prefix = _PREFIX.get(asset_class, "e")
    url = _SA_HISTORY.format(prefix=prefix, symbol=symbol.lower())
    try:
        # 404 是答复（「没这个代码」），不是故障 —— 实测 e/ZZZZ 回 200 空表、
        # e/ZZZZZZ 回 404，两种都表示查无此标的。
        raw = _try_transport(transport, url, not_found_codes=(404,))
    except Unavailable:
        return None
    if raw is None:
        raise Unreachable(url)

    try:
        rows = json.loads(raw).get("data") or []
    except json.JSONDecodeError:
        return None

    cutoff = today - timedelta(days=int(years * 365.25) + 5)
    prices: list[tuple[date, float]] = []
    for row in rows:
        if not isinstance(row, (list, tuple)) or len(row) < 2:
            continue
        try:
            when = datetime.fromtimestamp(row[0] / 1000, timezone.utc).date()
            close = float(row[1])
        except (TypeError, ValueError, OSError, OverflowError):
            continue
        if close <= 0 or when > today or when < cutoff:
            continue
        prices.append((when, close))

    prices.sort(key=lambda p: p[0])
    return prices or None


# 每个来源各自的取价函数。加来源就是在这张表里加一行，其余逻辑不必动。
_PRICE_FETCHERS: dict[str, Callable[..., list[tuple[date, float]] | None]] = {
    SOURCE_STOCKANALYSIS: _fetch_prices_sa,
    SOURCE_NASDAQ: _fetch_prices_nasdaq,
}


def _fetch_info(
    symbol: str, asset_class: str, transport: Transport
) -> tuple[str | None, float | None, float | None]:
    """标的全名、管理费、**以及该源自报的现价**。

    管理费**仅供展示**（A23：不参与计算）。

    现价是**校验用**的：这个接口和取历史价的那个是同一个数据源，它俩说的价格
    必须对得上。对不上就说明这个源在自相矛盾，那份历史数据不能信 —— 见
    `PRICE_SANITY_TOLERANCE`。这个字段本来就在响应里，白拿，不额外发请求。
    """
    raw = _try_transport(
        transport, _NASDAQ_INFO.format(symbol=symbol.upper(), ac=asset_class)
    )
    if raw is None:
        return None, None, None
    try:
        data = json.loads(raw).get("data") or {}
    except json.JSONDecodeError:
        return None, None, None

    name = (data.get("companyName") or "").strip() or None
    primary = data.get("primaryData") or {}
    expense_ratio = _parse_money(primary.get("expenseRatio"))
    # primaryData.expenseRatio 给的是百分数（0.06 表示 0.06%），换成小数
    if expense_ratio is not None:
        expense_ratio /= 100.0
    return name, expense_ratio, _parse_money(primary.get("lastSalePrice"))


# ══════════════════════════════════════════════════════════════════
# 抓取：股息（stockanalysis.com）
# ══════════════════════════════════════════════════════════════════


def _fetch_dividends(
    symbol: str, asset_class: str, transport: Transport
) -> list[tuple[date, float]]:
    """取派息历史。失败返回空列表 —— 股息数据缺失不该让整个抓取失败。

    数据源对 ETF 的派息历史覆盖不如个股完整，但收益率的**滚动 12 个月
    合计**通常仍可取到。真取不到时股息率会算成 0，界面上那个字段就是
    空的，用户手填即可 —— 不会静默给一个错误的数。
    """
    prefix = _PREFIX.get(asset_class, "e")
    url = _SA_DIVIDENDS.format(prefix=prefix, symbol=symbol.lower())
    raw = _try_transport(transport, url)
    if raw is None:
        return []

    try:
        data = json.loads(raw).get("data") or {}
    except json.JSONDecodeError:
        return []

    rows = data.get("history") or []
    out: list[tuple[date, float]] = []
    for row in rows:
        amount = _parse_money(row.get("amt"))
        stamp = row.get("dt")
        if amount is None or amount <= 0 or not stamp:
            continue
        try:
            out.append((date.fromisoformat(stamp), amount))
        except ValueError:
            continue

    out.sort(key=lambda p: p[0])
    return out


# ══════════════════════════════════════════════════════════════════
# 参数提取 —— 从历史序列反推 AssetParams
# ══════════════════════════════════════════════════════════════════


def _annualised_price_growth(prices: list[tuple[date, float]]) -> float:
    """价格年化增长：对 ln(价格) 做最小二乘回归，取斜率。

    为什么不用「首尾两点」直接算 CAGR：那个数**完全由两个端点决定**，
    端点撞上一个高点或低点，整条曲线就被带偏。回归用上了每一个交易日，
    对端点噪声稳健得多。

    一个干净的检验：价格若严格按年化 g 增长，回归斜率恰好是 ln(1+g)，
    还原回来正好等于 g（见 test_datafeed）。
    """
    if len(prices) < 2:
        return 0.0

    origin = prices[0][0]
    xs = [(d - origin).days / 365.25 for d, _ in prices]
    ys = [math.log(c) for _, c in prices]

    n = len(xs)
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    var_x = sum((x - mean_x) ** 2 for x in xs)
    if var_x <= 0:
        return 0.0

    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys)) / var_x
    return math.exp(slope) - 1.0


def _payments_per_year(dates: list[date]) -> int:
    """从派息日期的间隔推断每年派几次。

    取间隔的**中位数**而不是平均值：偶有一次特别分红或跳过一次派息，
    平均值会被带偏，中位数不会。
    """
    if len(dates) < 2:
        return 1
    gaps = sorted((b - a).days for a, b in zip(dates, dates[1:]))
    median = gaps[len(gaps) // 2]
    if median <= 0:
        return 1
    return max(1, min(12, round(365.25 / median)))


def _trailing_annual_series(
    dividends: list[tuple[date, float]], per_year: int
) -> list[tuple[date, float]]:
    """每个派息日给出「截至该日的最近 N 次派息合计」（N = 每年派息次数）。

    为什么不用「过去 365 天」这种日历窗口：季度派息的间隔并不正好是
    91.31 天，窗口边界会随机地圈进 4 次或 **5 次**派息。实测 SCHD 的
    2025-09-24 与 2026-09-23 相隔 364 天，于是同一个 365 天窗口里挤进了
    5 笔 —— 股息率会凭空虚高约 25%，增长率更是彻底失真。

    按「最近 N 次」而不是「最近 365 天」，则每次都是完整的一个年度，
    与派息日的漂移无关。
    """
    out: list[tuple[date, float]] = []
    for i in range(len(dividends)):
        window = dividends[max(0, i - per_year + 1) : i + 1]
        out.append((dividends[i][0], sum(amount for _, amount in window)))
    return out


def _annualised_dividend_growth(
    dividends: list[tuple[date, float]], max_years: int
) -> float:
    """股息年化增长：比较两个**完整**年度的派息合计。

    只在窗口完整时才取用（索引 ≥ per_year−1，即已经攒够一整年的派息）。
    股息历史常比价格历史短得多 —— 缓存里 SCHD 只有约 5 年 —— 所以取
    **实际可用的最长完整跨度**，而不是硬凑回看窗口；凑不满就少算几年，
    总好过拿半截窗口去比、把增长率算高。
    """
    if not dividends:
        return 0.0

    per_year = _payments_per_year([d for d, _ in dividends])
    # 至少要 per_year + 1 笔，否则连两个完整年度都凑不出来
    if len(dividends) < per_year + 1:
        return 0.0

    series = _trailing_annual_series(dividends, per_year)
    latest_date, latest_value = series[-1]
    if latest_value <= 0:
        return 0.0

    best: tuple[float, float] | None = None  # (years, value)
    for index in range(per_year - 1, len(series) - 1):
        when, value = series[index]
        if value <= 0:
            continue
        years = (latest_date - when).days / 365.25
        if not (1.0 <= years <= max_years):
            continue
        # 越远越好 —— 跨度越长，增长率越稳
        if best is None or years > best[0]:
            best = (years, value)

    if best is None:
        return 0.0

    years, earlier = best
    return (latest_value / earlier) ** (1.0 / years) - 1.0


def derive_params(
    prices: list[tuple[date, float]],
    dividends: list[tuple[date, float]],
    lookback_years: int,
) -> AssetParams:
    """从历史序列提取参数（FR-005）。

    回看窗口只作用于**价格增长**（价格历史够长）；股息历史通常短得多，
    取实际可用的最长跨度。

    **历史不够长就用成立以来的全部数据**，并把实际跨度如实回报给
    `history_years` —— 新上市的标的凑不满窗口，如果不说，界面就会拿
    「回看 15 年」去描述一份只有 3 年的数据，那是在误导人。

    `expense_ratio` 恒为 0：抓取模式下历史价格已含管理费（A23）。
    """
    if not prices:
        raise ValueError("没有价格数据，无法提取参数")

    window_start = prices[-1][0] - timedelta(days=int(lookback_years * 365.25) + 5)
    window = [(d, c) for d, c in prices if d >= window_start] or prices
    history_years = (window[-1][0] - window[0][0]).days / 365.25

    latest_close = prices[-1][1]
    if dividends:
        per_year = _payments_per_year([d for d, _ in dividends])
        trailing = _trailing_annual_series(dividends, per_year)[-1][1]
    else:
        trailing = 0.0
    yield_ = trailing / latest_close if latest_close > 0 else 0.0

    return AssetParams(
        price_growth=_annualised_price_growth(window),
        dividend_yield=max(0.0, yield_),
        dividend_growth=_annualised_dividend_growth(dividends, lookback_years),
        expense_ratio=0.0,  # A23：抓取模式强制为 0，由模型校验兜底
        source=ParamSource.FETCHED,
        lookback_years=lookback_years,
        history_years=history_years,
    )


# ══════════════════════════════════════════════════════════════════
# 对外入口
# ══════════════════════════════════════════════════════════════════


def _resolve_asset_class(
    symbol: str, source: str, transport: Transport, today: date
) -> tuple[str, list[tuple[date, float]]] | None:
    """ETF 与个股走不同的 assetclass 参数，挨个试。

    先试 etf —— 本项目面向的正是 ETF 投资者，命中率更高，常见情况下
    第一个就中，不会多打一次请求。

    返回 None 表示「数据源答了，但没有这个标的」；两个 assetclass 都**一个
    字节都没收到**才抛 `Unreachable`。后者是用来把话说准的：一次响应都没有
    时，我们根本无从判断代码对错，不该去指控用户填错了代码。

    注意这里**即使第一次不可达也照样试第二次**：某个 assetclass 的端点单独
    抽风是可能的，为省一次超时就把本来能成功的抓取变成失败，不划算。
    """
    fetch = _PRICE_FETCHERS[source]
    unreachable = 0
    answered = False
    for asset_class in _ASSET_CLASSES:
        try:
            prices = fetch(symbol, asset_class, MAX_YEARS, transport, today)
        except Unreachable:
            unreachable += 1
            continue
        answered = True
        if prices:
            return asset_class, prices
    if not answered and unreachable:
        raise Unreachable(symbol)
    return None


def _display_name(entry: CacheEntry) -> str:
    """缓存里的名称，必要时用离线清单**升级**一下。

    早期快路径没有名字可填，就回落成了代码本身；那些缓存现在还躺在磁盘上。
    与其让用户看到「VOO」这种不像名字的名字一直显示到缓存自然过期，
    不如在这里顺手换成清单里那份 —— 零成本，且用户什么都不用做。
    """
    if entry.name and entry.name != entry.symbol:
        return entry.name
    return name_for_symbol(entry.symbol) or entry.name or entry.symbol


def _from_cache(
    entry: CacheEntry,
    lookback_years: int,
    *,
    known: bool | None,
    reason: str | None = None,
) -> QuoteResult:
    """用缓存内容组装结果（命中新鲜缓存、以及抓取失败降级，都走这里）。"""
    return QuoteResult(
        symbol=entry.symbol,
        available=True,
        name=_display_name(entry),
        asset_class=entry.asset_class,
        last_price=entry.prices[-1][1],
        params=derive_params(entry.prices, entry.dividends, lookback_years),
        as_of=entry.last_bar_date.isoformat(),
        fetched_at=entry.fetched_at,
        from_cache=True,
        source=entry.source,
        known=known,
        expense_ratio_info=entry.expense_ratio,
        lookback_years=lookback_years,
        reason=reason,
    )


def _unavailable(
    symbol: str, reason: str, known: bool | None, source: str, failure: str
) -> QuoteResult:
    return QuoteResult(
        symbol=symbol,
        available=False,
        reason=reason,
        known=known,
        source=source,
        failure=failure,
    )


def get_quote(
    symbol: str,
    lookback_years: int = 10,
    *,
    source: str = SOURCE_STOCKANALYSIS,
    now: datetime | None = None,
    transport: Transport | None = None,
    cache_dir: Path | None = None,
    force: bool = False,
) -> QuoteResult:
    """取标的参数。**命中新鲜缓存时一个网络请求都不发。**

    一次只负责**一个来源**。前端并行请求两个来源：stockanalysis 先回、立刻
    渲染，Nasdaq 后到且成功就覆盖。这样慢的那个再慢也不挡路，而快的那份
    哪怕接口明天就没了，也只是退回「只有 Nasdaq」而已。

    参数全部可注入，是为了让测试能在不打网络、不碰系统时间的前提下
    把「缓存命中 / 缓存过期 / 抓取失败」三条路径都走一遍。
    """
    symbol = symbol.strip().upper()
    if not symbol:
        return QuoteResult(symbol="", available=False, reason="标的代码为空")
    if source not in _PRICE_FETCHERS:
        return QuoteResult(
            symbol=symbol,
            available=False,
            reason=f"未知数据源：{source}",
            source=source,
            failure="bad_source",
        )

    now = now or datetime.now(timezone.utc)
    transport = transport or _http_get
    wanted = last_completed_trading_day(now)
    # 只在**代码确实不在清单里**时才为 False；没有清单时是 None，界面不提示。
    known = is_known_symbol(symbol)

    # ── ① 先看缓存 ──────────────────────────────────────────────
    cached = read_cache(symbol, source, cache_dir)
    # 缓存也要过一遍自洽校验。只在**抓取**时把关是不够的：一份坏数据只要落过盘，
    # 就会一直被端上来，直到它自然过期为止 —— 而「新鲜」的判定是按交易日的，
    # 也就是说用户会整整一天都看着那个错数，还没法靠刷新绕开。
    if cached and not cached.is_self_consistent():
        cached = None
    if cached and not force and cached.is_fresh_for(wanted):
        return _from_cache(cached, lookback_years, known=known)

    # ── ② 缓存过期或不存在，这才真的去打 API ────────────────────
    try:
        resolved = _resolve_asset_class(symbol, source, transport, now.date())
    except Unreachable:
        # 抓不到就退回旧缓存（哪怕有点旧），总好过什么都没有 ——
        # 但必须**说明这是旧的**，不能装作是最新的。
        if cached:
            return _from_cache(
                cached, lookback_years, known=known, reason="数据源暂时不可达，以下为缓存中的数据"
            )
        # 说清楚是**网络**的问题。原先这里和「查无此标的」共用一句话，
        # 于是数据源一抖，界面就去指控用户填错了代码 —— 用户会真的去改，
        # 而代码本来是对的。冤枉人比不解释更糟。
        return _unavailable(
            symbol,
            "数据源暂时不可达（已重试）。这不代表代码有问题 —— 请稍后重试，或先手输参数。",
            known,
            source,
            "unreachable",
        )
    if resolved is None:
        # 数据源确实答了，只是没有这个标的 —— 这时候才该怀疑代码。
        return _unavailable(
            symbol,
            "数据源查不到这个代码。请确认代码是否正确（美股 / ETF），或手输参数。",
            known,
            source,
            "unknown_symbol",
        )

    asset_class, prices = resolved
    dividends = _fetch_dividends(symbol, asset_class, transport)

    if source == SOURCE_NASDAQ:
        # 股息只有一个来源（stockanalysis），没有「Nasdaq 的股息」这回事。
        # 所以这次股息没抓到就直接沿用 stockanalysis 缓存里的那份 ——
        # 否则 Nasdaq 一覆盖，界面上已经显示对的股息率会突然变成 0，
        # 「换个更好的源」反而把数据换坏了。
        if not dividends:
            sa_cached = read_cache(symbol, SOURCE_STOCKANALYSIS, cache_dir)
            if sa_cached:
                dividends = sa_cached.dividends
        # 名称与管理费同样出自 Nasdaq 的 info 接口，慢路径才有。
        name, expense_ratio, self_price = _fetch_info(symbol, asset_class, transport)
    else:
        # 快路径**故意不碰 Nasdaq** —— 那个 info 接口实测要几十秒，
        # 调它就把整条快路径拖成慢的，白白毁掉先渲染的意义。
        # 名称改从离线清单里取：零网络开销，且清单里本来就是这个名字。
        name, expense_ratio, self_price = name_for_symbol(symbol), None, None

    # ── 自洽校验：这个源给的历史价，和它自己报的现价，对得上吗？ ──
    #
    # 只需要**同一个源的两份响应**就能判定，不必去猜哪个源更可信 ——
    # 自相矛盾的那一份直接扔掉。实测 Nasdaq 给 VFLO 回的历史末价是 80243，
    # 而它自己的 info 接口说现价 51.55，相差 1556 倍。
    if self_price is not None and not _prices_agree(prices[-1][1], self_price):
        # **不写缓存** —— 坏数据一旦落盘，就得靠它自然过期才能清掉。
        return _unavailable(
            symbol,
            f"数据源返回的历史价格（{prices[-1][1]:,.2f}）与它自己报的现价"
            f"（{self_price:,.2f}）相差过大，本次数据不采用。",
            known,
            source,
            "bad_data",
        )

    entry = CacheEntry(
        symbol=symbol,
        source=source,
        asset_class=asset_class,
        last_bar_date=prices[-1][0],
        prices=prices,
        dividends=dividends,
        name=name or symbol,
        expense_ratio=expense_ratio,
        fetched_at=now.isoformat(timespec="seconds"),
        self_price=self_price,
    )
    write_cache(entry, cache_dir)

    return QuoteResult(
        symbol=symbol,
        available=True,
        name=entry.name,
        asset_class=asset_class,
        last_price=prices[-1][1],
        params=derive_params(prices, dividends, lookback_years),
        as_of=entry.last_bar_date.isoformat(),
        fetched_at=entry.fetched_at,
        from_cache=False,
        source=source,
        known=known,
        expense_ratio_info=expense_ratio,
        lookback_years=lookback_years,
        reason=None,
    )


def search_symbols(query: str, limit: int = 10, *, transport: Transport | None = None) -> list[dict]:
    """按关键字找标的（FR-002 的可选辅助）。抓不到就返回空列表。"""
    query = query.strip()
    if not query:
        return []

    transport = transport or _http_get
    raw = _try_transport(transport, _NASDAQ_SEARCH.format(limit=limit, q=query))
    if raw is None:
        return []

    try:
        rows = (json.loads(raw).get("data")) or []
    except json.JSONDecodeError:
        return []

    results = []
    for row in rows[:limit]:
        symbol = (row.get("symbol") or "").strip()
        if not symbol:
            continue
        results.append(
            {
                "symbol": symbol,
                "name": row.get("name") or "",
                "asset": (row.get("asset") or "").upper(),
            }
        )
    return results

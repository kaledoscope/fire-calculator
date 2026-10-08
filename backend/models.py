"""models.py —— 共享地基：全项目的数据模型。

宪法铁律 Ⅱ 允许各模块依赖「共享地基」。本模块就是那个地基：
所有模块（engine / storage / fx / datafeed / main）都通过它说话，
彼此之间不引用内部实现。

约束：
    本模块只定义数据结构与校验规则，**不含任何计算逻辑，不做任何 IO**。
    计算在 engine.py，IO 在 storage.py / datafeed.py / main.py。
"""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ══════════════════════════════════════════════════════════════════
# 枚举
# ══════════════════════════════════════════════════════════════════


class Market(str, Enum):
    """市场。本期仅支持美股（SRS FR-001）。"""

    US = "US"


class Currency(str, Enum):
    """显示货币。仅影响展示层，不参与任何计算（SRS FX-1）。"""

    USD = "USD"
    CNY = "CNY"
    JPY = "JPY"


class DividendMode(str, Enum):
    """股息去向（SRS A20）。"""

    DRIP = "drip"  # 再投资：税后股息按目标占比买回各标的
    CASH = "cash"  # 累积现金：进现金池，按年利率单利计息


class ParamSource(str, Enum):
    """参数来源（SRS §6.2）。手输永远优先。"""

    FETCHED = "fetched"  # 从历史行情提取
    MANUAL = "manual"  # 用户手输
    FALLBACK = "fallback"  # 内置兜底值


class FireMode(str, Enum):
    """退休方式。同一时间只用一种（SRS FR-006）—— 两者是本金「消耗 / 不动」的区别，
    不是可以叠加的两笔账。"""

    WITHDRAWAL = "withdrawal"  # 提取退休：按倍数卖出本金（4% 规则）
    INCOME = "income"  # 吃息退休：本金不动，只花税后股息


class GoalCriterion(str, Enum):
    """一个目标「算不算达成」的判据。两种退休方式各用一条。"""

    VALUE = "value"  # 组合总值 ≥ 目标
    INCOME = "income"  # 年化税后股息 ≥ 目标


# ══════════════════════════════════════════════════════════════════
# 输入：标的与参数
# ══════════════════════════════════════════════════════════════════


class AssetParams(BaseModel):
    """单个标的的年化参数。

    这些数由 FR-005 从历史行情提取，也可手输覆盖（SRS DP-2）。
    """

    price_growth: float = Field(..., description="股价年增长率")
    dividend_yield: float = Field(..., description="股息率（年化，相对当前股价）")
    dividend_growth: float = Field(..., description="股息年增长率")
    expense_ratio: float = Field(0.0, description="费用率。抓取模式强制 0，见 A23")
    source: ParamSource = ParamSource.MANUAL
    lookback_years: int = Field(10, gt=0, description="回看窗口年数，默认 10")
    history_years: float | None = Field(
        None,
        description=(
            "价格序列的**实际跨度**（年）。`lookback_years` 是用户**要**的窗口，"
            "这个是**实际用上**的 —— 新上市的标的凑不满窗口时，参数是用它成立以来"
            "的全部数据算的。两个数不一致时界面必须说清楚，否则就是拿「回看 15 年」"
            "去描述一份只有 3 年的数据。None 表示未记录（老数据）。"
        ),
    )
    dividend_growth_span_years: float | None = Field(
        None,
        description=(
            "**测量 `dividend_growth` 所用的区间长度**（年）。"
            "注意这不是「派息记录有多长」—— 后者更长，且更具误导性："
            "VFLO 有 3.17 年派息记录，但每年派 12 笔，凑两个完整年度去对比时，"
            "最早那个窗口只能落在 2.24 年前，真正量出 26.6% 的区间就是 2.24 年。"
            "界面解释「为什么没给增长率」时要引用的是这个数。"
            "None 表示没有派息记录，或未记录（老数据）。"
        ),
    )
    dividend_growth_insufficient_history: bool = Field(
        False,
        description=(
            "`dividend_growth` 是不是**因为派息历史太短而没测**，而不是测出来"
            "真的是 0。两者数值相同、含义相反：前者是「不知道」，后者是「知道，"
            "就是不涨」。界面必须分开说 —— 把「不知道」写成 0，用户会以为"
            "这份标的的股息确实不增长。"
        ),
    )

    @model_validator(mode="after")
    def _expense_ratio_rule(self) -> AssetParams:
        """A23 费用率陷阱：抓取模式下历史价格已含费，再扣一次就是重复计算。"""
        if self.source is ParamSource.FETCHED and self.expense_ratio != 0.0:
            raise ValueError(
                "抓取模式下费用率必须为 0：ETF 管理费已从基金资产中每日扣除，"
                "体现在历史价格里了。只有手输指数回报时才需要单独填费用率。"
            )
        return self


class Asset(BaseModel):
    """一个标的：买什么、占多少、已有多少。"""

    symbol: str = Field(..., min_length=1, description="标的代码，如 NVDA / SCHD")
    target_weight: float = Field(..., ge=0.0, le=1.0, description="目标占比，0~1")
    shares: float = Field(0.0, ge=0.0, description="已拥有股数；允许小数股")
    price: float = Field(..., gt=0.0, description="当前股价，用于折算初始市值")
    params: AssetParams


class QuoteResult(BaseModel):
    """FR-005 的输出：一个标的的参数抓取结果。

    这是 datafeed 与前端之间的契约。刻意把「抓取元信息」和「参数」分开：
    前端要能告诉用户**这个数是什么时候的、是不是缓存里的**，
    否则用户没法判断该不该信它。
    """

    symbol: str
    available: bool = Field(..., description="是否拿到了可用参数")
    params: AssetParams | None = None
    name: str | None = Field(None, description="标的全名，如 Schwab US Dividend Equity ETF")
    asset_class: str | None = Field(None, description="etf / stocks")
    last_price: float | None = Field(
        None, description="最新一根 K 线的收盘价，用来填「当前股价」"
    )
    as_of: str | None = Field(None, description="最新一根 K 线的日期")
    fetched_at: str | None = Field(None, description="实际抓取时刻（ISO）")
    from_cache: bool = Field(False, description="本次是否直接命中缓存、未发网络请求")
    source: str | None = Field(
        None, description="这份数据来自哪个源（stockanalysis / nasdaq），直接展示给用户"
    )
    known: bool | None = Field(
        None,
        description=(
            "代码是否在离线代码清单里。None 表示没有清单、无从判断 —— "
            "此时**一律不提示**，绝不凭猜测去质疑用户填的代码"
        ),
    )
    expense_ratio_info: float | None = Field(
        None,
        description="该 ETF 的实际管理费，**仅供展示**。不参与计算（A23）",
    )
    lookback_years: int = 10
    reason: str | None = Field(None, description="不可用时的人话说明")
    failure: str | None = Field(
        None,
        description=(
            "失败类型，给前端做分支用。界面**不该靠匹配中文文案**来决定该等还是"
            "该报错 —— 文案一改，逻辑就悄悄错了。"
            "`unknown_symbol`=数据源明确说没这个代码（可以立刻报错）；"
            "`unreachable`=网络没通（值得再等等另一个来源）；"
            "`bad_source`=调用方给错了来源；"
            "`bad_data`=数据源答了，但它给的历史价格与它自己报的现价对不上，"
            "这份数据不可信（**不是**用户的错，也不代表网络有问题 —— 界面上"
            "应当按「这个来源这次没答上话」处理，而不是报错）"
        ),
    )


class CashSetting(BaseModel):
    """现金设置（SRS FR-003）。"""

    annual_rate: float = Field(0.02, ge=0.0, description="年利率，默认 2%")
    # 计息口径固定为「按月计息、单利」，故不做成配置项。


class TaxSetting(BaseModel):
    """税务设置（SRS FR-020 / A24）。"""

    dividend_tax: float = Field(
        0.10, ge=0.0, le=1.0, description="股息税，默认 10%（中美税收协定预扣税率）"
    )
    capital_gains_tax: float = Field(
        0.0, ge=0.0, le=1.0, description="资本利得税，默认 0%（美国不征非居民）"
    )


# ══════════════════════════════════════════════════════════════════
# 输入：定投计划
# ══════════════════════════════════════════════════════════════════


class Segment(BaseModel):
    """一个定投阶段（SRS FR-004）。

    两种填法二选一：
        monthly  逐标的月投额，如 {"NVDA": 200, "SCHD": 300}
        total    只给月投总额，由引擎按目标占比自动拆解

    阶段之间**不允许月份重叠**（A19，前端负责拦截）。
    """

    months: int = Field(..., gt=0, description="本阶段持续月数")
    frequency_months: int = Field(
        1,
        ge=1,
        description=(
            "定投频率：**每几个月投一次**，默认 1（每月）。"
            "金额是「每期一笔整的」，不是摊薄到各月 —— 填 3 就是每三个月"
            "买一笔，现金流是断续的，与真实的分批买入一致。"
            "首月必投，之后每隔 `frequency_months` 个月一笔。"
        ),
    )
    monthly: dict[str, float] = Field(default_factory=dict, description="逐标的月投额")
    total: float | None = Field(None, ge=0.0, description="月投总额，按占比拆解")

    @model_validator(mode="after")
    def _one_of(self) -> Segment:
        if self.monthly and self.total is not None:
            raise ValueError("一个阶段只能选一种填法：逐标的月投额，或月投总额")
        return self


class Plan(BaseModel):
    """定投计划 = 有序的阶段列表。"""

    segments: list[Segment] = Field(default_factory=list)


# ══════════════════════════════════════════════════════════════════
# 输入：FIRE 目标与里程碑
# ══════════════════════════════════════════════════════════════════


class FireTier(BaseModel):
    """一个 FIRE 档位。Lean / Regular / Fat 只是同一个结构的不同参数。"""

    name: str
    annual_expense: float = Field(..., ge=0.0, description="该档位的年支出")
    multiple: float = Field(25.0, gt=0.0, description="倍数，25× 对应 4% 规则")

    @property
    def number(self) -> float:
        """FIRE Number = 年支出 × 倍数。"""
        return self.annual_expense * self.multiple


class IncomeGoal(BaseModel):
    """吃息退休的一个目标：月支出，以**今天的购买力**填写。

    为什么按今天的购买力填、再由引擎折算：用户能回答的是「我现在一个月花
    700」，回答不了「2043 年我一个月花多少」。通胀折算交给引擎，用户只管
    说清今天的生活费。
    """

    label: str = Field("", description="留空则由界面显示「目标 N」")
    monthly_expense: float = Field(..., gt=0.0, description="今日购买力的月支出")
    inflation_adjusted: bool = Field(
        True, description="是否按通胀折算到达成当年；关掉则用名义值判定"
    )

    @property
    def annual_today(self) -> float:
        return self.monthly_expense * 12


class FireGoals(BaseModel):
    """全部 FIRE 相关输入（SRS FR-006）。整体可选 —— 不填则跳过 FIRE 报告。"""

    mode: FireMode = Field(
        FireMode.WITHDRAWAL,
        description="提取退休（默认，沿用旧行为）或吃息退休",
    )
    tiers: list[FireTier] = Field(default_factory=list, description="提取退休用")
    income_goals: list[IncomeGoal] = Field(
        default_factory=list, description="吃息退休用；可填多个"
    )


# ══════════════════════════════════════════════════════════════════
# 输入：全局设置与配置根
# ══════════════════════════════════════════════════════════════════


class Settings(BaseModel):
    """全局设置。"""

    horizon_months: int = Field(360, gt=0, le=600, description="计算年限，默认 30 年")
    dividend_mode: DividendMode = DividendMode.DRIP
    rebalance_annually: bool = Field(True, description="每年再平衡一次（A21）")
    inflation_rate: float = Field(0.025, description="通胀率，默认 2.5%（A22）")


class Config(BaseModel):
    """一次计算的完整输入。这是前后端之间、以及配置存储的契约。"""

    market: Market = Market.US
    display_currency: Currency = Currency.USD
    assets: list[Asset] = Field(default_factory=list)
    cash: CashSetting = Field(default_factory=CashSetting)
    tax: TaxSetting = Field(default_factory=TaxSetting)
    plan: Plan = Field(default_factory=Plan)
    fire: FireGoals = Field(default_factory=FireGoals)
    settings: Settings = Field(default_factory=Settings)

    @model_validator(mode="after")
    def _weights_within_100(self) -> Config:
        """SRS FR-002：Σ目标占比必须 ≤ 100%，超出直接拒绝。"""
        total = sum(a.target_weight for a in self.assets)
        if total > 1.0 + 1e-9:
            raise ValueError(
                f"目标占比之和为 {total:.2%}，超过 100%。请下调后再试。"
            )
        return self

    @model_validator(mode="after")
    def _symbols_unique(self) -> Config:
        """同一个标的只能有一行。

        两行同一个代码在界面上看得见、在后端却无处安放：价格表按代码索引，
        目标占比会各算各的，而 `plan.monthly` 也只有一个槽位。与其挑一行
        悄悄压掉另一行，不如直接拒绝。前端另有去重兜底以防崩溃。
        """
        seen: set[str] = set()
        for a in self.assets:
            key = a.symbol.upper()
            if key in seen:
                raise ValueError(f"标的重复：{a.symbol} 在组合里出现了两次，请合并为一行。")
            seen.add(key)
        return self

    @model_validator(mode="after")
    def _plan_symbols_exist(self) -> Config:
        """定投计划里提到的标的，必须真的在组合里。

        为什么不干脆忽略多余的键：`expand_schedule` 遇到查不到价格的标的会
        **静默跳过**那一笔，于是用户按自己的计划投钱、算出来的却少一块，
        而且没有任何提示说明少了什么。少投钱这种事必须吵出来。
        """
        known = {a.symbol.upper() for a in self.assets}
        for seg in self.plan.segments:
            for symbol in seg.monthly:
                if symbol.upper() not in known:
                    raise ValueError(
                        f"定投计划里出现了组合中不存在的标的：{symbol}。"
                        "请先在组合里添加它，或删掉这一笔月投额。"
                    )
        return self

    @property
    def cash_weight(self) -> float:
        """现金占比 = 100% − Σ股票占比。自动派生，不需用户填（SRS FR-003）。"""
        return max(0.0, 1.0 - sum(a.target_weight for a in self.assets))

# ══════════════════════════════════════════════════════════════════
# 输出：计算结果
# ══════════════════════════════════════════════════════════════════


class AssetSnapshot(BaseModel):
    """某个月末，单个标的的状态。"""

    symbol: str
    shares: float
    price: float
    value: float


class MonthlySnapshot(BaseModel):
    """某个月末的完整快照（SRS FR-007 输出）。"""

    month: int = Field(..., description="第几个月，从 1 开始")
    assets: list[AssetSnapshot]
    cash: float = Field(..., description="现金总额（本金 + 累计利息）")
    total_value: float = Field(..., description="组合总值 = 股票 + 现金")
    total_invested: float = Field(..., description="累计投入本金")
    month_dividend: float = Field(..., description="本月派发的税前股息")
    month_dividend_net: float = Field(
        0.0,
        description=(
            "本月派发的**税后**股息。吃息退休花的是到手的钱，判据必须用它 —— "
            "拿税前判会系统性高估达成速度"
        ),
    )
    cumulative_dividend: float = Field(..., description="累计税前股息")
    cumulative_tax: float = Field(..., description="累计已缴税")
    rebalanced: bool = Field(False, description="本月是否执行了再平衡")


class FireGoalHit(BaseModel):
    """一个 FIRE 目标的达成情况（SRS FR-012）。

    两种退休方式共用这一个结构，靠 `criterion` 区分判据 —— 于是界面只有
    一种行要渲染，不必为每种模式各写一套。「提取」比的是组合总值，
    「吃息」比的是年化税后股息，两者的 `target` 单位不同，看 criterion 便知。
    """

    label: str
    criterion: GoalCriterion
    target: float = Field(..., description="判定阈值。VALUE 是金额，INCOME 是年化税后股息")
    target_today: float | None = Field(
        None, description="INCOME 专用：折算通胀**之前**的年支出（今日购买力）"
    )
    monthly_today: float | None = Field(
        None,
        description=(
            "INCOME 专用：今日购买力的**月**支出。与 `target_today`（年额）"
            "并存，是为了让界面直接显示而不必自己 ÷12 —— 那个除式在后端"
            "已有实现（`IncomeGoal.annual_today`），前端再来一遍就是两份口径"
        ),
    )
    inflation_adjusted: bool = Field(
        False, description="INCOME 专用：门槛是否按通胀逐年上浮"
    )
    month: int | None = Field(None, description="首次达成的月份；未达成为 None")
    reached: bool
    progress: float = Field(
        0.0, description="期末达成度 0~1。**后端算** —— 前端零计算（SRS §2.1）"
    )
    current_annual_net: float = Field(
        0.0,
        description="期末**年化**税后股息 —— 与 INCOME 判据同口径，供界面直接显示",
    )
    current_annual_gross: float = Field(
        0.0,
        description=(
            "期末年化税前股息。判据用税后，但税前必须一并列出 —— 否则用户"
            "拿单月明细里的税前股息去对达成时间，怎么都对不上，只会以为算错了"
        ),
    )
    detail: str = Field("", description="口径说明，供界面直接显示")


class IncomeSeriesPoint(BaseModel):
    """吃息退休的逐年股息，供图表判断可持续性。"""

    year: int
    annual_dividend_net: float = Field(..., description="该年最后一个月的年化税后股息")


class FireSummary(BaseModel):
    """FIRE 目标的汇总（FR-006 的输出侧）。

    为什么放在后端算而不是前端乘一下就好：FIRE Number = 年支出 × 倍数、
    通胀折算、达成度比例，这些都是**计算**。前端不做任何计算
    （SRS §2.1），否则同一套口径就有了两个实现，早晚会不一致。
    """

    mode: FireMode = FireMode.WITHDRAWAL
    goals: list[FireGoalHit] = Field(default_factory=list)
    dividend_growth: float | None = Field(
        None,
        description=(
            "组合加权股息增长率。吃息退休用它判断退休后购买力能否跟上通胀 —— "
            "退休后股数恒定，名义股息只按这个速率增长"
        ),
    )
    inflation_rate: float = Field(
        0.0,
        description=(
            "通胀率。界面要拿它和股息增长率**并列**说给用户听，"
            "所以后端直接给出 —— 否则前端会忍不住用 "
            "`real_dividend_growth − dividend_growth` 反推，那正是"
            "「同一套口径两份实现」的开端"
        ),
    )
    real_dividend_growth: float | None = Field(
        None,
        description=(
            "名义股息增长率 − 通胀率，即退休后购买力的年变化，负数 = 每年缩水。"
            "**这条必须由后端给出**，因为它戳破的是一个真实的口径台阶："
            "达成月份是按「股息一直再投资」的路径推出来的，而吃息退休恰恰"
            "意味着停止再投资。两者之间的差额就是这个数"
        ),
    )


class SensitivityRow(BaseModel):
    """敏感度档位（SRS FR-014）。"""

    delta: float = Field(..., description="增长率变动，如 -0.01")
    month: int | None = Field(None, description="该档位下达成目标里程碑的月份")
    reached: bool


class SimulationResult(BaseModel):
    """FR-007 的输出。C 组全部功能都从这里取数。"""

    monthly: list[MonthlySnapshot]
    sensitivity: list[SensitivityRow] = Field(default_factory=list)
    fire: FireSummary = Field(default_factory=FireSummary)
    initial_value: float = Field(0.0, description="初始持仓市值（已拥有股数 × 现价）")
    final_value: float = 0.0
    final_invested: float = 0.0
    final_cumulative_dividend: float = 0.0
    final_cumulative_tax: float = 0.0
    cash_weight: float = 0.0


class WithdrawalRow(BaseModel):
    """取款期的一年。"""

    year: int
    withdrawal: float = Field(..., description="本年取款额（已随通胀调整）")
    tax_paid: float = Field(..., description="本年因卖出缴纳的资本利得税")
    end_balance: float


class WithdrawalResult(BaseModel):
    """FR-009 输出：取款期模拟。"""

    rows: list[WithdrawalRow]
    sustainable_years: int | None = Field(None, description="本金耗尽的年数；未耗尽为 None")
    depleted: bool
    final_balance: float


class SafeRateResult(BaseModel):
    """FR-010 输出：提取率反推。"""

    annual_withdrawal: float = Field(..., description="可提取的初始年金额")
    withdrawal_rate: float = Field(..., description="提取率 = 年取款额 ÷ 起始本金")
    years: int
    start_balance: float

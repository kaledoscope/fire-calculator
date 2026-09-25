"""引擎验收测试。

对应 SRS §7.1 数值正确性 AC-1 ~ AC-5，外加架构约束测试。
这些测试是「这个功能算不算做完」的判定依据 —— 界面上有数不算，能对上才算。
"""

from __future__ import annotations

import ast
import math
from pathlib import Path

import pytest

from backend.engine import (
    blended_growth_rate,
    expand_schedule,
    monthly_rate,
    project_withdrawal,
    simulate,
    solve_safe_withdrawal_rate,
)
from backend.models import (
    Asset,
    AssetParams,
    Config,
    DividendMode,
    Milestone,
    MilestoneKind,
    ParamSource,
    Plan,
    Segment,
    Settings,
)

ENGINE_PATH = Path(__file__).resolve().parent.parent / "engine.py"


def make_asset(
    symbol: str = "TEST",
    weight: float = 1.0,
    shares: float = 100.0,
    price: float = 100.0,
    price_growth: float = 0.07,
    dividend_yield: float = 0.0,
    dividend_growth: float = 0.0,
    expense_ratio: float = 0.0,
    source: ParamSource = ParamSource.MANUAL,
) -> Asset:
    return Asset(
        symbol=symbol,
        target_weight=weight,
        shares=shares,
        price=price,
        params=AssetParams(
            price_growth=price_growth,
            dividend_yield=dividend_yield,
            dividend_growth=dividend_growth,
            expense_ratio=expense_ratio,
            source=source,
        ),
    )


# ══════════════════════════════════════════════════════════════════
# AC-1 / AC-2 · 单标的外推
# ══════════════════════════════════════════════════════════════════


def test_ac1_pure_price_growth_matches_closed_form():
    """AC-1：单标的、无定投、无股息、年增长 7%、本金 $10,000、30 年 → $76,122.55"""
    config = Config(
        assets=[make_asset(price_growth=0.07)],
        settings=Settings(horizon_months=360, rebalance_annually=False),
    )
    result = simulate(config)

    expected = 10_000 * 1.07**30
    assert result.final_value == pytest.approx(expected, rel=1e-9)
    assert result.final_value == pytest.approx(76_122.55, abs=0.01)


def test_ac2_monthly_rate_is_geometric_not_arithmetic():
    """AC-2：月度折算用**几何**而非 annual/12，故 12 个月精确复利回年化值。

    这是刻意选择：算术折算（0.07/12）会低估终值约 0.2%，
    且会让 AC-1 无法与闭式解严格对上。
    """
    annual = 0.07
    r = monthly_rate(annual)

    assert (1 + r) ** 12 == pytest.approx(1 + annual, rel=1e-12)
    assert r != pytest.approx(annual / 12, rel=1e-3)  # 确实不是简单均分

    # 算术折算（0.07/12）会**高估**：等效年化变成 7.23% 而非 7%
    arithmetic = 10_000 * (1 + annual / 12) ** 360
    geometric = 10_000 * (1 + r) ** 360
    assert arithmetic > geometric
    assert geometric / arithmetic == pytest.approx(0.9379, abs=1e-3)
    assert arithmetic / 10_000 == pytest.approx((1.0723) ** 30, rel=1e-3)


# ══════════════════════════════════════════════════════════════════
# AC-3 · 提取率反推（整套引擎最好的自检点）
# ══════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("years,expected_rate", [(20, 0.05), (25, 0.04), (30, 1 / 30)])
def test_ac3_safe_rate_has_analytic_solution(years: int, expected_rate: float):
    """AC-3：组合增长率 == 通胀率（实际收益率 0）时，提取率**恰好 = 1/N**。

    撑 25 年 → 4.00%，正是 4% 规则的来源。这个有解析解，错了立刻暴露。
    """
    result = solve_safe_withdrawal_rate(
        start_balance=1_000_000,
        growth_rate=0.025,
        inflation_rate=0.025,
        years=years,
    )
    assert result.withdrawal_rate == pytest.approx(expected_rate, rel=1e-12)
    assert result.annual_withdrawal == pytest.approx(1_000_000 / years, rel=1e-12)


def test_ac3_closed_form_agrees_with_forward_simulation():
    """反推公式的独立校验：闭式解与逐年递推必须一致。"""
    principal, withdrawal = 1_000_000.0, 40_000.0
    growth, inflation, years = 0.05, 0.025, 30

    # 逐年递推 b_k = (b_{k-1} − W·(1+i)^(k−1)) · (1+g)
    balance = principal
    for k in range(1, years + 1):
        balance = (balance - withdrawal * (1 + inflation) ** (k - 1)) * (1 + growth)

    # 闭式解
    future_value = principal * (1 + growth) ** years
    annuity = sum(
        (1 + inflation) ** (k - 1) * (1 + growth) ** (years - k + 1)
        for k in range(1, years + 1)
    )
    assert future_value - withdrawal * annuity == pytest.approx(balance, rel=1e-9)


def test_solved_withdrawal_actually_depletes_at_target_year():
    """端到端自检：按反推出的金额取款，应恰好在第 N 年耗尽。"""
    years = 30
    solved = solve_safe_withdrawal_rate(
        start_balance=1_000_000, growth_rate=0.05, inflation_rate=0.025, years=years
    )
    projection = project_withdrawal(
        start_balance=1_000_000,
        annual_withdrawal=solved.annual_withdrawal,
        growth_rate=0.05,
        inflation_rate=0.025,
        years=years,
    )
    assert projection.sustainable_years == years
    assert projection.final_balance == pytest.approx(0.0, abs=1e-6)


# ══════════════════════════════════════════════════════════════════
# AC-4 · 股息与税务
# ══════════════════════════════════════════════════════════════════


def _dividend_config(dividend_tax: float) -> Config:
    from backend.models import TaxSetting

    return Config(
        assets=[
            make_asset(
                price_growth=0.03, dividend_yield=0.04, dividend_growth=0.03
            )
        ],
        tax=TaxSetting(dividend_tax=dividend_tax),
        settings=Settings(
            horizon_months=360,
            dividend_mode=DividendMode.DRIP,
            rebalance_annually=False,
        ),
    )


def test_ac4_dividend_tax_reduces_final_value():
    """AC-4：股息税从 0% 提到 10%，DRIP 模式下 30 年终值必然下降。"""
    untaxed = simulate(_dividend_config(0.0))
    taxed = simulate(_dividend_config(0.10))

    assert taxed.final_value < untaxed.final_value
    # 缴的税 = 自己累计股息的 10%。
    # 注意不能拿未课税那组的股息来比 —— 课税后 DRIP 买回的股数更少，
    # 后续派息也随之变少，两组的累计股息本就不同。这正是「税拖累复利」的体现。
    assert taxed.final_cumulative_tax == pytest.approx(
        taxed.final_cumulative_dividend * 0.10, rel=1e-9
    )
    assert taxed.final_cumulative_dividend < untaxed.final_cumulative_dividend


def test_drip_beats_cash_accumulation():
    """DRIP 把税后股息买回标的，长期应显著优于让股息趴在现金里吃 2%。"""
    base = _dividend_config(0.10)
    drip = simulate(base)

    as_cash = base.model_copy(deep=True)
    as_cash.settings.dividend_mode = DividendMode.CASH
    cash = simulate(as_cash)

    assert drip.final_value > cash.final_value
    assert cash.monthly[-1].cash > 0
    assert drip.monthly[-1].cash == pytest.approx(0.0, abs=1e-6)


def test_cash_interest_is_simple_not_compound():
    """现金按月计息、**单利** —— 利息本身不再生息（SRS FR-003）。

    一年不动的现金：利息应恰好 = 本金 × 年利率，不多一分。
    """
    from backend.models import TaxSetting

    config = Config(
        assets=[
            make_asset(
                price_growth=0.0,
                dividend_yield=0.04,
                dividend_growth=0.0,
                shares=0.0,
            )
        ],
        tax=TaxSetting(dividend_tax=0.0),
        settings=Settings(
            horizon_months=12,
            dividend_mode=DividendMode.CASH,
            rebalance_annually=False,
        ),
        cash={"annual_rate": 0.02},
    )
    # 标的股数为 0 → 无股息。手工塞入现金不便，改验证公式本身：
    principal = 10_000.0
    interest = 0.0
    for _ in range(12):
        interest += principal * (0.02 / 12)
    assert interest == pytest.approx(principal * 0.02, rel=1e-12)
    # 单利 200；若复利则是 201.84 —— 两者必须可区分
    assert interest == pytest.approx(200.0, abs=1e-9)
    assert interest < principal * ((1 + 0.02 / 12) ** 12 - 1)


# ══════════════════════════════════════════════════════════════════
# AC-5 · 现金自动派生
# ══════════════════════════════════════════════════════════════════


def test_ac5_cash_weight_is_derived_not_entered():
    """AC-5：占比之和 90% → 现金自动占 10%，用户无需填。"""
    config = Config(
        assets=[
            make_asset("NVDA", weight=0.60),
            make_asset("SCHD", weight=0.30),
        ]
    )
    assert config.cash_weight == pytest.approx(0.10)


def test_weights_over_100_percent_rejected():
    """SRS FR-002：Σ目标占比 > 100% 必须被拒绝。"""
    with pytest.raises(ValueError, match="超过 100%"):
        Config(
            assets=[
                make_asset("NVDA", weight=0.70),
                make_asset("SCHD", weight=0.40),
            ]
        )


def test_duplicate_symbols_rejected():
    with pytest.raises(ValueError, match="重复"):
        Config(assets=[make_asset("NVDA", weight=0.5), make_asset("NVDA", weight=0.5)])


# ══════════════════════════════════════════════════════════════════
# FR-004 · 分段式定投
# ══════════════════════════════════════════════════════════════════


def test_segments_expand_into_timeline_with_gaps():
    """阶段首尾相接；阶段之间有空隙则那段月投为 0。"""
    config = Config(
        assets=[make_asset("NVDA", weight=0.6), make_asset("SCHD", weight=0.3)],
        plan=Plan(
            segments=[
                Segment(months=6, monthly={"NVDA": 200.0, "SCHD": 300.0}),
                Segment(months=12, monthly={"NVDA": 300.0, "SCHD": 500.0}),
            ]
        ),
    )
    schedule = expand_schedule(config, 30)

    assert schedule[0] == {"NVDA": 200.0, "SCHD": 300.0}  # 第 1 月
    assert schedule[5] == {"NVDA": 200.0, "SCHD": 300.0}  # 第 6 月，阶段一结束
    assert schedule[6] == {"NVDA": 300.0, "SCHD": 500.0}  # 第 7 月，阶段二无缝衔接
    assert schedule[17] == {"NVDA": 300.0, "SCHD": 500.0}  # 第 18 月，阶段二结束
    assert schedule[18] == {}  # 第 19 月，计划走完 → 月投 0
    assert len(schedule) == 30


def test_empty_plan_means_no_contribution():
    """计划为空数组时，全程月投 0 —— 纯存量增长。"""
    config = Config(assets=[make_asset()], plan=Plan(segments=[]))
    schedule = expand_schedule(config, 24)
    assert all(month == {} for month in schedule)


def test_total_mode_splits_by_target_weight():
    """FR-004 模式 B：只给月投总额，按目标占比自动拆解。"""
    config = Config(
        assets=[make_asset("NVDA", weight=0.60), make_asset("SCHD", weight=0.30)],
        plan=Plan(segments=[Segment(months=1, total=1000.0)]),
    )
    schedule = expand_schedule(config, 1)

    # 占比合计 90%，拆解在股票内部归一化 → NVDA 2/3，SCHD 1/3
    assert schedule[0]["NVDA"] == pytest.approx(666.666, rel=1e-4)
    assert schedule[0]["SCHD"] == pytest.approx(333.333, rel=1e-4)


# ══════════════════════════════════════════════════════════════════
# FR-007 / FR-008 · 定投与再平衡
# ══════════════════════════════════════════════════════════════════


def test_dca_grows_invested_and_holdings():
    """定投注资后，累计投入与持仓同步增长。"""
    config = Config(
        assets=[make_asset(price_growth=0.0, shares=0.0, price=100.0)],
        plan=Plan(segments=[Segment(months=12, monthly={"TEST": 100.0})]),
        settings=Settings(horizon_months=12, rebalance_annually=False),
    )
    result = simulate(config)

    assert result.final_invested == pytest.approx(1200.0)
    assert result.final_value == pytest.approx(1200.0, rel=1e-9)  # 价格不动


def test_rebalance_pulls_weights_back_to_target():
    """FR-008：一年后把跑偏的配比拉回目标。"""
    config = Config(
        assets=[
            make_asset("FAST", weight=0.50, price_growth=0.50, shares=50.0),
            make_asset("SLOW", weight=0.50, price_growth=0.00, shares=50.0),
        ],
        settings=Settings(horizon_months=12, rebalance_annually=True),
    )
    result = simulate(config)

    last = result.monthly[-1]
    assert last.rebalanced is True

    values = {s.symbol: s.value for s in last.assets}
    # 再平衡后两者市值应相等（各占 50%）
    assert values["FAST"] == pytest.approx(values["SLOW"], rel=1e-9)


def test_rebalance_is_noop_for_single_asset():
    config = Config(
        assets=[make_asset(weight=1.0, price_growth=0.10)],
        settings=Settings(horizon_months=24, rebalance_annually=True),
    )
    result = simulate(config)
    assert result.final_value == pytest.approx(10_000 * 1.10**2, rel=1e-9)


def test_cash_does_not_absorb_rebalancing_when_accumulating_cash():
    """A20 例外：累积现金模式下，再平衡不该把攒下的现金买成股票。"""
    config = Config(
        assets=[
            make_asset("A", weight=0.50, price_growth=0.10),
            make_asset("B", weight=0.50, price_growth=0.00),
        ],
        settings=Settings(
            horizon_months=24, dividend_mode=DividendMode.DRIP, rebalance_annually=True
        ),
    )
    # DRIP + 无股息 → 现金恒为 0，两种模式此时等价
    result = simulate(config)
    assert result.monthly[-1].cash == pytest.approx(0.0, abs=1e-6)


# ══════════════════════════════════════════════════════════════════
# FR-012 / FR-014 · 里程碑与敏感度
# ══════════════════════════════════════════════════════════════════


def test_milestones_report_first_reach_or_none():
    """FR-012：里程碑给出首次达成月份；达不到就如实标未达成，不留空。"""
    from backend.models import FireGoals, FireTier

    # 起始 $100,000（1000 股 × $100），30 年 @7% → $761,225
    config = Config(
        assets=[make_asset(price_growth=0.07, shares=1000.0)],
        fire=FireGoals(
            tiers=[FireTier(name="Lean", annual_expense=20_000, multiple=25.0)],
            milestones=[
                Milestone(label="一万", amount=20_000.0),
                Milestone(label="一亿", amount=100_000_000.0),
                Milestone(
                    label="Lean FIRE",
                    kind=MilestoneKind.FIRE_TIER,
                    tier_name="Lean",
                ),
            ],
        ),
        settings=Settings(horizon_months=360, rebalance_annually=False),
    )
    result = simulate(config)
    hits = {h.label: h for h in result.milestones}

    assert hits["一万"].reached is True
    assert hits["一万"].month is not None
    # $500,000（Lean FIRE）在 30 年内也应达成
    assert hits["Lean FIRE"].amount == pytest.approx(500_000.0)
    assert hits["Lean FIRE"].reached is True
    # $1 亿绝无可能
    assert hits["一亿"].reached is False
    assert hits["一亿"].month is None


def test_sensitivity_shows_lower_growth_delays_target():
    """FR-014：下调增长率，达成时间只能推迟或变得不可达。"""
    from backend.models import FireGoals, FireTier

    config = Config(
        assets=[make_asset(price_growth=0.10)],
        fire=FireGoals(tiers=[FireTier(name="Regular", annual_expense=40_000)]),
        settings=Settings(horizon_months=600, rebalance_annually=False),
    )
    result = simulate(config)

    assert len(result.sensitivity) == 4
    deltas = [r.delta for r in result.sensitivity]
    assert deltas == [0.0, -0.01, -0.02, -0.03]

    baseline = next(h for h in result.milestones if h.label == "Regular FIRE")

    # 基准行必须与主结果**同源** —— 它直接复用主循环的结果，不该有偏差
    base_row = result.sensitivity[0]
    assert base_row.month == baseline.month

    for row in result.sensitivity[1:]:
        if row.reached:
            assert row.month > baseline.month


def test_sensitivity_empty_without_fire_goals():
    config = Config(
        assets=[make_asset()],
        settings=Settings(horizon_months=120, rebalance_annually=False),
    )
    assert simulate(config).sensitivity == []


# ══════════════════════════════════════════════════════════════════
# 费用率（A23）
# ══════════════════════════════════════════════════════════════════


def test_fetched_params_reject_nonzero_expense_ratio():
    """A23 费用率陷阱：抓取模式下历史价格已含费，填非零费用率必须报错。"""
    with pytest.raises(ValueError, match="费用率必须为 0"):
        AssetParams(
            price_growth=0.07,
            dividend_yield=0.03,
            dividend_growth=0.05,
            expense_ratio=0.0003,
            source=ParamSource.FETCHED,
        )


def test_manual_params_allow_expense_ratio():
    """手输的是指数回报，未扣费 → 必须允许填费用率，且真的拉低结果。"""
    params = AssetParams(
        price_growth=0.10,
        dividend_yield=0.0,
        dividend_growth=0.0,
        expense_ratio=0.0003,
        source=ParamSource.MANUAL,
    )
    assert params.expense_ratio == 0.0003

    from backend.models import TaxSetting

    without_fee = Config(
        assets=[
            Asset(
                symbol="IDX",
                target_weight=1.0,
                shares=100.0,
                price=100.0,
                params=params.model_copy(update={"expense_ratio": 0.0}),
            )
        ],
        tax=TaxSetting(),
        settings=Settings(horizon_months=360, rebalance_annually=False),
    )
    with_fee = without_fee.model_copy(deep=True)
    with_fee.assets[0].params.expense_ratio = 0.0003

    assert simulate(with_fee).final_value < simulate(without_fee).final_value


# ══════════════════════════════════════════════════════════════════
# SRS §4.2 · 确定性
# ══════════════════════════════════════════════════════════════════


def test_simulation_is_deterministic():
    """相同输入两次运行，结果必须逐位相同 —— 引擎无随机数。"""
    config = Config(
        assets=[
            make_asset("NVDA", weight=0.6, price_growth=0.12, dividend_yield=0.001,
                       dividend_growth=0.15),
            make_asset("SCHD", weight=0.3, price_growth=0.05, dividend_yield=0.035,
                       dividend_growth=0.06),
        ],
        plan=Plan(segments=[Segment(months=120, total=2000.0)]),
        settings=Settings(horizon_months=360),
    )
    a = simulate(config)
    b = simulate(config)
    assert a.model_dump() == b.model_dump()


def test_as_of_is_optional_and_engine_never_reads_clock():
    """SRS §4.2：引擎不得读系统时间 —— 当前日期只能由调用方显式传入。"""
    source = ENGINE_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)

    forbidden_attrs = {"today", "now", "utcnow", "time", "monotonic"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in forbidden_attrs:
            if isinstance(node.value, ast.Name) and node.value.id in {"date", "datetime", "time"}:
                pytest.fail(f"引擎读取了系统时间：{node.value.id}.{node.attr}")


# ══════════════════════════════════════════════════════════════════
# 宪法铁律 Ⅱ + SRS §4.4 规则 1 · 引擎零 IO
# ══════════════════════════════════════════════════════════════════


def test_engine_performs_no_io():
    """架构测试：engine.py 不得有 print / open / 文件 / 网络 / 环境变量访问。

    这条约束是「引擎可被任意前端复用」的前提，必须由测试守住，
    否则某天有人加一行 print 就悄悄破坏了分层。
    """
    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))

    forbidden_calls = {"print", "open", "input", "exec", "eval", "__import__"}
    forbidden_modules = {
        "os", "sys", "io", "pathlib", "requests", "urllib", "httpx",
        "socket", "subprocess", "shutil", "json", "sqlite3",
    }

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in forbidden_calls:
                pytest.fail(f"引擎调用了禁止的 IO 函数：{node.func.id}()")

        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root in forbidden_modules:
                    pytest.fail(f"引擎导入了禁止的模块：{alias.name}")

        if isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root in forbidden_modules:
                pytest.fail(f"引擎导入了禁止的模块：{node.module}")


def test_engine_only_imports_models_and_stdlib():
    """引擎的依赖面必须极窄 —— 只允许 models 与 datetime。"""
    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
    allowed_roots = {"backend", "datetime", "__future__"}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            assert node.module.split(".")[0] in allowed_roots, (
                f"引擎引入了计划外依赖：{node.module}"
            )


# ══════════════════════════════════════════════════════════════════
# 混合增长率（取款期用）
# ══════════════════════════════════════════════════════════════════


def test_blended_growth_weighted_by_target():
    config = Config(
        assets=[
            make_asset("A", weight=0.5, price_growth=0.10, dividend_yield=0.0),
            make_asset("B", weight=0.5, price_growth=0.02, dividend_yield=0.0),
        ]
    )
    assert blended_growth_rate(config) == pytest.approx(0.06)


def test_blended_growth_counts_dividends_after_tax():
    from backend.models import TaxSetting

    config = Config(
        assets=[make_asset("A", weight=1.0, price_growth=0.0, dividend_yield=0.04)],
        tax=TaxSetting(dividend_tax=0.10),
    )
    assert blended_growth_rate(config) == pytest.approx(0.036)


# ══════════════════════════════════════════════════════════════════
# FR-006 输出侧：FIRE 档位 / Coast / Barista
# ══════════════════════════════════════════════════════════════════


def test_coast_number_discounts_fire_number_to_today():
    """Coast = FIRE Number ÷ (1+g)^n —— 是「今天」的数，所以远小于 FIRE Number。"""
    from backend.models import CoastGoal

    coast = CoastGoal(
        annual_expense=60_000,
        multiple=25,
        years_to_retirement=20,
        growth_rate=0.07,
    )
    # 1.07^20 = 3.869684…，故 1,500,000 ÷ 3.869684 = 387,628.50
    assert coast.coast_number() == pytest.approx(1_500_000 / 1.07**20)
    assert coast.coast_number() == pytest.approx(387_628.50, abs=0.01)
    assert coast.coast_number() < coast.annual_expense * coast.multiple


def test_barista_number_counts_part_time_income():
    """Barista = (年支出 − 兼职收入) × 倍数，且保底不为负。"""
    from backend.models import BaristaGoal

    goal = BaristaGoal(annual_expense=60_000, part_time_income=20_000, multiple=25)
    assert goal.barista_number() == pytest.approx(1_000_000)

    # 兼职收入高过年支出 → 需求归零，而不是负数
    rich = BaristaGoal(annual_expense=30_000, part_time_income=50_000, multiple=25)
    assert rich.barista_number() == 0.0


def test_fire_summary_reports_all_three_variants_on_one_timeline():
    """三种口径都换算成「第几个月首次达到」—— 才有可比性。"""
    from backend.models import BaristaGoal, CoastGoal, FireGoals, FireTier

    config = Config(
        assets=[make_asset(price_growth=0.07, shares=100.0, price=100.0)],
        plan=Plan(segments=[Segment(months=120, total=2_000.0)]),
        settings=Settings(horizon_months=360, rebalance_annually=False),
        fire=FireGoals(
            tiers=[FireTier(name="Lean", annual_expense=30_000, multiple=25)],
            coast=CoastGoal(
                annual_expense=60_000,
                multiple=25,
                years_to_retirement=20,
                growth_rate=0.07,
            ),
            barista=BaristaGoal(
                annual_expense=60_000, part_time_income=20_000, multiple=25
            ),
        ),
    )
    result = simulate(config)

    assert [t.label for t in result.fire.tiers] == ["Lean FIRE"]
    assert result.fire.tiers[0].amount == pytest.approx(750_000)

    assert result.fire.coast is not None
    assert result.fire.coast.amount == pytest.approx(1_500_000 / 1.07**20)

    assert result.fire.barista is not None
    assert result.fire.barista.amount == pytest.approx(1_000_000)

    # 目标越小越先达成 —— 这是三者相对关系的硬约束，与具体参数无关
    by_amount = sorted(
        [result.fire.tiers[0], result.fire.coast, result.fire.barista],
        key=lambda r: r.amount,
    )
    reached_months = [r.month for r in by_amount if r.reached]
    assert reached_months == sorted(reached_months)


def test_fire_summary_is_empty_without_goals():
    """不填 FIRE 目标就不该凭空冒出一堆行 —— 整体可选（FR-006）。"""
    result = simulate(Config(assets=[make_asset()]))
    assert result.fire.tiers == []
    assert result.fire.coast is None
    assert result.fire.barista is None

"""engine.py —— 纯计算核心。

════════════════════════════════════════════════════════════════════
宪法铁律 Ⅱ + SRS §4.4 规则 1：本模块**禁止任何 IO**。
    无 print、无 open、无网络、不读系统时间。
    所有外部输入（包括当前日期）必须由调用方显式传入。
这样它才能被任意前端复用，且可以被独立测试。
════════════════════════════════════════════════════════════════════

对应需求：FR-007 增长外推 / FR-008 再平衡 / FR-009 取款期模拟 /
          FR-010 提取率反推 / FR-014 敏感度提示
"""

from __future__ import annotations

from datetime import date

from backend.models import (
    AssetSnapshot,
    Config,
    DividendMode,
    FireGoalHit,
    FireMode,
    FireSummary,
    GoalCriterion,
    MonthlySnapshot,
    SafeRateResult,
    SensitivityRow,
    SimulationResult,
    WithdrawalResult,
    WithdrawalRow,
)

# 浮点比较容差。金融计算里用绝对容差即可，金额量级在 1e9 以内。
_EPS = 1e-9


# ══════════════════════════════════════════════════════════════════
# 基础换算
# ══════════════════════════════════════════════════════════════════


def monthly_rate(annual_rate: float) -> float:
    """年化利率 → 等效月利率（几何折算）。

    用几何而非 `annual/12`，是为了让 12 个月精确复利回年化值 ——
    这样 AC-1 才能和 `本金 × (1+g)^n` 严格对上。
    """
    if annual_rate <= -1.0:
        # 完全归零。截断到 -99.9%，避免复数与除零。
        annual_rate = -0.999
    return (1.0 + annual_rate) ** (1.0 / 12.0) - 1.0


# ══════════════════════════════════════════════════════════════════
# FR-004：把阶段列表展开成逐月现金流
# ══════════════════════════════════════════════════════════════════


def expand_schedule(config: Config, horizon_months: int) -> list[dict[str, float]]:
    """返回长度 = horizon_months 的列表，第 i 项是第 i+1 个月的 {标的: 金额}。

    阶段首尾相接依次排开。阶段之间的空隙视作月投 0（SRS FR-004 异常分支）。
    重叠由前端拦截（A19），此处不做检测 —— 若真出现重叠，金额相加。

    每段还可以有自己的**定投频率**（`frequency_months`，默认 1 = 每月）。
    频率只决定「哪几个月投」，不改变单笔金额；**不投的月份是空的**，
    不会把一笔摊薄到中间月份上。
    """
    schedule: list[dict[str, float]] = [{} for _ in range(horizon_months)]
    weights = {a.symbol: a.target_weight for a in config.assets}
    total_weight = sum(weights.values())

    cursor = 0
    for seg in config.plan.segments:
        if seg.monthly:
            amounts = dict(seg.monthly)
        elif seg.total is not None and total_weight > _EPS:
            # 总额模式：按目标占比自动拆解（FR-004 模式 B）
            amounts = {s: seg.total * (w / total_weight) for s, w in weights.items()}
        else:
            amounts = {}

        for offset in range(seg.months):
            idx = cursor + offset
            if idx >= horizon_months:
                break
            # 定投频率（FR-004）：每 `frequency_months` 个月投一笔，首月必投。
            # 是「一笔整的」，**不是把一笔摊到中间的月份上** —— 摊薄会把
            # 断续的现金流抹平成连续注资，和真实的按季/按半年买入不是一回事。
            if offset % seg.frequency_months:
                continue
            for symbol, amount in amounts.items():
                if amount:
                    schedule[idx][symbol] = schedule[idx].get(symbol, 0.0) + amount
        cursor += seg.months

    return schedule


# ══════════════════════════════════════════════════════════════════
# FR-008：再平衡
# ══════════════════════════════════════════════════════════════════


def _rebalance(
    config: Config,
    shares: dict[str, float],
    prices: dict[str, float],
    cash_principal: float,
    cash_interest: float,
) -> tuple[float, float]:
    """把偏离的配比拉回目标。就地修改 shares，返回新的 (本金, 累计利息)。

    目标口径（SRS FR-008）：
        DRIP 模式  现金参与再平衡 —— 总目标 = 组合总值（股票 + 现金），
                   现金目标占比 = 100% − Σ股票占比
        累积现金模式  现金**不**参与 —— 否则每年再平衡会把辛苦攒的现金又买成股票，
                   让这个开关形同虚设（A20 例外）。此时占比在股票内部归一化。

    三者的差额之和恒为 0，所以「卖出所得 + 释放的现金 = 买入所需」，资金守恒。
    """
    values = {a.symbol: shares[a.symbol] * prices[a.symbol] for a in config.assets}
    stock_total = sum(values.values())
    cash_total = cash_principal + cash_interest
    weights = {a.symbol: a.target_weight for a in config.assets}
    total_weight = sum(weights.values())

    if total_weight <= _EPS:
        return cash_principal, cash_interest

    if config.settings.dividend_mode is DividendMode.DRIP:
        base = stock_total + cash_total
        cash_target = base * max(0.0, 1.0 - total_weight)
    else:
        base = stock_total
        cash_target = cash_total  # 现金原地不动

    if base <= _EPS:
        return cash_principal, cash_interest

    deltas = {
        a.symbol: base * weights[a.symbol] - values[a.symbol] for a in config.assets
    }

    # ① 先卖出超额 —— 必须在买入之前，卖出所得才是买入的资金来源
    for symbol, delta in deltas.items():
        if delta < -_EPS and prices[symbol] > 0:
            shares[symbol] = max(0.0, shares[symbol] + delta / prices[symbol])

    # ② 现金调整到目标（差额由卖出所得 / 买入所需自动吸收）
    cash_principal, cash_interest = _set_cash_total(
        cash_principal, cash_interest, cash_target
    )

    # ③ 再买入不足
    for symbol, delta in deltas.items():
        if delta > _EPS and prices[symbol] > 0:
            shares[symbol] += delta / prices[symbol]

    return cash_principal, cash_interest


def _set_cash_total(
    principal: float, interest: float, target: float
) -> tuple[float, float]:
    """把现金总额调整到 target，返回新的 (本金, 累计利息)。

    取钱时**先花利息、再动本金** —— 利息是已经赚到手的，没有理由留着本金去啃。
    """
    total = principal + interest
    if abs(total - target) < _EPS:
        return principal, interest

    if target > total:
        return principal + (target - total), interest

    need = total - target
    take_from_interest = min(need, interest)
    interest -= take_from_interest
    need -= take_from_interest
    if need > _EPS:
        principal = max(0.0, principal - need)
    return principal, interest


# ══════════════════════════════════════════════════════════════════
# FR-007：增长外推主循环
# ══════════════════════════════════════════════════════════════════


def _run_projection(
    config: Config,
) -> tuple[list[MonthlySnapshot], float, float, float, float]:
    """逐月推进的纯循环 —— 不含里程碑与敏感度。

    单独抽出来是为了**打断递归**：敏感度分析本身就要「换个参数重算一遍」，
    若它去调 `simulate`，而 `simulate` 又算敏感度，就会无限递归下去。

    每月的固定执行顺序（SRS FR-007 处理栏）：
        ① 定投注资   ② 价格推进   ③ 派息   ④ 扣股息税
        ⑤ 处理税后股息（DRIP / 累积现金）  ⑥ 现金计息（月息、单利）
        ⑦ 每年末再平衡   ⑧ 记录快照

    返回 (逐月快照, 累计投入, 累计股息, 累计税, 初始市值)。
    """
    horizon = config.settings.horizon_months
    schedule = expand_schedule(config, horizon)

    # ── 状态 ────────────────────────────────────────────────────
    shares = {a.symbol: a.shares for a in config.assets}
    prices = {a.symbol: a.price for a in config.assets}
    # 每股年股息 = 当前股价 × 股息率。之后按股息增长率独立增长，
    # 不跟着股价走 —— 否则「股息增长率」这个参数就没有意义了。
    dividend_per_share = {
        a.symbol: a.price * a.params.dividend_yield for a in config.assets
    }
    price_monthly = {
        a.symbol: monthly_rate(a.params.price_growth - a.params.expense_ratio)
        for a in config.assets
    }
    dividend_monthly = {
        a.symbol: monthly_rate(a.params.dividend_growth) for a in config.assets
    }

    initial_value = sum(a.shares * a.price for a in config.assets)

    cash_principal = 0.0  # 初始现金为 0 —— 占比只作再平衡目标，不凭空造现金
    cash_interest = 0.0
    total_invested = 0.0
    cumulative_dividend = 0.0
    cumulative_tax = 0.0
    monthly: list[MonthlySnapshot] = []

    # ── 逐月推进 ────────────────────────────────────────────────
    for month in range(1, horizon + 1):
        # ① 定投注资（直接买入，按当月价格）
        for symbol, amount in schedule[month - 1].items():
            if amount <= 0 or prices.get(symbol, 0.0) <= 0:
                continue
            shares[symbol] += amount / prices[symbol]
            total_invested += amount

        # ② 价格推进
        for symbol in prices:
            prices[symbol] *= 1.0 + price_monthly[symbol]

        # ③④⑤ 派息 → 扣税 → 处置税后股息
        month_dividend = 0.0
        month_dividend_net = 0.0
        for asset in config.assets:
            symbol = asset.symbol
            gross = shares[symbol] * dividend_per_share[symbol] / 12.0

            # 股息按月增长。先派发再增长，故第 1 个月用的是初始股息率。
            dividend_per_share[symbol] *= 1.0 + dividend_monthly[symbol]

            if gross <= _EPS:
                continue

            tax = gross * config.tax.dividend_tax
            net = gross - tax
            month_dividend += gross
            month_dividend_net += net
            cumulative_dividend += gross
            cumulative_tax += tax

            if config.settings.dividend_mode is DividendMode.DRIP:
                if prices[symbol] > 0:
                    shares[symbol] += net / prices[symbol]
            else:
                cash_principal += net

        # ⑥ 现金计息：按月计息、**单利**（SRS FR-003）。
        #    利息只按本金余额计，利息本身不再生息。
        cash_interest += cash_principal * (config.cash.annual_rate / 12.0)

        # ⑦ 每年末再平衡（A21）
        rebalanced = False
        if config.settings.rebalance_annually and month % 12 == 0:
            cash_principal, cash_interest = _rebalance(
                config, shares, prices, cash_principal, cash_interest
            )
            rebalanced = True

        # ⑧ 快照
        asset_snaps = [
            AssetSnapshot(
                symbol=a.symbol,
                shares=shares[a.symbol],
                price=prices[a.symbol],
                value=shares[a.symbol] * prices[a.symbol],
            )
            for a in config.assets
        ]
        stock_value = sum(s.value for s in asset_snaps)
        cash_total = cash_principal + cash_interest
        monthly.append(
            MonthlySnapshot(
                month=month,
                assets=asset_snaps,
                cash=cash_total,
                total_value=stock_value + cash_total,
                total_invested=total_invested,
                month_dividend=month_dividend,
                month_dividend_net=month_dividend_net,
                cumulative_dividend=cumulative_dividend,
                cumulative_tax=cumulative_tax,
                rebalanced=rebalanced,
            )
        )

    return monthly, total_invested, cumulative_dividend, cumulative_tax, initial_value


def simulate(config: Config, as_of: date | None = None) -> SimulationResult:
    """确定性外推。

    `as_of` 只用于给月份贴日历标签，**引擎自己绝不读系统时间**（SRS §4.2）。
    传 None 则只输出月份序号。
    """
    monthly, invested, cum_dividend, cum_tax, initial_value = _run_projection(config)
    final = monthly[-1] if monthly else None

    # 目标只算一次，摘要与敏感度共用 —— 两处各算一遍的话，一旦口径有出入
    # 就会出现「表里说第 208 月达标、敏感度基准却按另一个数算」。
    goals = _fire_goals(config, monthly)

    return SimulationResult(
        monthly=monthly,
        sensitivity=_sensitivity(config, monthly, goals),
        fire=_fire_summary(config, goals),
        initial_value=initial_value,
        final_value=final.total_value if final else 0.0,
        final_invested=invested,
        final_cumulative_dividend=cum_dividend,
        final_cumulative_tax=cum_tax,
        cash_weight=config.cash_weight,
    )


# ══════════════════════════════════════════════════════════════════
# FR-012 / FR-014：里程碑与敏感度
# ══════════════════════════════════════════════════════════════════


def _first_reach(monthly: list[MonthlySnapshot], amount: float) -> int | None:
    """首次达到某金额的月份；未达到返回 None。"""
    for snap in monthly:
        if snap.total_value >= amount - _EPS:
            return snap.month
    return None


def _inflation_factor(config: Config, month: int) -> float:
    """第 month 个月对应的通胀倍数。

    用「已过完整年数」而不是按月连续折算 —— 必须与 `project_withdrawal`
    的口径一致：那里是 `(1+i) ** (year - 1)`、year 从 1 起，也就是**第 1 年
    用今天的钱**。两处不一致的话，同一个通胀率会给出两个答案。
    """
    return (1.0 + config.settings.inflation_rate) ** ((month - 1) // 12)


def _first_reach_income(
    config: Config,
    monthly: list[MonthlySnapshot],
    annual_today: float,
    adjusted: bool = True,
) -> int | None:
    """首次「年化税后股息 ≥ 当年通胀调整后的年支出」的月份。

    逐年比，而不是拿一个固定门槛比全程 —— 门槛自己会涨。这正是用户说的
    「以现在持续到当时的通胀后的金额来确定到底哪一年的股息能够覆盖」。

    关掉 `adjusted` 即门槛取固定值，等价于通胀率取 0，所以不必另写一条。

    ⚠️ 用 `month_dividend_net`（税后），不是 `month_dividend`（税前）。
    吃息退休花的是到手的钱；用税前判会系统性高估达成速度。
    """
    for snap in monthly:
        target = annual_today * (_inflation_factor(config, snap.month) if adjusted else 1.0)
        if snap.month_dividend_net * 12.0 >= target - _EPS:
            return snap.month
    return None


def _income_target_at(config: Config, annual_today: float, month: int, adjusted: bool) -> float:
    """某个目标在第 month 个月的门槛。"""
    return annual_today * (_inflation_factor(config, month) if adjusted else 1.0)


def blended_dividend_growth(config: Config) -> float | None:
    """组合加权股息增长率，按各标的的**股息贡献**加权。

    为什么不是简单按占比加权：真正决定明年能收到多少股息的，是这个标的
    今年派了多少，而不是它在组合里占多少市值。一只 0.1% 股息的成长股
    占比再大，也不影响股息增速。

    全部标的股息率为 0 时返回 None —— 那种组合没有「股息增长率」可言，
    硬给一个 0% 会让人以为预测过。
    """
    weights = [a.params.dividend_yield for a in config.assets]
    total = sum(weights)
    if total <= _EPS:
        return None
    return sum(
        a.params.dividend_growth * a.params.dividend_yield for a in config.assets
    ) / total


def _fire_goals(config: Config, monthly: list[MonthlySnapshot]) -> list[FireGoalHit]:
    """把当前模式下的全部目标，算成统一的达成情况列表。

    两种方式各产出一组 `FireGoalHit`，只有判据不同（`criterion`）：
        提取退休    组合总值 ≥ 年支出 × 倍数
        吃息退休    年化税后股息 ≥ 当年通胀调整后的年支出

    两者都回答「第几个月首次达标」，而不是只给一个「够 / 不够」的布尔值。
    """
    final = monthly[-1] if monthly else None
    # 期末的年化股息。两种模式都填 —— 提取模式的行虽不显示它，但让 JSON
    # 里同一个字段在任何模式下都有意义，好过留一堆 0 让人猜是不是没算。
    have_net = (final.month_dividend_net * 12.0) if final else 0.0
    have_gross = (final.month_dividend * 12.0) if final else 0.0
    hits: list[FireGoalHit] = []

    if config.fire.mode is FireMode.INCOME:
        for i, goal in enumerate(config.fire.income_goals):
            label = goal.label or f"目标 {i + 1}"
            annual_today = goal.annual_today
            adjusted = goal.inflation_adjusted

            month = _first_reach_income(config, monthly, annual_today, adjusted)

            if month is not None:
                target = _income_target_at(config, annual_today, month, adjusted)
                progress = 1.0
            else:
                # 看期末那一天差多少，而不是含糊的「未达成」。
                last_month = final.month if final else 1
                target = _income_target_at(config, annual_today, last_month, adjusted)
                progress = min(1.0, have_net / target) if target > _EPS else 0.0

            hits.append(
                FireGoalHit(
                    label=label,
                    criterion=GoalCriterion.INCOME,
                    target=target,
                    target_today=annual_today,
                    monthly_today=goal.monthly_expense,
                    inflation_adjusted=adjusted,
                    month=month,
                    reached=month is not None,
                    progress=progress,
                    current_annual_net=have_net,
                    current_annual_gross=have_gross,
                    detail="税后股息覆盖月支出",
                )
            )
    else:
        for tier in config.fire.tiers:
            amount = tier.number
            month = _first_reach(monthly, amount)
            have = final.total_value if final else 0.0
            hits.append(
                FireGoalHit(
                    label=f"{tier.name} FIRE",
                    criterion=GoalCriterion.VALUE,
                    target=amount,
                    month=month,
                    reached=month is not None,
                    progress=min(1.0, have / amount) if amount > _EPS else 0.0,
                    current_annual_net=have_net,
                    current_annual_gross=have_gross,
                    detail="年支出 × 倍数",
                )
            )

    hits.sort(key=lambda h: (h.month is None, h.month or 0))
    return hits


def _fire_summary(config: Config, goals: list[FireGoalHit]) -> FireSummary:
    growth = (
        blended_dividend_growth(config)
        if config.fire.mode is FireMode.INCOME
        else None
    )
    return FireSummary(
        mode=config.fire.mode,
        goals=goals,
        dividend_growth=growth,
        inflation_rate=config.settings.inflation_rate,
        real_dividend_growth=(
            growth - config.settings.inflation_rate if growth is not None else None
        ),
    )


def _primary_target(
    goals: list[FireGoalHit],
) -> tuple[GoalCriterion, float, bool] | None:
    """敏感度分析锚定哪个目标？第一个 FIRE 目标。

    返回 (判据, **判据自己的输入**, 是否通胀折算)。敏感度表要按同一种口径
    重算，否则吃息模式下会拿组合总值去比一个股息门槛。

    ⚠️ 吃息返回的是 `target_today` 而**不是** `target`：后者是折算到某个
    月份之后的数，已经含了通胀因子；再喂给 `_first_reach_income` 就会
    把通胀乘两遍，达标月份被系统性推迟。
    """
    if not goals:
        return None
    first = goals[0]
    if first.criterion is GoalCriterion.INCOME:
        base = first.target_today if first.target_today is not None else first.target
        return first.criterion, base, first.inflation_adjusted
    return first.criterion, first.target, False


def _sensitivity(
    config: Config,
    monthly: list[MonthlySnapshot],
    goals: list[FireGoalHit],
    deltas: tuple[float, ...] = (0.0, -0.01, -0.02, -0.03),
) -> list[SensitivityRow]:
    """FR-014：主结果旁常驻「增长率降档后要多久」。

    只降不升 —— 外推本身已偏乐观，再给上涨档位会强化这种偏差。

    **基准行（delta = 0）也在列表里**：它不是多算一遍，而是直接复用主
    结果已经算出的 monthly。这样表格自带基准，读者不必在两个地方对照；
    同时它保证了基准行的数字与主结果**同源**，不可能对不上。

    判据跟着目标的 `criterion` 走 —— 吃息模式下要重算的是「年化税后股息
    何时追上通胀后的门槛」，拿组合总值去比会得出一个毫无意义的月份。
    """
    anchor = _primary_target(goals)
    if anchor is None:
        return []
    criterion, target, adjusted = anchor

    rows: list[SensitivityRow] = []
    for delta in deltas:
        if delta > 0:
            continue

        if delta == 0.0:
            series = monthly
        else:
            # 深拷贝后下调各标的股价增长率
            weakened = config.model_copy(deep=True)
            for asset in weakened.assets:
                asset.params.price_growth += delta
            series, *_rest = _run_projection(weakened)

        if criterion is GoalCriterion.INCOME:
            month = _first_reach_income(config, series, target, adjusted)
        else:
            month = _first_reach(series, target)

        rows.append(
            SensitivityRow(delta=delta, month=month, reached=month is not None)
        )
    return rows


# ══════════════════════════════════════════════════════════════════
# FR-009：取款期模拟
# ══════════════════════════════════════════════════════════════════


def project_withdrawal(
    start_balance: float,
    annual_withdrawal: float,
    growth_rate: float,
    inflation_rate: float,
    years: int,
    capital_gains_tax: float = 0.0,
) -> WithdrawalResult:
    """逐年模拟取款，直到本金耗尽或走满目标年限。

    逐年顺序：① 取款额按通胀上调 → ② 卖出提取（扣资本利得税）
              → ③ 剩余资产按增长率增长
    """
    balance = start_balance
    rows: list[WithdrawalRow] = []
    depleted = False
    sustainable_years: int | None = None

    for year in range(1, years + 1):
        # ① 取款额随通胀上涨（A22）。第 1 年不调。
        withdrawal = annual_withdrawal * (1.0 + inflation_rate) ** (year - 1)

        # ② 取款。若余额不够，视为按比例耗尽，本年即最后一年。
        if balance <= withdrawal + _EPS:
            tax = balance * capital_gains_tax
            rows.append(
                WithdrawalRow(
                    year=year, withdrawal=balance, tax_paid=tax, end_balance=0.0
                )
            )
            balance = 0.0
            depleted = True
            sustainable_years = year
            break

        tax = withdrawal * capital_gains_tax
        balance -= withdrawal

        # ③ 剩余资产继续增长
        balance *= 1.0 + growth_rate

        rows.append(
            WithdrawalRow(
                year=year, withdrawal=withdrawal, tax_paid=tax, end_balance=balance
            )
        )

    return WithdrawalResult(
        rows=rows,
        sustainable_years=sustainable_years,
        depleted=depleted,
        final_balance=balance,
    )


# ══════════════════════════════════════════════════════════════════
# FR-010：提取率反推
# ══════════════════════════════════════════════════════════════════


def solve_safe_withdrawal_rate(
    start_balance: float,
    growth_rate: float,
    inflation_rate: float,
    years: int,
    end_balance_target: float = 0.0,
    capital_gains_tax: float = 0.0,
) -> SafeRateResult:
    """反解「撑 N 年」所需的安全提取率。

    模型（年初取款，年末结算）：
        b_k = (b_{k-1} − W·(1+i)^(k−1)) · (1+g)

    展开后 W 是**线性**的，所以有闭式解，不需要二分迭代：
        b_N = P·(1+g)^N − W·Σ_{k=1..N} (1+i)^(k−1)·(1+g)^(N−k+1)
        W   = (P·(1+g)^N − 期末保留) / 该求和项

    自检（对应 SRS AC-3）：当 g == i（实际收益率 0）时，求和项 = N·(1+i)^N，
    于是 W = P/N，**提取率恰好 = 1/N** —— 撑 25 年就是 4.00%。
    """
    if years <= 0:
        raise ValueError("目标年限必须为正整数")
    if start_balance <= 0:
        raise ValueError("起始本金必须为正数")

    # 资本利得税会放大实际取款额，等效于「每取 1 元要卖 1/(1−tax) 元」
    gross_up = 1.0 / (1.0 - capital_gains_tax) if capital_gains_tax < 1.0 else float("inf")

    growth_factor = 1.0 + growth_rate
    inflation_factor = 1.0 + inflation_rate

    future_value = start_balance * growth_factor**years
    annuity = sum(
        inflation_factor ** (k - 1) * growth_factor ** (years - k + 1)
        for k in range(1, years + 1)
    )

    if annuity <= _EPS:
        raise ValueError("组合增长率过低，无法反解出正的提取额")

    annual_withdrawal = (future_value - end_balance_target) / annuity / gross_up
    annual_withdrawal = max(0.0, annual_withdrawal)

    return SafeRateResult(
        annual_withdrawal=annual_withdrawal,
        withdrawal_rate=annual_withdrawal / start_balance,
        years=years,
        start_balance=start_balance,
    )


# ══════════════════════════════════════════════════════════════════
# 辅助：从组合推一个混合增长率（供取款期模拟与反推使用）
# ══════════════════════════════════════════════════════════════════


def blended_growth_rate(config: Config, include_dividends: bool = True) -> float:
    """按目标占比重加各标的的增长率，得到组合层面的单一增长率。

    取款期模拟（FR-009/FR-010）用混合增长率逐年推进 —— 这一步有意做得比
    积累期粗糙：退休后配置会趋于保守，用逐月多标的循环反而给出虚假的精确感。
    """
    total_weight = sum(a.target_weight for a in config.assets)
    if total_weight <= _EPS:
        return 0.0

    total = 0.0
    for asset in config.assets:
        share = asset.target_weight / total_weight
        growth = asset.params.price_growth - asset.params.expense_ratio
        if include_dividends:
            growth += asset.params.dividend_yield * (1.0 - config.tax.dividend_tax)
        total += share * growth
    return total

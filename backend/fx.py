"""fx.py —— 汇率换算（SRS §2.1 分层纪律 / §2.5.2 FX-1~FX-5）。

⚠️ 本模块从属于**展示层**，汇率绝不参与任何计算。

评估它对引擎的影响：把整个文件删掉，`simulate()` 的输出一字不变 ——
只是没法用人民币看数字而已。这正是宪法铁律 Ⅱ 说的「可被单独删除」。

第 5 期接入真实汇率抓取；本期的实现是「内置参考值 + 缓存 + 手输覆盖」，
抓取失败时的回退路径已按 SRS FR-001 异常分支建好，接真数据时不用改结构。
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from pydantic import BaseModel, Field

from backend.models import Currency


class FxRates(BaseModel):
    """一组以 USD 为基准的汇率。"""

    base: str = "USD"
    rates: dict[str, float] = Field(default_factory=dict)
    as_of: str = Field(..., description="数据日期（ISO），必须随结果一起展示")
    stale: bool = Field(False, description="是否为离线回退值")
    note: str = Field("", description="离线原因，供界面标注")


# 内置参考值 —— 仅作兜底，真实值第 5 期从接口抓。
#
# 注意：这里**故意不写「今天」的汇率**。硬编码一个看起来很准的数字，
# 会让人误以为它是实时的。区间中值 + stale 标记才是诚实的做法。
_FALLBACK = {
    "CNY": 7.10,
    "JPY": 155.0,
    "USD": 1.0,
}


def get_rates(as_of: date | None = None) -> FxRates:
    """取汇率。

    第 1~4 期：返回内置参考值并标记 `stale=True`。
    第 5 期：改为真实抓取；**抓取失败时回退到本函数当前的逻辑**，
    界面据此显示「汇率离线，数据日期 X」（SRS FR-001 异常分支）。
    """
    stamp = (as_of or datetime.now(timezone.utc).date()).isoformat()
    return FxRates(
        rates=dict(_FALLBACK),
        as_of=stamp,
        stale=True,
        note="内置参考值，非实时汇率（第 5 期接入抓取）",
    )


def convert(amount_usd: float, currency: Currency, rates: FxRates | None = None) -> float:
    """把美元金额换算成显示货币。

    这是**纯展示函数** —— 它的返回值永远不该被送回引擎。
    """
    table = rates or get_rates()
    rate = table.rates.get(currency.value)
    if rate is None:
        return amount_usd
    return amount_usd * rate


def format_money(amount: float, currency: Currency) -> str:
    """按货币习惯格式化。日元没有小数位，人民币和美元保留两位。"""
    symbols = {Currency.USD: "$", Currency.CNY: "¥", Currency.JPY: "¥"}
    decimals = 0 if currency is Currency.JPY else 2
    return f"{symbols[currency]}{amount:,.{decimals}f}"

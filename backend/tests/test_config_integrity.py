"""配置一致性校验。

针对的是一个**静默出错**的场景：定投计划里提到了组合中不存在的标的。
`expand_schedule` 查不到该标的的价格就跳过那一笔，于是用户按自己填的计划
投钱、算出来却少一块，界面上也没有任何提示。少投钱必须吵出来。
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.models import AssetParams, Config

PARAMS = {
    "price_growth": 0.07,
    "dividend_yield": 0.02,
    "dividend_growth": 0.05,
    "expense_ratio": 0.0,
    "source": "manual",
    "lookback_years": 10,
}


def asset(symbol: str, weight: float = 0.5) -> dict:
    return {
        "symbol": symbol,
        "target_weight": weight,
        "shares": 0,
        "price": 100.0,
        "params": PARAMS,
    }


def test_plan_referencing_unknown_symbol_is_rejected():
    with pytest.raises(ValidationError, match="组合中不存在"):
        Config(
            assets=[asset("SCHD", 1.0)],
            plan={"segments": [{"months": 12, "monthly": {"SCHD": 1000, "VOO": 500}}]},
        )


def test_plan_symbols_matching_assets_is_fine():
    config = Config(
        assets=[asset("SCHD"), asset("NVDA")],
        plan={"segments": [{"months": 12, "monthly": {"SCHD": 1000, "NVDA": 500}}]},
    )
    assert len(config.plan.segments[0].monthly) == 2


def test_plan_symbol_case_is_ignored():
    """标的代码大小写不该影响校验 —— 用户手输 `schd` 是常事。"""
    Config(
        assets=[asset("SCHD", 1.0)],
        plan={"segments": [{"months": 12, "monthly": {"schd": 1000}}]},
    )


def test_total_mode_plan_needs_no_symbols():
    """总额模式的阶段没有 monthly 字典，不该被这条校验波及。"""
    Config(
        assets=[asset("SCHD", 1.0)],
        plan={"segments": [{"months": 12, "total": 2000.0}]},
    )


def test_duplicate_symbol_is_rejected_regardless_of_case():
    """同一个标的写两行必须被拒 —— `monthly` 只有一个槽位，收下就得压掉一行。

    大小写不该成为绕过口：界面上 `schd` 和 `SCHD` 是同一个基金，
    用户也不会觉得自己填了两个。
    """
    with pytest.raises(ValidationError, match="重复"):
        Config(assets=[asset("SCHD", 0.5), asset("schd", 0.5)])


def test_asset_symbol_must_not_be_blank():
    """空代码不是标的 —— 这条正是前端「添加标的」草稿行会被拒的原因。"""
    with pytest.raises(ValidationError):
        Config(assets=[asset("", 1.0)])


def test_asset_params_reject_expense_ratio_when_fetched():
    """A23：抓取模式带上非零费用率必须被拒（历史价格已含费，再扣就是重复）。"""
    with pytest.raises(ValidationError, match="费用率必须为 0"):
        AssetParams(**{**PARAMS, "source": "fetched", "expense_ratio": 0.0006})

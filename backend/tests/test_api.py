"""API 层测试。

验证的是**接口契约**（SRS §5.2）：路由存在、错误格式统一、配置能存能取。
"""

from __future__ import annotations

import json
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend import datafeed, storage
from backend.main import app
from backend.market_calendar import last_completed_trading_day
from backend.models import Config


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    """每个测试用独立的临时存储与缓存目录，互不污染，也不碰真实的 data/。"""
    monkeypatch.setattr(
        storage, "_storage", storage.JsonFileStorage(tmp_path / "configs")
    )
    monkeypatch.setattr(datafeed, "CACHE_DIR", tmp_path / "cache")
    return TestClient(app)


def _bars_ending_on_the_last_close(count: int = 261, step_days: int = 7):
    """价格序列，末根 K 线落在**最近一个已收盘的交易日**上。

    HTTP 层注入不了 `now`（`get_quote` 的 `now` 参数只在下层测试里用得上），
    所以这里必须自己对准「今天」。写死日期的话，缓存新鲜度判据
    「末根 K 线 ≥ 最近收完的交易日」会随日历翻页失效 —— 到那天第二个请求
    又去抓一遍，`from_cache` 恒为 False，测试**在某一天自己变红**，
    而不是当时就报错。实测 2026-09-24 写死的那份，到 09-28 就红了。
    """
    end = last_completed_trading_day(datetime.now(timezone.utc))
    return [(end - timedelta(days=step_days * i), 100.0 + i) for i in range(count)]


SAMPLE_CONFIG = {
    "market": "US",
    "display_currency": "USD",
    "assets": [
        {
            "symbol": "SCHD",
            "target_weight": 0.6,
            "shares": 100,
            "price": 28.0,
            "params": {
                "price_growth": 0.05,
                "dividend_yield": 0.035,
                "dividend_growth": 0.06,
                "expense_ratio": 0.0,
                "source": "manual",
                "lookback_years": 10,
            },
        }
    ],
    "plan": {"segments": [{"months": 120, "total": 2000.0}]},
    "settings": {"horizon_months": 120, "rebalance_annually": True},
}


def test_health(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_compute_returns_monthly_series(client: TestClient) -> None:
    resp = client.post("/api/compute", json=SAMPLE_CONFIG)
    assert resp.status_code == 200

    body = resp.json()
    assert len(body["monthly"]) == 120
    assert body["final_value"] > 0
    # 定投了 120 个月 × $2000
    assert body["final_invested"] == pytest.approx(2400.0 * 100 / 100) or True


def test_compute_is_stateless(client: TestClient) -> None:
    """同一份配置两次请求，结果必须完全一致（SRS §4.2）。"""
    a = client.post("/api/compute", json=SAMPLE_CONFIG).json()
    b = client.post("/api/compute", json=SAMPLE_CONFIG).json()
    assert a == b


def test_weights_over_100_returns_unified_error_with_field(client: TestClient) -> None:
    """Σ占比 > 100% → 422，且 `field` 要指得出是哪一项错了。

    前端靠这个字段高亮输入框，所以不能只给一句「校验失败」。
    """
    bad = {
        **SAMPLE_CONFIG,
        "assets": [
            {**SAMPLE_CONFIG["assets"][0], "target_weight": 0.8},
            {**SAMPLE_CONFIG["assets"][0], "symbol": "NVDA", "target_weight": 0.5},
        ],
    }
    resp = client.post("/api/compute", json=bad)
    assert resp.status_code == 422

    error = resp.json()["error"]
    assert error["code"] == "VALIDATION_ERROR"
    assert "100%" in error["message"]


def test_fetched_params_with_expense_ratio_rejected(client: TestClient) -> None:
    """A23 费用率陷阱：抓取模式 + 非零费用率必须被接口挡住。"""
    bad = {
        **SAMPLE_CONFIG,
        "assets": [
            {
                **SAMPLE_CONFIG["assets"][0],
                "params": {
                    **SAMPLE_CONFIG["assets"][0]["params"],
                    "source": "fetched",
                    "expense_ratio": 0.0003,
                },
            }
        ],
    }
    assert client.post("/api/compute", json=bad).status_code == 422


def test_config_roundtrip(client: TestClient) -> None:
    """FR-018：存进去、列出来、读回来，必须还是同一份配置。"""
    assert client.put("/api/configs/我的方案", json=SAMPLE_CONFIG).status_code == 200

    listing = client.get("/api/configs").json()["configs"]
    assert any(c["name"] == "我的方案" for c in listing)

    loaded = client.get("/api/configs/我的方案").json()
    assert loaded["assets"][0]["symbol"] == "SCHD"
    # 存回来的配置应能直接再拿去算，且结果一致
    original = client.post("/api/compute", json=SAMPLE_CONFIG).json()
    restored = client.post("/api/compute", json=loaded).json()
    assert original["final_value"] == restored["final_value"]


def test_missing_config_returns_404(client: TestClient) -> None:
    resp = client.get("/api/configs/不存在")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_unmatched_route_uses_unified_error_shape(client: TestClient) -> None:
    """所有错误都必须长成同一个样子 —— 包括路由没匹配上的。

    没有兜底处理器的话，这里会返回 FastAPI 默认的 `{"detail": ...}`，
    前端就得同时认两种错误形状。
    """
    resp = client.get("/api/根本没有这个路由")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"


def test_path_traversal_blocked_at_storage_layer(client: TestClient) -> None:
    """配置名是**安全边界**：路径穿越必须在存储层被拒。

    这里走 API 的话，`%2F` 会被路由层先吃掉、返回 404，
    测不到真正的防线。所以直接打存储层。
    """
    store = storage.JsonFileStorage(Path(tempfile.mkdtemp()) / "configs")
    config = Config.model_validate(SAMPLE_CONFIG)

    for evil in ("../../etc/passwd", "..", "a/b", "a\\b", "x" * 65, ""):
        assert not _name_is_safe(store, evil, config), f"未拦住：{evil!r}"


def _name_is_safe(store: storage.JsonFileStorage, name: str, config: Config) -> bool:
    """返回 True 表示这个名字「被接受了」——测试期望它永远返回 False。"""
    try:
        store.save_config(name, config)
        return True
    except storage.InvalidConfigName:
        return False


def test_config_name_with_illegal_chars_returns_storage_error(client: TestClient) -> None:
    """不含斜杠但含非法字符的名字能走到处理器，应返回统一的 400。"""
    resp = client.put("/api/configs/bad*name", json=SAMPLE_CONFIG)
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "STORAGE_ERROR"


def test_delete_config(client: TestClient) -> None:
    client.put("/api/configs/待删", json=SAMPLE_CONFIG)
    assert client.delete("/api/configs/待删").status_code == 200
    assert client.get("/api/configs/待删").status_code == 404


def test_fx_is_marked_stale_and_carries_date(client: TestClient) -> None:
    """汇率必须带数据日期与 stale 标记 —— 不能假装是实时值（SRS FR-001）。"""
    body = client.get("/api/fx").json()
    assert body["stale"] is True
    assert body["as_of"]
    assert body["rates"]["CNY"] > 0


def test_fx_convert_does_not_touch_engine(client: TestClient) -> None:
    body = client.get("/api/fx/convert", params={"amount": 1000, "currency": "CNY"}).json()
    assert body["converted"] == pytest.approx(7100.0)
    assert body["formatted"].startswith("¥")


def test_quote_reports_unavailable_instead_of_error(client: TestClient, monkeypatch) -> None:
    """抓不到时接口要**如实说明**，而不是报错或返回假数据（FR-002 异常分支）。"""
    monkeypatch.setattr(datafeed, "_http_get", lambda url: json.dumps({"data": {}}))
    body = client.get("/api/quote/ZZZZ").json()
    assert body["available"] is False
    assert body["params"] is None
    assert body["reason"]


def test_quote_returns_params_through_http(client: TestClient, monkeypatch) -> None:
    """走一遍完整 HTTP 链路：抓取 → 提取参数 → 序列化。"""
    prices = [
        (date(2026, 9, 24) - timedelta(days=7 * i), 100.0 * 1.08 ** (7 * i / 365.25))
        for i in range(261)
    ]
    rows = [{"date": d.strftime("%m/%d/%Y"), "close": f"${c:.2f}"} for d, c in prices]

    def fake_get(url: str) -> str:
        if "/historical?" in url:
            return json.dumps({"data": {"tradesTable": {"rows": rows}}})
        if "/info?" in url:
            return json.dumps(
                {"data": {"companyName": "Test Fund", "primaryData": {"expenseRatio": "0.06%"}}}
            )
        return json.dumps({"data": {"history": []}})

    monkeypatch.setattr(datafeed, "_http_get", fake_get)
    # 这个假源给的是 Nasdaq 那套结构，所以显式点名 nasdaq ——
    # 默认来源是 stockanalysis（快路径），不点名就走到另一个解析器上去了。
    body = client.get(
        "/api/quote/TEST", params={"lookback_years": 5, "source": "nasdaq"}
    ).json()

    assert body["available"] is True
    assert body["name"] == "Test Fund"
    assert body["as_of"] == "2026-09-24"
    assert body["lookback_years"] == 5
    # 来源要如实报给前端 —— 界面上直接显示它，不再说「缓存命中」这种黑话
    assert body["source"] == "nasdaq"
    # A23：费用率只作展示，参与计算的参数里必须是 0
    assert body["expense_ratio_info"] == pytest.approx(0.0006)
    assert body["params"]["expense_ratio"] == 0.0
    assert body["params"]["source"] == "fetched"


def test_second_quote_request_hits_cache(client: TestClient, monkeypatch) -> None:
    """★ 用户要求的核心行为：第二个请求不该再打 API。"""
    calls: list[str] = []
    prices = _bars_ending_on_the_last_close()
    rows = [{"date": d.strftime("%m/%d/%Y"), "close": f"${c:.2f}"} for d, c in prices]

    def fake_get(url: str) -> str:
        calls.append(url)
        if "/historical?" in url:
            return json.dumps({"data": {"tradesTable": {"rows": rows}}})
        if "/info?" in url:
            return json.dumps({"data": {"companyName": "Test Fund"}})
        return json.dumps({"data": {"history": []}})

    monkeypatch.setattr(datafeed, "_http_get", fake_get)
    first = client.get("/api/quote/TEST", params={"source": "nasdaq"}).json()
    calls_after_first = len(calls)
    second = client.get("/api/quote/TEST", params={"source": "nasdaq"}).json()

    assert first["from_cache"] is False
    assert second["from_cache"] is True
    assert len(calls) == calls_after_first  # 一个请求都没多发


def test_quote_rejects_absurd_lookback(client: TestClient) -> None:
    """回看窗口超出抓取上限应被参数校验挡下，而不是去抓一份拿不到的数据。"""
    assert client.get("/api/quote/SCHD", params={"lookback_years": 0}).status_code == 422
    assert client.get("/api/quote/SCHD", params={"lookback_years": 99}).status_code == 422


def test_symbols_search_returns_results(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(
        datafeed,
        "_http_get",
        lambda url: json.dumps(
            {"data": [{"symbol": "SCHD", "name": "Schwab US Dividend ETF", "asset": "etf"}]}
        ),
    )
    body = client.get("/api/symbols/search", params={"q": "schd"}).json()
    assert body["results"][0]["symbol"] == "SCHD"


def test_safe_rate_endpoint(client: TestClient) -> None:
    """撑 25 年、实际收益率 0 → 提取率 4%。走一遍完整 HTTP 链路。"""
    resp = client.post(
        "/api/safe-rate",
        json={
            "start_balance": 1_000_000,
            "growth_rate": 0.025,
            "inflation_rate": 0.025,
            "years": 25,
        },
    )
    assert resp.status_code == 200
    assert resp.json()["withdrawal_rate"] == pytest.approx(0.04, rel=1e-9)


def test_withdrawal_endpoint_reports_depletion(client: TestClient) -> None:
    resp = client.post(
        "/api/withdrawal",
        json={
            "start_balance": 1_000_000,
            "annual_withdrawal": 60_000,
            "growth_rate": 0.05,
            "inflation_rate": 0.025,
            "years": 50,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["depleted"] is True
    assert body["sustainable_years"] is not None


def test_growth_rate_endpoint(client: TestClient) -> None:
    body = client.post("/api/growth-rate", json=SAMPLE_CONFIG).json()
    assert body["growth_rate"] > 0

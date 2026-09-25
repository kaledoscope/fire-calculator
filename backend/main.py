"""main.py —— FastAPI 壳（SRS §2.1 分层纪律）。

宪法铁律 Ⅱ：本模块是**唯一的 HTTP 出口**。所有路由、请求校验、错误格式
都在这里，别的模块一律不知道 HTTP 的存在。

`/api/compute` 是**无状态纯函数接口** —— 不读数据库、不读磁盘、不读系统时间。
配置存哪是 storage.py 的事，与计算无关。
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend import __version__, datafeed, fx
from backend.engine import (
    blended_growth_rate,
    project_withdrawal,
    simulate,
    solve_safe_withdrawal_rate,
)
from backend.models import (
    Config,
    Currency,
    SafeRateResult,
    SimulationResult,
    WithdrawalResult,
)
from backend.storage import (
    ConfigNotFound,
    StorageError,
    get_storage,
)

app = FastAPI(
    title="W-DCA-Planner API",
    version=__version__,
    description="FIRE 计算器：确定性外推 + 分段式定投",
)

# 开发期前端跑在 Vite 的 5173 端口，与后端不同源，需要放行。
# 注意只放行本机 —— 这是本机工具，不是公开服务（SRS §4.3）。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════
# 统一错误格式（SRS §5.2）
# ══════════════════════════════════════════════════════════════════


def _error(code: str, message: str, field: str | None = None) -> dict:
    return {"error": {"code": code, "message": message, "field": field}}


@app.exception_handler(RequestValidationError)
async def _validation_handler(_request, exc: RequestValidationError) -> JSONResponse:
    """把 Pydantic 的校验错误压成统一格式。

    `field` 要能直接对应到前端的输入框 —— 前端据此高亮出错的那一格，
    而不是弹一个不知道指哪儿的红条。
    """
    first = exc.errors()[0] if exc.errors() else {}
    loc = [str(p) for p in first.get("loc", []) if p not in ("body",)]
    return JSONResponse(
        status_code=422,
        content=_error(
            code="VALIDATION_ERROR",
            message=first.get("msg", "输入校验未通过"),
            field=".".join(loc) or None,
        ),
    )


@app.exception_handler(ValueError)
async def _value_error_handler(_request, exc: ValueError) -> JSONResponse:
    return JSONResponse(
        status_code=422, content=_error(code="INVALID_INPUT", message=str(exc))
    )


@app.exception_handler(ConfigNotFound)
async def _not_found_handler(_request, exc: ConfigNotFound) -> JSONResponse:
    return JSONResponse(
        status_code=404, content=_error(code="NOT_FOUND", message=str(exc))
    )


@app.exception_handler(StarletteHTTPException)
async def _http_handler(_request, exc: StarletteHTTPException) -> JSONResponse:
    """兜住所有走不到上面处理器的 HTTP 错误（路由未匹配、方法不对等）。

    没有这一层的话，FastAPI 会用它默认的 `{"detail": ...}` 格式，
    前端就得同时认两种错误形状 —— 「格式统一」也就名存实亡了。
    """
    codes = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}
    return JSONResponse(
        status_code=exc.status_code,
        content=_error(
            code=codes.get(exc.status_code, "HTTP_ERROR"), message=str(exc.detail)
        ),
    )


@app.exception_handler(StorageError)
async def _storage_handler(_request, exc: StorageError) -> JSONResponse:
    return JSONResponse(
        status_code=400, content=_error(code="STORAGE_ERROR", message=str(exc))
    )


# ══════════════════════════════════════════════════════════════════
# 计算
# ══════════════════════════════════════════════════════════════════


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/api/compute", response_model=SimulationResult)
def compute(config: Config) -> SimulationResult:
    """FR-007/008/012/014：增长外推 + 再平衡 + 里程碑 + 敏感度。

    无状态：同一个 config 永远得到同一个结果（SRS §4.2）。
    """
    return simulate(config)


class WithdrawalRequest(BaseModel):
    start_balance: float = Field(..., gt=0)
    annual_withdrawal: float = Field(..., gt=0, description="初始年取款额")
    growth_rate: float = Field(..., description="组合年化增长率")
    inflation_rate: float = Field(0.025)
    years: int = Field(..., gt=0, le=100)
    capital_gains_tax: float = Field(0.0, ge=0.0, le=1.0)


@app.post("/api/withdrawal", response_model=WithdrawalResult)
def withdrawal(req: WithdrawalRequest) -> WithdrawalResult:
    """FR-009：取款期模拟 —— 按这个金额取，能撑多少年。"""
    return project_withdrawal(**req.model_dump())


class SafeRateRequest(BaseModel):
    start_balance: float = Field(..., gt=0)
    growth_rate: float
    inflation_rate: float = 0.025
    years: int = Field(..., gt=0, le=100, description="想撑多少年")
    end_balance_target: float = Field(0.0, ge=0.0)
    capital_gains_tax: float = Field(0.0, ge=0.0, le=1.0)


@app.post("/api/safe-rate", response_model=SafeRateResult)
def safe_rate(req: SafeRateRequest) -> SafeRateResult:
    """FR-010：提取率反推 —— 想撑 N 年，每年最多能取多少。"""
    return solve_safe_withdrawal_rate(**req.model_dump())


@app.post("/api/growth-rate")
def growth_rate(config: Config) -> dict:
    """从组合推出混合增长率，供取款期模拟与反推使用。

    单独做成接口而不是让前端自己算 —— 前端不做任何计算（SRS §2.1）。
    """
    return {"growth_rate": blended_growth_rate(config)}


# ══════════════════════════════════════════════════════════════════
# 配置持久化（FR-018）
# ══════════════════════════════════════════════════════════════════


@app.get("/api/configs")
def list_configs() -> dict:
    return {"configs": get_storage().list_configs()}


@app.get("/api/configs/{name}", response_model=Config)
def load_config(name: str) -> Config:
    config = get_storage().load_config(name)
    if config is None:
        raise ConfigNotFound(f"配置「{name}」不存在")
    return config


@app.put("/api/configs/{name}")
def save_config(name: str, config: Config) -> dict:
    get_storage().save_config(name, config)
    return {"saved": name}


@app.delete("/api/configs/{name}")
def delete_config(name: str) -> dict:
    get_storage().delete_config(name)
    return {"deleted": name}


# ══════════════════════════════════════════════════════════════════
# 展示层：汇率（FX-1，绝不参与计算）
# ══════════════════════════════════════════════════════════════════


@app.get("/api/fx")
def get_fx() -> dict:
    """取汇率。抓取失败时返回回退值并带 `stale` 标记（SRS FR-001 异常分支）。"""
    rates = fx.get_rates()
    return rates.model_dump()


@app.get("/api/fx/convert")
def convert_fx(amount: float, currency: Currency) -> dict:
    rates = fx.get_rates()
    return {
        "amount_usd": amount,
        "currency": currency.value,
        "converted": fx.convert(amount, currency, rates),
        "formatted": fx.format_money(fx.convert(amount, currency, rates), currency),
        "as_of": rates.as_of,
        "stale": rates.stale,
    }


# ══════════════════════════════════════════════════════════════════
# 第 5 期：数据抓取（FR-005）
# ══════════════════════════════════════════════════════════════════


@app.get("/api/quote/{symbol}")
def quote(
    symbol: str,
    lookback_years: int = Query(10, ge=1, le=datafeed.MAX_YEARS),
    source: str = Query(
        datafeed.SOURCE_STOCKANALYSIS,
        description="价格来源。前端**并行**请求两个：stockanalysis 先渲染，nasdaq 后到覆盖。",
    ),
    force: bool = False,
) -> dict:
    """FR-005：取标的参数。**一次只负责一个来源。**

    **缓存命中时一个网络请求都不发** —— 判据是「缓存里的最新 K 线是否已
    覆盖最近一个已收盘的美股交易日」，这在一天之内是常量，所以盘中反复
    改参数、来回切标的都不会产生请求。

    抓不到东西时**不报错**，而是返回 `available: false` 加上人话说明，
    前端据此提示用户手输参数（SRS FR-002 的异常分支）。
    """
    # 这个路由是同步的，FastAPI 会把它丢进线程池，不会卡住事件循环。
    result = datafeed.get_quote(symbol, lookback_years, source=source, force=force)
    return result.model_dump()


@app.get("/api/symbols/search")
def symbols_search(q: str = "", limit: int = 10) -> dict:
    """按关键字找标的（FR-002 的可选辅助）。抓不到就返回空列表。"""
    return {"results": datafeed.search_symbols(q, limit)}


# ══════════════════════════════════════════════════════════════════
# 生产模式：托管前端构建产物（存在才挂载，开发期不影响）
# ══════════════════════════════════════════════════════════════════

_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"

if _DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=_DIST / "assets"), name="assets")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_DIST / "index.html")

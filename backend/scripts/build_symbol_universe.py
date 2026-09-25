"""生成美股代码清单快照 `backend/data/symbols_us.txt`。

════════════════════════════════════════════════════════════════════
为什么是**快照**而不是运行时去查
════════════════════════════════════════════════════════════════════

需求是「用户输完代码，立刻告诉他这个代码存不存在」。这要求判断必须在
**毫秒级、且不依赖网络** —— 而任何一份完整的美股代码清单都要下载近兆字节，
运行时去拉会让每次输入都卡上几秒。

于是：**离线拉一次、随仓库带走、运行时零网络**。

代价是清单会过时。但过时只意味着「刚上市的新代码会被提示可能不存在」——
而那条提示是**软提示**（用户确认无误仍可手输参数），不是拒绝。
用「偶尔多问一句」换「每次输入都快且一定能答」，这笔账是划算的。

════════════════════════════════════════════════════════════════════
为什么是 stockanalysis 而不是 Nasdaq Trader
════════════════════════════════════════════════════════════════════

Nasdaq Trader 的官方代码目录（`nasdaqlisted.txt` / `otherlisted.txt`）本该是
首选，但**本机够不着**：那个 CDN 挂在 Incapsula 后面，实测一个文件要 243 秒，
另一个反复断在 316KB / 349KB，多试几次就直接开始回机器人挑战页。

退而求其次用 SEC 的 `company_tickers.json` 也不行 —— 它列的是**注册发行人**，
ETF 基本不在里面（实测 QQQ / SCHD / VOO / VTI / AGG / VXUS / BND 全部缺失，
只有 SPY 因为自己就是一个注册信托才在）。而本项目面向的正是 ETF 投资者：
一份「查不到 QQQ」的清单比没有清单更糟，它会主动冤枉用户。

最后落在 stockanalysis 上：与快路径价格同源，本机实测两个接口各约 2 秒、
稳定可达，而且**顺带给出名称**——这正是快路径缺的那一块（Nasdaq 的 info
接口要几十秒，快路径不能调它）。

⚠️ 与价格端点一样，`/_api/endpoints/screener/initial` 也是他家官网自用的
未公开接口，没有稳定性承诺。但它只影响**重新生成**，不影响运行时：
文件已在仓库里，接口挂了也只是暂时更新不了。

重新生成（代码目录变化很慢，不需要经常跑）：

    uv run python backend/scripts/build_symbol_universe.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "data" / "symbols_us.txt"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# 个股与 ETF 是两个**互不重叠**的接口，缺一个都会漏掉一大类标的 ——
# 个股那份不含 ETF，ETF 那份不含个股。所以任何一份失败都必须整体作废。
SOURCES = {
    "stock": "https://stockanalysis.com/_api/endpoints/screener/initial?type=stock",
    "etf": "https://stockanalysis.com/_api/endpoints/screener/initial?type=etf",
}

TIMEOUT = 90
RETRIES = 3

# 少于此数就认为拉残了。一份不完整的清单会把正常代码判成「不存在」，
# 那比没有清单更糟：它会主动误导用户。
MIN_TOTAL = 5000


def fetch(url: str) -> str:
    last: Exception | None = None
    for attempt in range(RETRIES):
        request = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read().decode("utf-8", errors="replace")
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = exc
            print(f"    第 {attempt + 1} 次失败：{type(exc).__name__}: {exc}", file=sys.stderr)
    raise RuntimeError(f"重试 {RETRIES} 次仍失败：{last}")


def parse(text: str) -> dict[str, str]:
    """`{"data": {"data": [{"s": 代码, "n": 名称}, …]}}` → {代码: 名称}。"""
    payload = json.loads(text)
    rows = ((payload.get("data") or {}).get("data")) or []
    out: dict[str, str] = {}
    for row in rows:
        symbol = (row.get("s") or "").strip().upper()
        if not symbol or len(symbol) > 12:
            continue
        # 名称里的制表符会把 TSV 撑成两列，读的时候就会把半个名字当代码
        name = (row.get("n") or "").replace("\t", " ").strip()
        out[symbol] = name or symbol
    return out


def main() -> int:
    universe: dict[str, str] = {}
    for kind, url in SOURCES.items():
        print(f"拉取 {kind} …", flush=True)
        try:
            rows = parse(fetch(url))
        except Exception as exc:  # noqa: BLE001
            print(f"  失败：{type(exc).__name__}: {exc}", file=sys.stderr)
            # 整体作废：只写个股不写 ETF（或反之）会让另一大半标的被误判
            print("  两份必须都在，本次不写入。", file=sys.stderr)
            return 1
        print(f"  {len(rows)} 个标的", flush=True)
        universe.update(rows)

    if len(universe) < MIN_TOTAL:
        print(f"只拿到 {len(universe)} 个，明显不完整，拒绝写入。", file=sys.stderr)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    # 头几行是给**人**看的：清单会过时，用户有权知道它是什么时候的。
    header = [
        f"# 美股代码清单快照 · 生成于 {date.today().isoformat()} · {len(universe)} 个标的",
        "# 由 backend/scripts/build_symbol_universe.py 生成，勿手工编辑。",
        "# 格式：代码 <TAB> 名称",
    ]
    lines = [f"{s}\t{n}" for s, n in sorted(universe.items())]
    OUT.write_text("\n".join(header + lines) + "\n", encoding="utf-8")
    print(f"\n写入 {OUT}：{len(lines)} 个标的，{OUT.stat().st_size:,} 字节")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

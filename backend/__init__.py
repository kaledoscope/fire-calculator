"""W-DCA-Planner 后端。

模块一览（宪法铁律 Ⅱ：每一层都可被单独替换）：

    models.py     共享地基 —— 数据结构与校验，无计算无 IO
    engine.py     核心 —— 纯计算，零 IO
    storage.py    存储抽象 —— 现在 JSON 文件，将来 PostgreSQL
    fx.py         展示层 —— 汇率换算，不参与计算，可整个删除
    datafeed.py   第 5 期 —— 抓历史行情，失败不阻塞
    main.py       壳 —— FastAPI，唯一的 HTTP 出口
"""

__version__ = "0.1.0"

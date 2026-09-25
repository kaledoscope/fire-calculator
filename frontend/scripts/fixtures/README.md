# 自检夹具

`result.sample.json` —— 一份**真实**的 `/api/compute` 响应，用于
`npm run check:render`（前端渲染自检）。

## 怎么重新抓取

后端模型（`backend/models.py` 的输出类）改动后，这份夹具会**陈旧**，
渲染自检仍然会通过，但它验的就不再是当前的契约了。重新抓一次：

```bash
# 终端 A
uv run uvicorn backend.main:app --port 8000

# 终端 B（仓库根目录）
uv run python -c "
from backend.models import Config
from backend.defaults import demo_config   # 或任意一份配置
" 2>/dev/null || true

curl -s -X POST localhost:8000/api/compute \
  -H 'Content-Type: application/json' \
  -d @../config.example.json \
  -o frontend/scripts/fixtures/result.sample.json
```

（`config.example.json` 在仓库根目录，就是一份能算出完整结果的配置。）

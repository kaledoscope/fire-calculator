/**
 * 后端客户端。
 *
 * 前端**不做任何计算**（SRS §2.1 分层纪律）—— 所有数字都从后端来。
 * 这里只负责把统一错误格式拆成可用的形状。
 */

const BASE = '/api'

export class ApiError extends Error {
  constructor(message, { code, field, status } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.field = field
    this.status = status
  }
}

function parse(text) {
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function request(path, { method = 'GET', body, signal } = {}) {
  let res
  try {
    res = await fetch(BASE + path, {
      method,
      headers: body === undefined ? undefined : { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    if (cause?.name === 'AbortError') throw cause
    throw new ApiError('连不上后端。确认 uvicorn 已在 8000 端口运行。', {
      code: 'NETWORK',
    })
  }

  const text = await res.text()
  const data = text ? parse(text) : null

  if (!res.ok) {
    // 后端保证所有错误都是 {error: {code, message, field}}（SRS §5.2）
    const err = data?.error ?? {}
    throw new ApiError(err.message ?? `请求失败（HTTP ${res.status}）`, {
      code: err.code,
      field: err.field,
      status: res.status,
    })
  }
  return data
}

export const api = {
  health: () => request('/health'),

  /** FR-007/008/012/014：增长外推。无状态纯函数接口。 */
  compute: (config, signal) =>
    request('/compute', { method: 'POST', body: config, signal }),

  /**
   * FR-005：按标的取历史参数。**一次一个来源。**
   *
   * 后端按天缓存：只要「最近一个已收盘的美股交易日」没变，这个调用就是
   * 纯读本地文件、一个网络请求都不发。所以前端可以放心地在每次输入
   * 标的代码后调用它。
   *
   * `source` 由调用方点名：stockanalysis 快（约 1 秒），Nasdaq 慢但更规范。
   * 前端两个**并行**发，谁先回谁先渲染，Nasdaq 后到再覆盖。
   */
  quote: (symbol, lookbackYears = 10, { source = 'stockanalysis', force = false, signal } = {}) =>
    request(
      `/quote/${encodeURIComponent(symbol)}?lookback_years=${lookbackYears}` +
        `&source=${encodeURIComponent(source)}` +
        (force ? '&force=true' : ''),
      { signal },
    ),

  /** FR-002 辅助：按关键字找标的。 */
  searchSymbols: (q, signal) =>
    request(`/symbols/search?q=${encodeURIComponent(q)}`, { signal }),

  /** 展示层汇率，绝不参与计算。 */
  fx: () => request('/fx'),

  /** FR-018：配置持久化。 */
  listConfigs: () => request('/configs'),
  loadConfig: (name) => request(`/configs/${encodeURIComponent(name)}`),
  saveConfig: (name, config) =>
    request(`/configs/${encodeURIComponent(name)}`, { method: 'PUT', body: config }),
  deleteConfig: (name) =>
    request(`/configs/${encodeURIComponent(name)}`, { method: 'DELETE' }),

  /** FR-010：提取率反推。 */
  safeRate: (payload) => request('/safe-rate', { method: 'POST', body: payload }),

  /** FR-009：取款期模拟。 */
  withdrawal: (payload) => request('/withdrawal', { method: 'POST', body: payload }),

  /** 组合混合增长率 —— 前端不自己算。 */
  growthRate: (config) => request('/growth-rate', { method: 'POST', body: config }),
}

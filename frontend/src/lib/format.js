/** 展示层格式化。汇率换算只在这里发生，绝不回流到计算。 */

const SYMBOLS = { USD: '$', CNY: '¥', JPY: '¥' }
const DECIMALS = { USD: 2, CNY: 2, JPY: 0 }

export function money(amountUsd, currency = 'USD', rates = null) {
  const rate = currency === 'USD' ? 1 : (rates?.rates?.[currency] ?? 1)
  const value = amountUsd * rate
  const decimals = DECIMALS[currency] ?? 2
  return `${SYMBOLS[currency] ?? ''}${value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })}`
}

/** 大额金额的紧凑写法，用于概览卡片：$1.23M */
export function moneyCompact(amountUsd, currency = 'USD', rates = null) {
  const rate = currency === 'USD' ? 1 : (rates?.rates?.[currency] ?? 1)
  const value = amountUsd * rate
  const sign = SYMBOLS[currency] ?? ''
  const abs = Math.abs(value)

  if (abs >= 1e8) return `${sign}${(value / 1e8).toFixed(2)}亿`
  if (abs >= 1e4 && currency !== 'USD' && currency !== 'JPY')
    return `${sign}${(value / 1e4).toFixed(2)}万`
  if (abs >= 1e6) return `${sign}${(value / 1e6).toFixed(2)}M`
  if (abs >= 1e3) return `${sign}${(value / 1e3).toFixed(1)}K`

  const decimals = DECIMALS[currency] ?? 2
  return `${sign}${value.toFixed(decimals)}`
}

export function percent(value, decimals = 2) {
  if (value === null || value === undefined) return '—'
  return `${(value * 100).toFixed(decimals)}%`
}

export function shares(value) {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: 0,
    maximumFractionDigits: 2,
  })
}

/** 月份序号 → 「12 年 3 个月」。 */
export function months(month) {
  if (month === null || month === undefined) return '—'
  const y = Math.floor(month / 12)
  const m = month % 12
  if (y === 0) return `${m} 个月`
  if (m === 0) return `${y} 年`
  return `${y} 年 ${m} 个月`
}

/** 相对基准的变化，用于敏感度：「+3 年 2 个月」 */
export function monthDelta(base, value) {
  if (value === null || value === undefined) return '未达成'
  const delta = value - base
  if (delta === 0) return '无变化'
  return `${delta > 0 ? '+' : '−'}${months(Math.abs(delta))}`
}

export function number(value, decimals = 2) {
  return value.toLocaleString('en-US', {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  })
}

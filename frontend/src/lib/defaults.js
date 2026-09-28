/**
 * 起始配置。
 *
 * 刻意给一组能立刻算出结果的参数 —— 打开就能看到数，而不是一片空表。
 * 这些数字是**示例**，不是建议；真实参数第 5 期由数据抓取填充。
 */

export function defaultAsset(symbol = '', weight = 0) {
  return {
    symbol,
    target_weight: weight,
    shares: 0,
    price: 100,
    params: {
      price_growth: 0.07,
      dividend_yield: 0.02,
      dividend_growth: 0.05,
      // A23 费用率陷阱：手输参数默认允许填费用率，抓取模式才强制 0
      expense_ratio: 0,
      source: 'manual',
      lookback_years: 10,
    },
  }
}

export function defaultConfig() {
  return {
    market: 'US',
    display_currency: 'USD',
    assets: [
      {
        symbol: 'SCHD',
        target_weight: 0.6,
        shares: 100,
        price: 28,
        params: {
          price_growth: 0.05,
          dividend_yield: 0.035,
          dividend_growth: 0.06,
          expense_ratio: 0,
          source: 'manual',
          lookback_years: 10,
        },
      },
      {
        symbol: 'NVDA',
        target_weight: 0.3,
        shares: 20,
        price: 180,
        params: {
          price_growth: 0.12,
          dividend_yield: 0.001,
          dividend_growth: 0.15,
          expense_ratio: 0,
          source: 'manual',
          lookback_years: 10,
        },
      },
    ],
    cash: { annual_rate: 0.02 },
    tax: { dividend_tax: 0.1, capital_gains_tax: 0 },
    plan: {
      segments: [{ months: 120, frequency_months: 1, monthly: { SCHD: 1200, NVDA: 800 } }],
    },
    fire: {
      tiers: [
        { name: 'Lean', annual_expense: 30000, multiple: 25 },
        { name: 'Regular', annual_expense: 60000, multiple: 25 },
        { name: 'Fat', annual_expense: 120000, multiple: 25 },
      ],
      // 社区常见节点（见 SRS FR-006 里程碑预设档位）。
      // 标签不带货币符号 —— 金额是美元，显示时会按所选货币换算，
      // 写死「$100,000」在日元视图下会变成「$100,000 ¥100,000」。
      milestones: [
        { label: '10K', kind: 'fixed', amount: 10000 },
        { label: '100K', kind: 'fixed', amount: 100000 },
        { label: '250K', kind: 'fixed', amount: 250000 },
        { label: '500K', kind: 'fixed', amount: 500000 },
        { label: '1M', kind: 'fixed', amount: 1000000 },
      ],
      coast: {
        annual_expense: 60000,
        multiple: 25,
        years_to_retirement: 20,
        growth_rate: 0.07,
      },
      barista: { annual_expense: 60000, part_time_income: 20000, multiple: 25 },
    },
    settings: {
      horizon_months: 360,
      dividend_mode: 'drip',
      rebalance_annually: true,
      inflation_rate: 0.025,
    },
  }
}

/** 从一份配置深拷贝出可安全编辑的副本。 */
export function clone(value) {
  return JSON.parse(JSON.stringify(value))
}

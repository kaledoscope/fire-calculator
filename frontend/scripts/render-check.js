/**
 * 渲染自检 —— `npm run check:render`
 *
 * 构建只证明「编译得过」，证明不了「渲染得出来」。模板里一次越界访问
 * （比如 result 为 null 时读 last.total_value）编译期完全看不出来，
 * 运行时却会白屏。这个脚本把结果类组件用 SSR 真渲染一遍：
 * 模板、$derived、格式化函数全部真跑，出错就抛并以非零码退出。
 *
 * 每个组件都跑**两组数据**：真实数据 + 空数据。空数据那一组是关键 ——
 * 用户删光标的、清空 FIRE 目标时走的就是这条路径，而它最容易崩。
 *
 * 数据来源：`fixtures/result.sample.json`，一份抓取的真实接口响应。
 * ⚠️ 后端模型若改动，这份夹具会变陈旧，需重新抓取（见文件头注释）。
 */
import { render } from 'svelte/server'

import FireOverview from '../src/lib/components/FireOverview.svelte'
import GoalInput from '../src/lib/components/GoalInput.svelte'
import GrowthChart from '../src/lib/components/GrowthChart.svelte'
import ResultsPanel from '../src/lib/components/ResultsPanel.svelte'
import SensitivityTable from '../src/lib/components/SensitivityTable.svelte'
import WithdrawalPanel from '../src/lib/components/WithdrawalPanel.svelte'
import YearlyTable from '../src/lib/components/YearlyTable.svelte'
import result from './fixtures/result.sample.json'
import incomeResult from './fixtures/result.income.sample.json'

const monthly = result.monthly

/** 一份最小的 FIRE 目标配置，够 GoalInput 渲染即可（它只读 fire 这一段）。 */
function fireInput(mode, goals = 2, tiers = 3) {
  return {
    fire: {
      mode,
      tiers: Array.from({ length: tiers }, (_, i) => ({
        name: ['Lean', 'Regular', 'Fat'][i],
        annual_expense: [30000, 60000, 120000][i],
        multiple: 25,
      })),
      income_goals: Array.from({ length: goals }, (_, i) => ({
        label: '',
        monthly_expense: [700, 1500][i] ?? 3000,
        inflation_adjusted: i % 2 === 0,
      })),
    },
  }
}

const cases = [
  ['ResultsPanel', ResultsPanel, { result, growthRate: 0.086, currency: 'USD', rates: null, loading: false }],
  ['ResultsPanel·空', ResultsPanel, { result: null, growthRate: 0, currency: 'USD', rates: null, loading: false }],
  ['ResultsPanel·载入中', ResultsPanel, { result: null, growthRate: 0, currency: 'CNY', rates: null, loading: true }],
  // 改参数之后、点「计算」之前 —— 用户每次打字都会停在这个状态上
  ['ResultsPanel·待计算', ResultsPanel, { result, growthRate: 0.086, currency: 'USD', rates: null, loading: false, dirty: true }],
  ['GrowthChart', GrowthChart, { monthly, currency: 'CNY', rates: null }],
  ['GrowthChart·带目标', GrowthChart, { monthly, currency: 'USD', rates: null, goals: result.fire.goals }],
  [
    'GrowthChart·吃息目标',
    GrowthChart,
    { monthly: incomeResult.monthly, currency: 'JPY', rates: null, goals: incomeResult.fire.goals },
  ],
  ['GoalInput·提取', GoalInput, { config: fireInput('withdrawal') }],
  ['GoalInput·吃息', GoalInput, { config: fireInput('income', 2) }],
  // 目标全删光之后走的那条路 —— 空表最容易崩
  ['GoalInput·吃息无目标', GoalInput, { config: fireInput('income', 0) }],
  ['GoalInput·提取无档位', GoalInput, { config: fireInput('withdrawal', 0, 0) }],
  ['FireOverview', FireOverview, { fire: result.fire, monthly, currency: 'USD', rates: null }],
  ['FireOverview·吃息', FireOverview, { fire: incomeResult.fire, monthly: incomeResult.monthly, currency: 'JPY', rates: null }],
  // 股息跑输通胀那条分支：夹具里 real 是正的，得手改一个才走得到
  [
    'FireOverview·吃息跑输通胀',
    FireOverview,
    {
      fire: { ...incomeResult.fire, real_dividend_growth: -0.015 },
      monthly: incomeResult.monthly,
      currency: 'USD',
      rates: null,
    },
  ],
  ['FireOverview·空', FireOverview, { fire: null, monthly: [], currency: 'USD', rates: null }],
  ['SensitivityTable', SensitivityTable, { sensitivity: result.sensitivity, baseMonth: 208, baseLabel: 'Lean FIRE' }],
  ['SensitivityTable·空', SensitivityTable, { sensitivity: [], baseMonth: null, baseLabel: '目标' }],
  ['YearlyTable', YearlyTable, { monthly, currency: 'USD', rates: null }],
  ['YearlyTable·不足一年', YearlyTable, { monthly: monthly.slice(0, 6), currency: 'USD', rates: null }],
  ['WithdrawalPanel', WithdrawalPanel, { startBalance: result.final_value, growthRate: 0.086, currency: 'USD', rates: null }],
]

let failed = 0
for (const [name, Component, props] of cases) {
  try {
    const { body } = render(Component, { props })
    const text = body.replace(/<[^>]*>/g, '').replace(/\s+/g, ' ').trim()
    console.log(`✓ ${name.padEnd(22)} ${String(body.length).padStart(6)} 字节  「${text.slice(0, 58)}…」`)
  } catch (err) {
    failed++
    console.log(`✗ ${name.padEnd(22)} ${err.message}`)
  }
}

console.log(failed === 0 ? '\n全部渲染通过' : `\n${failed} 个失败`)
process.exit(failed === 0 ? 0 : 1)

/**
 * 渲染自检 —— `npm run check:render`
 *
 * 构建只证明「编译得过」，证明不了「渲染得出来」。模板里一次越界访问
 * （比如 result 为 null 时读 last.total_value）编译期完全看不出来，
 * 运行时却会白屏。这个脚本把结果类组件用 SSR 真渲染一遍：
 * 模板、$derived、格式化函数全部真跑，出错就抛并以非零码退出。
 *
 * 每个组件都跑**两组数据**：真实数据 + 空数据。空数据那一组是关键 ——
 * 用户删光标的、清空里程碑时走的就是这条路径，而它最容易崩。
 *
 * 数据来源：`fixtures/result.sample.json`，一份抓取的真实接口响应。
 * ⚠️ 后端模型若改动，这份夹具会变陈旧，需重新抓取（见文件头注释）。
 */
import { render } from 'svelte/server'

import FireOverview from '../src/lib/components/FireOverview.svelte'
import GrowthChart from '../src/lib/components/GrowthChart.svelte'
import MilestoneList from '../src/lib/components/MilestoneList.svelte'
import ResultsPanel from '../src/lib/components/ResultsPanel.svelte'
import SensitivityTable from '../src/lib/components/SensitivityTable.svelte'
import WithdrawalPanel from '../src/lib/components/WithdrawalPanel.svelte'
import YearlyTable from '../src/lib/components/YearlyTable.svelte'
import result from './fixtures/result.sample.json'

const monthly = result.monthly

const cases = [
  ['ResultsPanel', ResultsPanel, { result, growthRate: 0.086, currency: 'USD', rates: null, loading: false }],
  ['ResultsPanel·空', ResultsPanel, { result: null, growthRate: 0, currency: 'USD', rates: null, loading: false }],
  ['ResultsPanel·载入中', ResultsPanel, { result: null, growthRate: 0, currency: 'CNY', rates: null, loading: true }],
  // 改参数之后、点「计算」之前 —— 用户每次打字都会停在这个状态上
  ['ResultsPanel·待计算', ResultsPanel, { result, growthRate: 0.086, currency: 'USD', rates: null, loading: false, dirty: true }],
  ['GrowthChart', GrowthChart, { monthly, currency: 'CNY', rates: null }],
  ['FireOverview', FireOverview, { fire: result.fire, monthly, currency: 'USD', rates: null }],
  ['FireOverview·空', FireOverview, { fire: null, monthly: [], currency: 'USD', rates: null }],
  ['MilestoneList', MilestoneList, { milestones: result.milestones, currency: 'JPY', rates: null }],
  ['MilestoneList·空', MilestoneList, { milestones: [], currency: 'USD', rates: null }],
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

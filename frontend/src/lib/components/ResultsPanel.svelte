<script>
  import Card from './Card.svelte'
  import FireOverview from './FireOverview.svelte'
  import GrowthChart from './GrowthChart.svelte'
  import MilestoneList from './MilestoneList.svelte'
  import SensitivityTable from './SensitivityTable.svelte'
  import WithdrawalPanel from './WithdrawalPanel.svelte'
  import YearlyTable from './YearlyTable.svelte'
  import { money, moneyCompact, months, percent, shares } from '../format.js'

  /** FR-007 结果的呈现层。所有数字都来自后端，这里只排版。
   *
   * `dirty` / `onCompute` 由 `App` 传进来 —— 这里只管把按钮画在对的地方，
   * 「什么时候该重算」是编排层的事（SRS §2.1：这一层不做任何计算）。
   */
  let {
    result = null,
    growthRate = 0,
    currency = 'USD',
    rates = null,
    loading = false,
    dirty = false,
    onCompute = () => {},
  } = $props()

  const monthly = $derived(result?.monthly ?? [])
  const last = $derived(monthly.length ? monthly[monthly.length - 1] : null)

  const gain = $derived(last ? last.total_value - last.total_invested : 0)
  const gainRatio = $derived(last && last.total_value > 0 ? gain / last.total_value : 0)

  /** 敏感度基准：第一个 FIRE 档位，否则最大的固定里程碑。 */
  const baseMilestone = $derived.by(() => {
    const hits = result?.milestones ?? []
    if (!hits.length) return null
    const tier = hits.find((h) => h.label.endsWith(' FIRE'))
    return tier ?? hits[hits.length - 1]
  })

  const finalAssets = $derived(
    [...(last?.assets ?? [])].sort((a, b) => b.value - a.value),
  )
</script>

<!-- 摘要卡**永远渲染**，哪怕还没有结果。
     它头里那个「计算」按钮是用户唯一的显式入口 —— 结果为空时把它一起藏起来，
     等于用户填完参数后无处可点（这正是当初的问题）。 -->
<Card title="期末总值">
  {#snippet headExtra()}
    {#if last}
      <span class="tiny sub">
        第 {months(last.month)} · 混合年化 {percent(growthRate, 2)}
      </span>
    {/if}
    {#if loading}
      <span class="tiny">计算中…</span>
    {:else if dirty && last}
      <!-- 「我改了参数，屏幕上的数还是旧的。」这句话得说出来，否则用户以为
           自己点的「计算」没生效，或者更糟 —— 以为那个数已经更新过了。 -->
      <span class="tiny stale-flag">参数已改 · 待计算</span>
    {/if}
    <button
      class="compute"
      class:dirty
      onclick={onCompute}
      title="按当前参数重新计算（⌘/Ctrl + Enter）"
    >
      计算
    </button>
  {/snippet}

  {#if loading && !last}
    <p class="muted empty">正在计算…</p>
  {:else if !last}
    <!-- 后端保证 monthly 非空（horizon_months > 0），但这里仍然要挡一道：
         一次越界访问会让整页白屏，代价远大于多写一个分支。 -->
    <p class="muted empty">还没有结果。填好左边的参数，点右上角「计算」。</p>
  {:else}
    <div class="headline">
      <strong class="big">{money(last.total_value, currency, rates)}</strong>
    </div>

    <div class="stats">
      <div class="stat">
        <span class="tiny">累计投入</span>
        <strong>{moneyCompact(last.total_invested, currency, rates)}</strong>
      </div>
      <div class="stat">
        <span class="tiny">账面增值</span>
        <strong class="pos">{moneyCompact(gain, currency, rates)}</strong>
        <span class="tiny">占期末 {percent(gainRatio, 0)}</span>
      </div>
      <div class="stat">
        <span class="tiny">累计股息（税前）</span>
        <strong>{moneyCompact(last.cumulative_dividend, currency, rates)}</strong>
      </div>
      <div class="stat">
        <span class="tiny">累计已缴税</span>
        <strong class="neg">{moneyCompact(last.cumulative_tax, currency, rates)}</strong>
      </div>
    </div>

    {#if finalAssets.length > 0}
      <div class="table-wrap breakdown">
        <table>
          <thead>
            <tr>
              <th>标的</th>
              <th class="num">期末股数</th>
              <th class="num">期末单价</th>
              <th class="num">期末市值</th>
              <th class="num">占比</th>
            </tr>
          </thead>
          <tbody>
            {#each finalAssets as a (a.symbol)}
              <tr>
                <td class="symbol">{a.symbol}</td>
                <td class="num">{shares(a.shares)}</td>
                <td class="num">{money(a.price, currency, rates)}</td>
                <td class="num strong">{money(a.value, currency, rates)}</td>
                <td class="num muted-cell">
                  {percent(last.total_value > 0 ? a.value / last.total_value : 0, 1)}
                </td>
              </tr>
            {/each}
            {#if last.cash > 0.01}
              <tr>
                <td class="symbol">现金</td>
                <td class="num">—</td>
                <td class="num">—</td>
                <td class="num strong">{money(last.cash, currency, rates)}</td>
                <td class="num muted-cell">
                  {percent(last.total_value > 0 ? last.cash / last.total_value : 0, 1)}
                </td>
              </tr>
            {/if}
          </tbody>
        </table>
      </div>
    {/if}
  {/if}
</Card>

{#if last}
  <GrowthChart {monthly} {currency} {rates} />

  <FireOverview fire={result.fire} {monthly} {currency} {rates} />

  <MilestoneList milestones={result.milestones} {currency} {rates} />

  <SensitivityTable
    sensitivity={result.sensitivity}
    baseMonth={baseMilestone?.month ?? null}
    baseLabel={baseMilestone?.label ?? '目标'}
  />

  <YearlyTable {monthly} {currency} {rates} />

  <WithdrawalPanel
    startBalance={last.total_value}
    {growthRate}
    currency={currency}
    {rates}
  />
{/if}

<style>
  .empty {
    text-align: center;
    padding: var(--gap-xl) var(--gap);
  }

  /* ── 计算按钮 ──────────────────────────────────────────────── */

  /* 平时是描边的次要按钮，参数一改就自己亮成实心的强调色。
     这比「禁用/启用」好：按钮**一直在那儿**（用户不会找不到），
     而「亮起来」本身就是那句「现在有你没看到的改动」的另一种说法。 */
  .compute {
    font: inherit;
    font-size: 13px;
    font-weight: 500;
    line-height: 1;
    padding: 7px 14px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
    flex: 0 0 auto;
    white-space: nowrap;
    transition:
      background-color 220ms cubic-bezier(0.32, 0.72, 0, 1),
      border-color 220ms cubic-bezier(0.32, 0.72, 0, 1),
      color 220ms cubic-bezier(0.32, 0.72, 0, 1),
      transform 120ms ease;
  }

  .compute:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .compute:active {
    transform: scale(0.96);
  }

  .compute.dirty {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
    box-shadow: 0 1px 3px rgba(0, 113, 227, 0.3);
  }

  .compute.dirty:hover {
    color: #fff;
    filter: brightness(1.06);
  }

  /* 「参数已改 · 待计算」——说的是「屏幕上的数不是现在的参数的数」。
     用次级色而不是警告色：这是流程的一部分，不是出事了。 */
  .stale-flag {
    color: var(--accent);
    white-space: nowrap;
  }

  .headline {
    display: flex;
    flex-direction: column;
    gap: 2px;
    margin-bottom: var(--gap);
  }

  .big {
    font-size: 40px;
    font-weight: 600;
    letter-spacing: -0.025em;
    font-variant-numeric: tabular-nums;
    line-height: 1.15;
    color: var(--text);
  }

  .sub {
    white-space: nowrap;
  }

  .stats {
    display: grid;
    /* 四个数要**排得整齐**：窄了 2×2，宽了一行四个。
       原先用 `repeat(auto-fit, minmax(140px, 1fr))`，碰上「放得下 3 个、
       放不下 4 个」的宽度就变成**三个一行、第四个独占一行还被拉满**，
       看着像另一种信息。auto-fit 只保证每列不窄于 140，不管一行几个好看。
       这里条目数是固定的 4，索性按断点写死。 */
    grid-template-columns: repeat(2, 1fr);
    gap: var(--gap-sm);
    padding-top: var(--gap);
    border-top: 1px solid var(--border);
  }

  @media (min-width: 1280px) {
    .stats {
      grid-template-columns: repeat(4, 1fr);
    }
  }

  .stat {
    display: flex;
    flex-direction: column;
    gap: 1px;
  }

  .stat strong {
    font-size: 18px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }

  .pos {
    color: var(--positive);
  }

  .neg {
    color: var(--text-secondary);
  }

  .breakdown {
    margin-top: var(--gap);
    border-top: 1px solid var(--border);
    padding-top: var(--gap-sm);
  }

  .table-wrap {
    overflow-x: auto;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .symbol {
    font-weight: 500;
  }

  .strong {
    font-weight: 600;
  }

  .muted-cell {
    color: var(--text-tertiary);
  }
</style>

<script>
  import Card from './Card.svelte'
  import { money, moneyCompact, percent } from '../format.js'

  /** 年度明细：按月快照聚合成年（FR-007 输出的一种读数方式） */
  let { monthly = [], currency = 'USD', rates = null } = $props()

  const rows = $derived.by(() => {
    const out = []
    for (let i = 11; i < monthly.length; i += 12) {
      const snap = monthly[i]
      const prev = i >= 12 ? monthly[i - 12] : null
      const startValue = prev ? prev.total_value : 0
      out.push({
        year: snap.month / 12,
        endValue: snap.total_value,
        invested: snap.total_invested,
        gain: snap.total_value - snap.total_invested,
        // 本年增长 = 年末 − 年初 − 本年投入
        yearGrowth:
          snap.total_value - startValue - (snap.total_invested - (prev?.total_invested ?? 0)),
        dividend: snap.cumulative_dividend,
        tax: snap.cumulative_tax,
      })
    }
    return out
  })

  const last = $derived(rows.length ? rows[rows.length - 1] : null)
</script>

<Card title="年度明细">
  {#snippet headExtra()}
    {#if last}
      <span class="tiny">
        {rows.length} 年 · 累计投入 {moneyCompact(last.invested, currency, rates)}
      </span>
    {/if}
  {/snippet}

  {#if rows.length === 0}
    <p class="muted empty">年限不足一年，没有年度数据。</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>年</th>
            <th class="num">年末总值</th>
            <th class="num">累计投入</th>
            <th class="num">本年增长</th>
            <th class="num">累计股息</th>
            <th class="num">累计税</th>
          </tr>
        </thead>
        <tbody>
          {#each rows as row (row.year)}
            <tr>
              <td class="year">{row.year}</td>
              <td class="num strong">{money(row.endValue, currency, rates)}</td>
              <td class="num muted-cell">{money(row.invested, currency, rates)}</td>
              <td class="num" class:pos={row.yearGrowth > 0}>
                {row.yearGrowth > 0 ? '+' : ''}{money(row.yearGrowth, currency, rates)}
              </td>
              <td class="num muted-cell">{money(row.dividend, currency, rates)}</td>
              <td class="num muted-cell">{money(row.tax, currency, rates)}</td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    {#if last}
      <p class="tiny note">
        期末总值里 <strong>{percent(last.gain / last.endValue, 0)}</strong> 是账面增值，
        其余是你自己投进去的本金 —— 时间越长，这个比例越高。
      </p>
    {/if}
  {/if}
</Card>

<style>
  .table-wrap {
    margin: 0 calc(-1 * var(--gap-sm));
    overflow-x: auto;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .year {
    font-variant-numeric: tabular-nums;
    color: var(--text-secondary);
  }

  .strong {
    font-weight: 600;
  }

  .muted-cell {
    color: var(--text-tertiary);
  }

  td.pos {
    color: var(--positive);
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }

  .note {
    margin-top: var(--gap-sm);
    line-height: 1.5;
  }

  .note strong {
    color: var(--text);
  }
</style>

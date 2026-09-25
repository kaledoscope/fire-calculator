<script>
  import Card from './Card.svelte'
  import { api, ApiError } from '../api.js'
  import { money, percent } from '../format.js'

  /** FR-009 取款期模拟 + FR-010 提取率反推 */
  let { startBalance = 0, growthRate = 0.07, inflationRate = 0.025, currency = 'USD', rates = null } =
    $props()

  let balance = $state(0)
  let annual = $state(0)
  let years = $state(30)
  let touched = $state(false)

  let sim = $state(null)
  let safe = $state(null)
  let error = $state(null)
  let busy = $state(false)

  // 初始本金跟着计算结果走，除非用户手动改过
  $effect(() => {
    if (!touched) {
      balance = Math.round(startBalance)
      annual = Math.round(startBalance * 0.04)
    }
  })

  $effect(() => {
    const payload = {
      start_balance: balance,
      annual_withdrawal: annual,
      growth_rate: growthRate,
      inflation_rate: inflationRate,
      years,
      capital_gains_tax: 0,
    }
    const safePayload = {
      start_balance: balance,
      growth_rate: growthRate,
      inflation_rate: inflationRate,
      years,
      end_balance_target: 0,
      capital_gains_tax: 0,
    }
    if (!(balance > 0) || !(annual > 0)) {
      sim = null
      safe = null
      return
    }

    let cancelled = false
    busy = true
    error = null
    Promise.all([api.withdrawal(payload), api.safeRate(safePayload)])
      .then(([a, b]) => {
        if (cancelled) return
        sim = a
        safe = b
      })
      .catch((err) => {
        if (cancelled || err?.name === 'AbortError') return
        error = err instanceof ApiError ? err.message : '算不出来'
        sim = null
        safe = null
      })
      .finally(() => {
        if (!cancelled) busy = false
      })

    return () => {
      cancelled = true
    }
  })

  /** 年数多的时候抽稀：前 5 年逐年，之后每 5 年，最后一年必留。 */
  const shownRows = $derived.by(() => {
    const rows = sim?.rows ?? []
    if (rows.length <= 15) return rows
    return rows.filter(
      (r) => r.year <= 5 || r.year % 5 === 0 || r.year === rows.length,
    )
  })

  const rate = $derived(balance > 0 ? annual / balance : 0)
  const safeRate = $derived(safe?.withdrawal_rate ?? null)
</script>

<Card title="取款期">
  {#snippet headExtra()}
    {#if busy}<span class="tiny">计算中…</span>{/if}
  {/snippet}

  <p class="tiny intro">
    前一个阶段在攒钱，这个阶段在花钱。本金按上面的组合增长率增长，
    每年取一笔、取款额随通胀上涨。
  </p>

  <div class="row">
    <div class="field">
      <label for="wb">起始本金</label>
      <input
        id="wb"
        type="number"
        step="1000"
        min="0"
        bind:value={balance}
        oninput={() => (touched = true)}
      />
    </div>
    <div class="field">
      <label for="wa">年取款额</label>
      <input
        id="wa"
        type="number"
        step="1000"
        min="0"
        bind:value={annual}
        oninput={() => (touched = true)}
      />
      <p class="tiny hint">提取率 {percent(rate, 2)}</p>
    </div>
    <div class="field narrow">
      <label for="wy">撑多少年</label>
      <input id="wy" type="number" step="1" min="1" max="100" bind:value={years} />
    </div>
  </div>

  {#if error}
    <div class="banner error" role="alert">{error}</div>
  {/if}

  {#if sim && safe}
    <div class="verdict" class:bad={sim.depleted}>
      {#if sim.depleted}
        <strong>撑不到 {years} 年。</strong>
        第 <strong>{sim.sustainable_years}</strong> 年就耗尽了 —— 之后无钱可取。
      {:else}
        <strong>撑得住 {years} 年。</strong>
        期末还剩 <strong>{money(sim.final_balance, currency, rates)}</strong>。
      {/if}
    </div>

    <div class="stats">
      <div class="stat">
        <span class="tiny">按 4% 规则的安全年取款</span>
        <strong>{money(Math.round(balance * 0.04), currency, rates)}</strong>
      </div>
      <div class="stat">
        <span class="tiny">想正好撑 {years} 年，每年最多取</span>
        <strong>{money(Math.round(safe.annual_withdrawal), currency, rates)}</strong>
        <span class="tiny">提取率 {percent(safeRate, 2)}</span>
      </div>
    </div>

    <details class="detail">
      <summary>逐年明细</summary>
      <div class="table-wrap">
        <table>
          <thead>
            <tr>
              <th>年</th>
              <th class="num">本年取款</th>
              <th class="num">年末余额</th>
            </tr>
          </thead>
          <tbody>
            {#each shownRows as r (r.year)}
              <tr>
                <td>{r.year}</td>
                <td class="num">{money(r.withdrawal, currency, rates)}</td>
                <td class="num" class:zero={r.end_balance <= 0}>
                  {money(Math.max(0, r.end_balance), currency, rates)}
                </td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
      {#if shownRows.length < (sim.rows.length ?? 0)}
        <p class="tiny hint">中间年份已折叠（每 5 年一档）。</p>
      {/if}
    </details>
  {/if}
</Card>

<style>
  .intro {
    margin-bottom: var(--gap);
    line-height: 1.5;
  }

  .narrow {
    flex: 0 0 130px;
  }

  .hint {
    margin-top: 5px;
  }

  .verdict {
    margin-top: var(--gap);
    padding: 13px 16px;
    border-radius: var(--radius-sm);
    background: color-mix(in srgb, var(--positive) 8%, transparent);
    color: var(--text);
    font-size: 14px;
    line-height: 1.6;
  }

  .verdict.bad {
    background: color-mix(in srgb, var(--negative) 9%, transparent);
  }

  .verdict strong {
    font-weight: 600;
  }

  .verdict.bad strong {
    color: var(--negative);
  }

  .stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: var(--gap-sm);
    margin-top: var(--gap);
  }

  .stat {
    display: flex;
    flex-direction: column;
    gap: 2px;
    padding: 12px 14px;
    background: var(--bg-sunken);
    border-radius: var(--radius-sm);
  }

  .stat strong {
    font-size: 19px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }

  .detail {
    margin-top: var(--gap);
    border-top: 1px solid var(--border);
    padding-top: var(--gap-sm);
  }

  summary {
    font-size: 13px;
    color: var(--text-secondary);
    cursor: pointer;
    list-style: none;
    padding: 4px 0;
  }

  summary::-webkit-details-marker {
    display: none;
  }

  summary::before {
    content: '▸ ';
    color: var(--text-tertiary);
  }

  details[open] summary::before {
    content: '▾ ';
  }

  .table-wrap {
    margin-top: var(--gap-sm);
    max-height: 300px;
    overflow-y: auto;
    overflow-x: auto;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  td.zero {
    color: var(--negative);
  }
</style>

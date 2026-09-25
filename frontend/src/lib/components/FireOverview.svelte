<script>
  import Card from './Card.svelte'
  import { money, moneyCompact, months } from '../format.js'

  /** FR-006 输出侧：FIRE 档位 / Coast / Barista 的达标时间。
   *
   * 这几个数全部由后端算好（`result.fire`）—— 前端只做排版。
   * 年支出 × 倍数、÷(1+g)^n 都是计算，放在前端就等于同一套口径有两个实现。
   */
  let { fire = null, monthly = [], currency = 'USD', rates = null } = $props()

  const rows = $derived.by(() => {
    if (!fire) return []
    const out = []
    for (const t of fire.tiers) out.push({ ...t, kind: '档位', hint: '年支出 × 倍数' })
    if (fire.coast)
      out.push({
        ...fire.coast,
        kind: 'Coast',
        hint: '现在存够这么多，其余交给复利',
      })
    if (fire.barista)
      out.push({
        ...fire.barista,
        kind: 'Barista',
        hint: '兼职收入顶掉一部分开销',
      })
    return out
  })

  const finalValue = $derived(
    monthly.length ? monthly[monthly.length - 1].total_value : 0,
  )
  const maxAmount = $derived(rows.length ? Math.max(...rows.map((r) => r.amount)) : 1)
</script>

<Card title="FIRE 进度">
  {#snippet headExtra()}
    <span class="tiny">期末 {moneyCompact(finalValue, currency, rates)}</span>
  {/snippet}

  {#if rows.length === 0}
    <p class="muted empty">
      还没设定 FIRE 目标。在上方「FIRE 目标」里勾一个档位试试。
    </p>
  {:else}
    <ul class="list">
      {#each rows as r (r.kind + r.label)}
        <li class="item" class:done={r.reached}>
          <div class="top">
            <span class="badge">{r.kind}</span>
            <span class="label">{r.label}</span>
            <span class="amount">{money(r.amount, currency, rates)}</span>
          </div>

          <div class="bar">
            <span
              class="fill"
              style="width: {Math.min(100, (finalValue / r.amount) * 100)}%"
            ></span>
          </div>

          <div class="foot">
            <span class="tiny">{r.hint}</span>
            <span class="when">
              {#if r.reached}
                第 {months(r.month)} 达成
              {:else}
                这个年限内达不到 · 已到 {Math.round((finalValue / r.amount) * 100)}%
              {/if}
            </span>
          </div>
        </li>
      {/each}
    </ul>
  {/if}
</Card>

<style>
  .list {
    display: flex;
    flex-direction: column;
    gap: var(--gap);
  }

  .top {
    display: flex;
    align-items: baseline;
    gap: var(--gap-xs);
    margin-bottom: 7px;
  }

  .badge {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 5px;
    background: var(--bg-sunken);
    color: var(--text-tertiary);
    flex: 0 0 auto;
  }

  .done .badge {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
  }

  .label {
    font-size: 14px;
    font-weight: 500;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .amount {
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }

  .done .amount {
    color: var(--accent);
  }

  .bar {
    height: 5px;
    border-radius: 3px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .fill {
    display: block;
    height: 100%;
    border-radius: 3px;
    background: var(--accent);
    opacity: 0.85;
    transition: width 0.35s cubic-bezier(0.25, 0.1, 0.25, 1);
  }

  .foot {
    display: flex;
    justify-content: space-between;
    gap: var(--gap-sm);
    margin-top: 6px;
  }

  .when {
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--text-secondary);
    text-align: right;
    flex: 0 0 auto;
  }

  .done .when {
    color: var(--accent);
    font-weight: 500;
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
    line-height: 1.5;
  }
</style>

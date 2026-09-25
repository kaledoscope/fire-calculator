<script>
  import Card from './Card.svelte'
  import { money, months } from '../format.js'

  /** FR-012：里程碑达成时间 */
  let { milestones = [], currency = 'USD', rates = null } = $props()

  const reached = $derived(milestones.filter((m) => m.reached))
  const missed = $derived(milestones.filter((m) => !m.reached))

  /** 进度条要有个参照：以「已达成里最大的那个」为满格。 */
  const furthest = $derived(
    reached.length ? Math.max(...reached.map((m) => m.month)) : 1,
  )
</script>

<Card title="里程碑">
  {#snippet headExtra()}
    <span class="tiny">{reached.length} / {milestones.length} 达成</span>
  {/snippet}

  {#if milestones.length === 0}
    <p class="muted empty">还没有设置里程碑。在上方「FIRE 目标」里勾选。</p>
  {:else}
    <ul class="list">
      {#each milestones as m (m.label)}
        <li class="item" class:missed={!m.reached}>
          <div class="top">
            <span class="label">{m.label}</span>
            <span class="amount tiny">{money(m.amount, currency, rates)}</span>
            <span class="when">
              {#if m.reached}
                {months(m.month)}
              {:else}
                <span class="muted">未达成</span>
              {/if}
            </span>
          </div>
          <div class="bar">
            <span
              class="fill"
              style="width: {m.reached ? (m.month / furthest) * 100 : 0}%"
            ></span>
          </div>
        </li>
      {/each}
    </ul>
  {/if}

  {#if missed.length > 0 && reached.length > 0}
    <p class="tiny note">
      未达成的档位在这个年限内够不着 —— 把年限拉长，或者提高月投额。
    </p>
  {/if}
</Card>

<style>
  .list {
    display: flex;
    flex-direction: column;
    gap: var(--gap-sm);
  }

  .item .top {
    display: flex;
    align-items: baseline;
    gap: var(--gap-sm);
    margin-bottom: 6px;
  }

  .label {
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    min-width: 88px;
  }

  .amount {
    flex: 1;
  }

  .when {
    font-size: 13px;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
    color: var(--accent);
  }

  .missed .label {
    color: var(--text-tertiary);
  }

  .bar {
    height: 4px;
    border-radius: 2px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .fill {
    display: block;
    height: 100%;
    background: var(--accent);
    opacity: 0.75;
    border-radius: 2px;
    transition: width 0.35s cubic-bezier(0.25, 0.1, 0.25, 1);
  }

  .missed .bar {
    background: repeating-linear-gradient(
      90deg,
      var(--bg-sunken) 0 4px,
      transparent 4px 8px
    );
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }

  .note {
    margin-top: var(--gap-sm);
    line-height: 1.5;
  }
</style>

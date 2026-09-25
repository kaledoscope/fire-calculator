<script>
  import Card from './Card.svelte'

  /** FR-006 FIRE 目标设定 + 里程碑 */
  let { config, rates = null } = $props()

  // 标签刻意**不带货币符号**：金额是美元，但显示时会被换算成所选货币。
  // 若标签写死「$100,000」，切到日元就会看到「$100,000 ¥100,000」——
  // 标签和金额自相矛盾。货币由金额那一栏统一表达。
  const milestonePresets = [
    { label: '10K', amount: 10000, note: '起步关，多数人在这里第一次感到复利' },
    { label: '100K', amount: 100000, note: '公认最难的一关' },
    { label: '250K', amount: 250000, note: '四分之一' },
    { label: '500K', amount: 500000, note: 'Lean FIRE 领域' },
    { label: '1M', amount: 1000000, note: '传统 FIRE 数字' },
    { label: '2M', amount: 2000000, note: '雪球变雪崩' },
  ]

  function hasMilestone(amount) {
    return config.fire.milestones.some(
      (m) => m.kind === 'fixed' && m.amount === amount,
    )
  }

  function toggleMilestone(preset) {
    const index = config.fire.milestones.findIndex(
      (m) => m.kind === 'fixed' && m.amount === preset.amount,
    )
    if (index >= 0) config.fire.milestones.splice(index, 1)
    else
      config.fire.milestones.push({
        label: preset.label,
        kind: 'fixed',
        amount: preset.amount,
      })
  }

  function toggleTier(name, expense) {
    const index = config.fire.tiers.findIndex((t) => t.name === name)
    if (index >= 0) config.fire.tiers.splice(index, 1)
    else config.fire.tiers.push({ name, annual_expense: expense, multiple: 25 })
  }

  function hasTier(name) {
    return config.fire.tiers.some((t) => t.name === name)
  }

  const tierPresets = [
    { name: 'Lean', expense: 30000, note: '极简退休' },
    { name: 'Regular', expense: 60000, note: '常规' },
    { name: 'Fat', expense: 120000, note: '富裕' },
  ]

  function cash(n) {
    return `$${n.toLocaleString('en-US')}`
  }
</script>

<Card title="FIRE 目标">
  <p class="tiny intro">
    FIRE Number = 年支出 × 倍数。4% 规则对应 25×；想提前退休通常用 28.6×（3.5%）。
  </p>

  <h3 class="sub-head">档位</h3>
  <div class="chips">
    {#each tierPresets as preset (preset.name)}
      <button
        class="chip"
        class:on={hasTier(preset.name)}
        onclick={() => toggleTier(preset.name, preset.expense)}
      >
        <span class="chip-name">{preset.name}</span>
        <span class="chip-note">{preset.note} · {cash(preset.expense)}/年</span>
      </button>
    {/each}
  </div>

  {#if config.fire.tiers.length > 0}
    <div class="tiers">
      {#each config.fire.tiers as tier, i (i)}
        <div class="row tier">
          <div class="field">
            <label for="tn-{i}">{tier.name} · 年支出</label>
            <input id="tn-{i}" type="number" step="1000" min="0" bind:value={tier.annual_expense} />
          </div>
          <div class="field narrow">
            <label for="tm-{i}">倍数</label>
            <input id="tm-{i}" type="number" step="0.5" min="1" bind:value={tier.multiple} />
          </div>
          <div class="computed">
            <span class="tiny">目标</span>
            <strong>{cash(Math.round(tier.annual_expense * tier.multiple))}</strong>
          </div>
        </div>
      {/each}
    </div>
  {/if}

  <div class="divider"></div>

  <h3 class="sub-head">里程碑</h3>
  <p class="tiny">勾选要在报告里看到达成时间的金额档。</p>
  <div class="chips">
    {#each milestonePresets as preset (preset.label)}
      <button
        class="chip compact"
        class:on={hasMilestone(preset.amount)}
        onclick={() => toggleMilestone(preset)}
        title={preset.note}
      >
        {preset.label}
      </button>
    {/each}
  </div>

  <div class="divider"></div>

  <h3 class="sub-head">变体</h3>
  <div class="row">
    <div class="field">
      <label for="coast">Coast FIRE · 距退休年数</label>
      <input
        id="coast"
        type="number"
        step="1"
        min="1"
        bind:value={config.fire.coast.years_to_retirement}
      />
      <p class="tiny hint">现在存够一笔，之后不再定投也能到期达标</p>
    </div>
    <div class="field">
      <label for="coastg">Coast · 折现增长率</label>
      <div class="suffixed">
        <input
          id="coastg"
          type="number"
          step="0.1"
          value={((config.fire.coast.growth_rate || 0) * 100).toFixed(1)}
          oninput={(e) =>
            (config.fire.coast.growth_rate = (Number(e.currentTarget.value) || 0) / 100)}
        />
        <span class="suffix">%</span>
      </div>
    </div>
  </div>

  <div class="row">
    <div class="field">
      <label for="bexp">Barista · 年支出</label>
      <input id="bexp" type="number" step="1000" min="0" bind:value={config.fire.barista.annual_expense} />
    </div>
    <div class="field">
      <label for="binc">Barista · 兼职年收入</label>
      <input id="binc" type="number" step="1000" min="0" bind:value={config.fire.barista.part_time_income} />
      <p class="tiny hint">兼职收入覆盖一部分支出，所需本金随之降低</p>
    </div>
  </div>
</Card>

<style>
  .intro {
    margin-bottom: var(--gap);
    line-height: 1.5;
  }

  .sub-head {
    margin-bottom: var(--gap-sm);
  }

  .chips {
    display: flex;
    flex-wrap: wrap;
    gap: var(--gap-xs);
  }

  .chip {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    padding: 9px 14px;
    border-radius: var(--radius-sm);
    background: var(--bg-sunken);
    color: var(--text-secondary);
    font-size: 13px;
  }

  .chip.compact {
    padding: 7px 14px;
    font-size: 13px;
    font-variant-numeric: tabular-nums;
  }

  .chip.on {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
  }

  .chip-name {
    font-weight: 600;
  }

  .chip-note {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .chip.on .chip-note {
    color: color-mix(in srgb, var(--accent) 70%, var(--text-secondary));
  }

  .tiers {
    margin-top: var(--gap-sm);
    display: flex;
    flex-direction: column;
    gap: var(--gap-xs);
  }

  .tier {
    align-items: flex-end;
  }

  .narrow {
    flex: 0 0 90px;
  }

  .computed {
    flex: 0 0 auto;
    display: flex;
    flex-direction: column;
    align-items: flex-end;
    padding-bottom: 8px;
    min-width: 100px;
  }

  .computed strong {
    font-size: 16px;
  }

  .divider {
    height: 1px;
    background: var(--border);
    margin: var(--gap) 0;
  }

  .suffixed {
    position: relative;
  }

  .suffix {
    position: absolute;
    right: 11px;
    top: 50%;
    transform: translateY(-50%);
    font-size: 13px;
    color: var(--text-tertiary);
    pointer-events: none;
  }

  .suffixed input {
    padding-right: 28px;
  }

  .hint {
    margin-top: 4px;
    line-height: 1.4;
  }
</style>

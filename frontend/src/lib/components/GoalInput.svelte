<script>
  import Card from './Card.svelte'

  /** FR-006 FIRE 目标设定 —— 吃息退休 / 提取退休。
   *
   * 两种方式同一时间**只用一种**，不是两笔可以叠加的账：两者的区别是
   * 本金「消耗」还是「不动」，混在一起算会把同一笔钱数两遍。
   */
  let { config, rates = null } = $props()

  const MODES = [
    {
      id: 'income',
      name: '吃息退休',
      en: 'Income FIRE',
      short: '本金不动',
      intro:
        '门槛 = 你当年的生活支出，达标 = 那一年的**税后**股息覆盖得了它。本金一直不动，所以花的是股息本身。',
    },
    {
      id: 'withdrawal',
      name: '提取退休',
      en: 'Withdrawal FIRE',
      short: '卖出本金',
      intro:
        'FIRE Number = 年支出 × 倍数。4% 规则对应 25×；想提前退休通常用 28.6×（3.5%）。本金会被逐步卖出。',
    },
  ]

  const mode = $derived(MODES.find((m) => m.id === config.fire.mode) ?? MODES[1])

  // ── 提取退休：档位 ─────────────────────────────────────────────

  const tierPresets = [
    { name: 'Lean', expense: 30000, note: '极简退休' },
    { name: 'Regular', expense: 60000, note: '常规' },
    { name: 'Fat', expense: 120000, note: '富裕' },
  ]

  function toggleTier(name, expense) {
    const index = config.fire.tiers.findIndex((t) => t.name === name)
    if (index >= 0) config.fire.tiers.splice(index, 1)
    else config.fire.tiers.push({ name, annual_expense: expense, multiple: 25 })
  }

  function hasTier(name) {
    return config.fire.tiers.some((t) => t.name === name)
  }

  // ── 吃息退休：目标列表 ─────────────────────────────────────────

  function addGoal() {
    config.fire.income_goals.push({
      label: '',
      monthly_expense: 3000,
      inflation_adjusted: true,
    })
  }

  function removeGoal(index) {
    config.fire.income_goals.splice(index, 1)
  }

  /**
   * 标签刻意**不带货币符号**：金额是美元，但显示时会被换算成所选货币。
   * 若标签写死「$100,000」，切到日元就会看到「$100,000 ¥100,000」——
   * 标签和金额自相矛盾。货币由金额那一栏统一表达。
   */
  function cash(n) {
    return `$${Math.round(n).toLocaleString('en-US')}`
  }
</script>

<Card title="FIRE 目标">
  <!-- 退休方式。放在最上面：它决定了下面整张卡长什么样，
       埋在中间会让人先填完一堆再发现填错了口径。 -->
  <div class="retire-modes" role="group" aria-label="退休方式">
    {#each MODES as m (m.id)}
      <button
        class="mode-btn"
        class:on={config.fire.mode === m.id}
        onclick={() => (config.fire.mode = m.id)}
        aria-pressed={config.fire.mode === m.id}
      >
        <span class="mode-name">{m.name}</span>
        <span class="mode-note">{m.short} · {m.en}</span>
      </button>
    {/each}
  </div>

  <p class="tiny intro">{mode.intro.replaceAll('**', '')}</p>

  {#if config.fire.mode === 'income'}
    <h3 class="sub-head">目标</h3>
    <p class="tiny lede">
      按<strong>今天的购买力</strong>填月支出 —— 引擎会把它折算到达成那年，再和当年的税后股息比。
      可以填多个，各自算各自的达成时间。
    </p>

    {#if config.fire.income_goals.length === 0}
      <p class="muted empty">还没有目标。点下面的「添加目标」开始。</p>
    {/if}

    <div class="goals">
      {#each config.fire.income_goals as goal, i (i)}
        <div class="goal">
          <div class="row main">
            <div class="field">
              <label for="ig-{i}">目标 {i + 1} · 月支出</label>
              <div class="suffixed">
                <input
                  id="ig-{i}"
                  type="number"
                  step="100"
                  min="0"
                  bind:value={goal.monthly_expense}
                />
                <span class="suffix">/月</span>
              </div>
            </div>

            <div class="computed">
              <span class="tiny">年支出</span>
              <strong>{cash(goal.monthly_expense * 12)}</strong>
            </div>

            <!-- 与标的卡、阶段卡同一套 ✕：右上角、圆形。
                 它删的是**整条目标**，所以绝不能和月支出框同行 ——
                 挨着某个输入框时，人只会以为删的是那一格。 -->
            <button class="icon close" onclick={() => removeGoal(i)} title="删除这个目标">✕</button>
          </div>

          <label class="check">
            <input type="checkbox" bind:checked={goal.inflation_adjusted} />
            <span>
              按通胀折算到达成当年
              <span class="tiny why">
                —— 关掉就用今天的金额当固定门槛，等于假设物价不涨
              </span>
            </span>
          </label>
        </div>
      {/each}
    </div>

    <button class="ghost add" onclick={addGoal}>＋ 添加目标</button>
  {:else}
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
              <strong>{cash(tier.annual_expense * tier.multiple)}</strong>
            </div>
          </div>
        {/each}
      </div>
    {:else}
      <p class="muted empty">还没选档位。勾一个上面的预设，或直接添加。</p>
    {/if}
  {/if}
</Card>

<style>
  /* ── 退休方式分段控件 ─────────────────────────────────────── */

  .retire-modes {
    display: flex;
    gap: 2px;
    background: color-mix(in srgb, var(--text) 5%, transparent);
    border-radius: 10px;
    padding: 2px;
    width: fit-content;
    max-width: 100%;
  }

  .mode-btn {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 1px;
    padding: 7px 14px;
    border-radius: 8px;
    background: transparent;
    color: var(--text-secondary);
    text-align: left;
  }

  .mode-btn.on {
    background: var(--bg-elevated);
    color: var(--text);
    box-shadow: 0 1px 2px rgba(0, 0, 0, 0.08);
  }

  .mode-name {
    font-size: 14px;
    font-weight: 600;
  }

  .mode-note {
    font-size: 11px;
    color: var(--text-tertiary);
  }

  .mode-btn.on .mode-note {
    color: var(--text-secondary);
  }

  .intro {
    margin: var(--gap-sm) 0 var(--gap);
    line-height: 1.5;
  }

  .lede {
    margin-bottom: var(--gap-sm);
    line-height: 1.5;
  }

  .sub-head {
    margin-bottom: var(--gap-sm);
  }

  /* ── 吃息：目标列表 ───────────────────────────────────────── */

  .goals {
    display: flex;
    flex-direction: column;
    gap: var(--gap-sm);
  }

  .goal {
    background: var(--bg-sunken);
    border-radius: var(--radius);
    padding: var(--gap-sm) var(--gap-sm) 10px;
    /* ✕ 的定位基准 */
    position: relative;
  }

  .main {
    align-items: flex-end;
    /* 给右上角的 ✕ 让位 */
    padding-right: 30px;
  }

  /* 与 PlanInput / PortfolioInput 的 .close 同一套：右上角、圆形，
     悬停转红（转红来自 app.css 里 button.icon:hover）。 */
  .close {
    position: absolute;
    top: 6px;
    right: 6px;
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border-radius: 50%;
    font-size: 14px;
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

  .check {
    display: flex;
    align-items: baseline;
    gap: 7px;
    margin-top: 9px;
    font-size: 13px;
    color: var(--text-secondary);
    cursor: pointer;
    line-height: 1.45;
  }

  /* 尺寸与配色由 `app.css` 的 `input[type='checkbox']` 统一给 ——
     这里只管它在这一行里的排布。 */
  .check input {
    flex: 0 0 auto;
    /* 与第一行文字对齐，而不是与整段对齐 */
    position: relative;
    top: 2px;
  }

  .why {
    color: var(--text-tertiary);
  }

  .add {
    margin-top: var(--gap-sm);
  }

  /* ── 提取：档位 ───────────────────────────────────────────── */

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

  .field {
    flex: 1;
    min-width: 0;
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
    padding-right: 40px;
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
    line-height: 1.5;
  }
</style>

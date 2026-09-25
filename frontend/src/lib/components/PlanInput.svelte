<script>
  import Card from './Card.svelte'

  /** FR-004 定投设置 —— 分段式定投。
   *
   * A19 要求「前端拦截阶段月份重叠」。这里用的是更彻底的办法：
   * **把阶段建模成首尾相接的链条**，每段只填「持续多久」，
   * 起点由前一段自动决定。重叠因此**结构上就不可能发生** ——
   * 不需要写校验，也就不存在校验漏掉的情况。
   */
  let { config } = $props()

  // 去重：下面的 {#each} 按代码做 key，两行同一个代码会让 Svelte 抛
  // each_key_duplicate，整块计划面板直接崩掉 —— 用户只是手滑输重复而已。
  // 这里不能改成按序号做 key：表单写的是 monthly[symbol]，同号两行只会
  // 互相镜像。重复本身由后端拒绝并在横幅里说明，前端只需保证不崩。
  const symbols = $derived([
    ...new Set(config.assets.map((a) => (a.symbol || '').trim().toUpperCase()).filter(Boolean)),
  ])

  const timeline = $derived.by(() => {
    let cursor = 0
    return config.plan.segments.map((seg) => {
      const start = cursor
      cursor += seg.months || 0
      return { start, end: cursor, seg }
    })
  })

  const totalMonths = $derived(timeline.reduce((max, t) => Math.max(max, t.end), 0))
  const horizon = $derived(config.settings.horizon_months)

  function addSegment() {
    const last = config.plan.segments[config.plan.segments.length - 1]
    const monthly = {}
    for (const s of symbols) monthly[s] = last?.monthly?.[s] ?? 0
    config.plan.segments.push({ months: 120, monthly })
  }

  function removeSegment(i) {
    config.plan.segments.splice(i, 1)
  }

  /**
   * 本阶段月投合计。
   *
   * 只累加**界面上真的显示出来的**那几行，不能把 `seg.monthly` 里的键全加起来：
   * 改过标的代码之后，旧代码可能还躺在字典里，全加就会显示一个比各行之和
   * 更大、而且和引擎实际投入对不上的数 —— 用户没有任何办法看出差在哪。
   */
  function monthlyTotal(seg) {
    if (seg.total != null) return seg.total
    return symbols.reduce((sum, s) => sum + (Number(seg.monthly?.[s]) || 0), 0)
  }

  function monthLabel(start, end) {
    return `第 ${start + 1} – ${end} 月`
  }
</script>

<Card title="定投计划">
  {#snippet headExtra()}
    <button class="ghost" onclick={addSegment}>＋ 添加阶段</button>
  {/snippet}

  {#if config.plan.segments.length === 0}
    <p class="muted empty">没有定投阶段 —— 全程只靠存量增长。</p>
  {/if}

  <div class="segments">
    {#each config.plan.segments as seg, i (i)}
      {@const span = timeline[i]}
      <div class="segment">
        <div class="row head">
          <div class="field months">
            <label for="sm-{i}">持续月数</label>
            <input id="sm-{i}" type="number" step="1" min="1" bind:value={seg.months} />
          </div>

          <div class="span tiny">
            {#if span}{monthLabel(span.start, span.end)}{/if}
          </div>

          <button class="icon" onclick={() => removeSegment(i)} title="删除阶段">✕</button>
        </div>

        <div class="mode">
          <button
            class="tab"
            class:active={seg.total == null}
            onclick={() => {
              seg.total = null
              if (!seg.monthly) seg.monthly = {}
            }}>逐标的填写</button
          >
          <button
            class="tab"
            class:active={seg.total != null}
            onclick={() => {
              seg.total = seg.total ?? 2000
              seg.monthly = {}
            }}>只填总额</button
          >
        </div>

        {#if seg.total != null}
          <div class="field">
            <label for="st-{i}">月投总额</label>
            <input id="st-{i}" type="number" step="any" min="0" bind:value={seg.total} />
            <p class="tiny hint">按各标的目标占比自动拆解</p>
          </div>
        {:else}
          <div class="row amounts">
            {#each symbols as symbol (symbol)}
              <div class="field">
                <label for="am-{i}-{symbol}">{symbol} 月投额</label>
                <input
                  id="am-{i}-{symbol}"
                  type="number"
                  step="any"
                  min="0"
                  value={seg.monthly?.[symbol] ?? 0}
                  oninput={(e) => {
                    seg.monthly = seg.monthly || {}
                    seg.monthly[symbol] = Number(e.currentTarget.value) || 0
                  }}
                />
              </div>
            {/each}
          </div>
        {/if}

        <p class="tiny total">
          本阶段月投合计 <strong>${monthlyTotal(seg).toLocaleString('en-US')}</strong>
        </p>
      </div>
    {/each}
  </div>

  {#if totalMonths > 0}
    <div class="timeline-wrap">
      <div class="timeline">
        {#each timeline as { start, end }, i (i)}
          <span
            class="block"
            style="left: {(start / horizon) * 100}%; width: {((end - start) / horizon) * 100}%"
            title="{monthLabel(start, end)}"
          ></span>
        {/each}
      </div>
      <div class="axis tiny">
        <span>0</span>
        <span>定投覆盖 {totalMonths} / {horizon} 个月</span>
        <span>{horizon}</span>
      </div>
      {#if totalMonths < horizon}
        <p class="tiny hint">
          第 {totalMonths + 1} 个月起不再定投 —— 之后只靠复利增长。
          这正是 Coast FIRE 的形状。
        </p>
      {/if}
    </div>
  {/if}
</Card>

<style>
  .segments {
    display: flex;
    flex-direction: column;
    gap: var(--gap-sm);
  }

  .segment {
    background: var(--bg-sunken);
    border-radius: var(--radius);
    padding: var(--gap-sm);
  }

  .head {
    align-items: flex-end;
  }

  .months {
    flex: 0 0 130px;
  }

  .span {
    flex: 1;
    padding-bottom: 10px;
    color: var(--text-secondary);
  }

  .mode {
    display: flex;
    gap: 2px;
    background: color-mix(in srgb, var(--text) 5%, transparent);
    border-radius: 8px;
    padding: 2px;
    margin: var(--gap-sm) 0;
    width: fit-content;
  }

  .tab {
    font-size: 12px;
    padding: 5px 12px;
    border-radius: 6px;
    background: transparent;
    color: var(--text-secondary);
  }

  .tab.active {
    background: var(--bg-elevated);
    color: var(--text);
    box-shadow: var(--shadow-sm);
  }

  .amounts {
    align-items: flex-end;
  }

  .hint {
    margin-top: 5px;
    line-height: 1.45;
  }

  .total {
    margin-top: var(--gap-sm);
  }

  .timeline-wrap {
    margin-top: var(--gap);
    padding-top: var(--gap);
    border-top: 1px solid var(--border);
  }

  .timeline {
    position: relative;
    height: 8px;
    border-radius: 4px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .block {
    position: absolute;
    top: 0;
    bottom: 0;
    background: var(--accent);
    opacity: 0.75;
    border-right: 1.5px solid var(--bg-elevated);
  }

  .axis {
    display: flex;
    justify-content: space-between;
    margin-top: 6px;
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }
</style>

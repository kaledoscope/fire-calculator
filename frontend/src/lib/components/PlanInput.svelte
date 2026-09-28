<script>
  import Card from './Card.svelte'
  import { months as duration } from '../format.js'

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

  /** 持续时间的两个框：**年与月是加数，不是进位位。**
   *
   * 唯一的真值仍然是 `seg.months` 一个整数，两个框只是它的两种面额 ——
   * `总月数 = 年 × 12 + 月`。所以「7 年 25 个月」是合法的，等于 9 年 1 个月，
   * 下面把实际值标出来；不设「填了年就要求月 ≤ 11」的限制，因为那条限制
   * 只为保护一个书写约定而存在，物理上没有依据，代价是让用户自己心算进位，
   * 还得再补一条「月 > 11 时年必须为空」的规则才自洽。
   *
   * **不能直接 `bind:value` 到 seg.months。** 那样在月份框里打「144」，
   * 每敲一个数字都会被规范化成 `144 % 12`，刚打下的 1 立刻变成别的东西，
   * 三个数字根本打不完。所以编辑期间把原样输入记在 `typing.raw` 里，
   * 并且**把另一格的值也冻在 `typing.other`** —— 合成总月数时必须用
   * 开始编辑那一刻的值，否则打「144」打到一半，年数会被自己刚写进去的
   * 中间结果带着走（`floor(14/12)=1`，于是 `1×12+144` 成了 156）。
   * 冻结值只参与算术，**不参与显示** —— 原因见 `shown()`。
   * 失焦才归位成规范拆分，于是屏幕上的样子和存盘的样子始终一致。
   */
  let typing = $state(null) // { i, part: 'y' | 'm' | 'f', raw: string, other: string }

  const monthsOf = (seg) => Math.max(0, Number(seg.months) || 0)

  /** 定投频率：每几个月投一次。缺省 1 = 每月。 */
  const freqOf = (seg) => Math.max(1, Number(seg.frequency_months) || 1)

  /** 「每月」/「每 3 个月」—— 措辞得跟着频率走。
   *
   * 频率不是 1 的时候，「月投额」「月投合计」这些词就是**错的**：
   * 每三个月投 900 不是「月投 900」。字数不多，但界面说错话比不说更糟。
   */
  const cadence = (seg) => (freqOf(seg) === 1 ? '每月' : `每 ${freqOf(seg)} 个月`)

  /** 某个框此刻该显示什么。
   *
   * 正在编辑的那个框显示用户打的原样；**另一个框一律显示规范化的值**。
   *
   * 冻结的 `other` 只用来算数，**不能拿来显示** —— 这条是踩出来的：
   * 一开始两个框都显示冻结值，于是在月份框里打完「144」再去点年份框时，
   * 年份框里原本显示的 `0` 会随 `onfocus` 当场变成 `12`。光标位置是按
   * 旧文本 `0` 算的，用户接着打 `7`，落在「12」后面就成了 **127 年**。
   * 真实的鼠标用户点进去打字，一样会中招，不是测试才有的问题。
   *
   * 现在另一个框按规范化显示，聚焦它时文本**本来就已经是** 12，不存在改写，
   * 也就不存在光标错位。附带的好处是它会在打字途中跟着进位 ——
   * 月份框打「25」时年份框显示 9，正是「7 年 25 个月 = 9 年 1 个月」的
   * 实时演绎，和下面那行标注说的是同一件事。
   */
  function shown(i, part, seg) {
    if (typing?.i === i && typing.part === part) return typing.raw
    if (part === 'f') return String(freqOf(seg))
    return String(part === 'y' ? Math.floor(monthsOf(seg) / 12) : monthsOf(seg) % 12)
  }

  function beginEdit(i, part) {
    const seg = config.plan.segments[i]
    const y = String(Math.floor(monthsOf(seg) / 12))
    const m = String(monthsOf(seg) % 12)
    typing =
      part === 'f'
        ? { i, part, raw: String(freqOf(seg)), other: '' }
        : { i, part, raw: part === 'y' ? y : m, other: part === 'y' ? m : y }
  }

  function onEdit(i, part, value) {
    if (typing?.i !== i || typing.part !== part) beginEdit(i, part)
    typing.raw = value

    // 频率是一格普通整数，没有「面额」问题，但**清空重打**这条路还得留着：
    // 删光时模型退回 1，框里保持空，用户接着打「3」不会变成「13」。
    if (part === 'f') {
      const n = parseInt(value, 10)
      config.plan.segments[i].frequency_months = Number.isFinite(n) && n >= 1 ? n : 1
      return
    }

    const y = part === 'y' ? value : typing.other
    const m = part === 'm' ? value : typing.other
    config.plan.segments[i].months =
      Math.max(0, (parseInt(y, 10) || 0) * 12 + (parseInt(m, 10) || 0))
  }

  /** 没进位的写法（月份框里 ≥ 12）—— 原样留着，但把实际值说清楚。 */
  function carryNote(i) {
    if (typing?.i !== i || typing.part === 'f') return null
    const m = typing.part === 'm' ? typing.raw : typing.other
    if ((parseInt(m, 10) || 0) < 12) return null
    return duration(monthsOf(config.plan.segments[i]))
  }

  function addSegment() {
    const last = config.plan.segments[config.plan.segments.length - 1]
    const monthly = {}
    for (const s of symbols) monthly[s] = last?.monthly?.[s] ?? 0
    config.plan.segments.push({ months: 120, frequency_months: 1, monthly })
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
          <div class="field duration">
            <label for="dy-{i}">持续时间</label>
            <div class="pair">
              <input
                id="dy-{i}"
                type="number"
                step="1"
                min="0"
                aria-label="年"
                value={shown(i, 'y', seg)}
                onfocus={() => beginEdit(i, 'y')}
                oninput={(e) => onEdit(i, 'y', e.currentTarget.value)}
                onblur={() => (typing = null)}
              />
              <span class="unit">年</span>
              <input
                id="dm-{i}"
                type="number"
                step="1"
                min="0"
                aria-label="个月"
                value={shown(i, 'm', seg)}
                onfocus={() => beginEdit(i, 'm')}
                oninput={(e) => onEdit(i, 'm', e.currentTarget.value)}
                onblur={() => (typing = null)}
              />
              <span class="unit">个月</span>
            </div>
          </div>

          <div class="span tiny">
            {#if span}{monthLabel(span.start, span.end)}{/if}
          </div>

          <button class="icon" onclick={() => removeSegment(i)} title="删除阶段">✕</button>
        </div>

        {#if carryNote(i)}
          <!-- 年、月各填各的，没进位也照收 —— 但实际是多少必须当场说清，
               否则「7 年 25 个月」到底算多久全靠用户自己脑补。 -->
          <p class="tiny carry">
            实际 <strong>{carryNote(i)}</strong>（{monthsOf(seg).toLocaleString('en-US')} 个月）
          </p>
        {:else if monthsOf(seg) === 0}
          <p class="tiny carry">至少填 1 个月</p>
        {/if}

        <div class="controls">
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

          <div class="field freq">
            <label for="sf-{i}">定投频率</label>
            <div class="pair">
              <input
                id="sf-{i}"
                type="number"
                step="1"
                min="1"
                aria-label="每几个月投一次"
                value={shown(i, 'f', seg)}
                onfocus={() => beginEdit(i, 'f')}
                oninput={(e) => onEdit(i, 'f', e.currentTarget.value)}
                onblur={() => (typing = null)}
              />
              <span class="unit">个月一次</span>
            </div>
          </div>
        </div>

        {#if seg.total != null}
          <div class="field">
            <label for="st-{i}">{cadence(seg)}投入总额</label>
            <input id="st-{i}" type="number" step="any" min="0" bind:value={seg.total} />
            <p class="tiny hint">按各标的目标占比自动拆解</p>
          </div>
        {:else}
          <div class="row amounts">
            {#each symbols as symbol (symbol)}
              <div class="field">
                <label for="am-{i}-{symbol}">{symbol} {cadence(seg)}投入额</label>
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
          本阶段{cadence(seg)}投入合计 <strong>${monthlyTotal(seg).toLocaleString('en-US')}</strong>
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

  /* 两个面额框 + 单位，宽度按内容定死 —— 让 `.span` 独占剩下的空间。
     `.row > *` 的 `flex: 1` 会把它撑开，把「第 1 – 109 月」挤到看不见。 */
  .duration {
    flex: 0 0 auto;
  }

  .pair {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  /* 全局 `input { width: 100% }` 在弹性盒里会退化成「尽量宽」，
     这里必须显式定宽，否则一个框吃掉整行。 */
  .pair input {
    flex: 0 0 66px;
    width: 66px;
  }

  .unit {
    font-size: 12px;
    color: var(--text-tertiary);
    white-space: nowrap;
  }

  .carry {
    margin-top: 6px;
    color: var(--text-secondary);
  }

  .span {
    flex: 1;
    padding-bottom: 10px;
    color: var(--text-secondary);
  }

  /* 「填法」和「定投频率」同处一行 —— 两者都是「怎么投」，本来就该挨着。
     分两行的话每段白白多出一行高度，而这一行右侧本来就是空的。
     纵向间距挪到 .controls 上：留在 .mode 上的话，它带着下外边距去和
     频率框按基线对齐，两个控件会差半个身位。 */
  .controls {
    display: flex;
    align-items: flex-end;
    justify-content: space-between;
    gap: var(--gap-sm);
    flex-wrap: wrap;
    margin: var(--gap-sm) 0;
  }

  .mode {
    display: flex;
    gap: 2px;
    background: color-mix(in srgb, var(--text) 5%, transparent);
    border-radius: 8px;
    padding: 2px;
    width: fit-content;
  }

  /* 频率框按内容定宽，别跟着弹性盒伸开 */
  .freq {
    flex: 0 0 auto;
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

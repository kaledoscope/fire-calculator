<script>
  import Card from './Card.svelte'
  import { money, moneyCompact, months as formatMonths } from '../format.js'

  /** FR-011 增长曲线（期 4：可缩放、可拖动、可点选查看单月明细）。
   *
   * 交互设计上只做三件事，都在同一块画布上：
   *
   *     滚轮  → 以**光标所在月份为锚点**缩放。锚点很重要：不锚定光标的话，
   *             想看某一段就得「缩放 → 拖动 → 再缩放」来回找。
   *     拖动  → 平移。像抓住纸带往两边拉，所以是「拖右看左」。
   *     单击  → 选中最近的月份，打出十字线和明细浮层。
   *
   * 缩放的真正价值不只是「看得更大」：Y 轴会跟着**可见区间**重新定标，
   * 于是前 5 年那点早期积累也能铺满整个画布，而不是挤在左下角一条缝里。
   */
  let { monthly = [], currency = 'USD', rates = null, goals = [] } = $props()

  const W = 720
  const H = 240
  const PAD = { top: 16, right: 16, bottom: 26, left: 58 }
  const PLOT_W = W - PAD.left - PAD.right
  const PLOT_H = H - PAD.top - PAD.bottom

  /** 最小可视跨度 —— 再窄就只剩一根线，没有信息量了。 */
  const MIN_SPAN = 6
  /** 抽稀上限。缩放后会按可见点数重新抽稀，所以放大能看到原本被略过的月份。 */
  const MAX_POINTS = 240

  let svgEl = $state(null)
  let view = $state(null) // { from, to }，单位是「第几个月」；null = 全程
  let selectedMonth = $state(null)
  let dragging = $state(false)
  let dragStart = null
  let wasDragged = false

  const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi)

  const chart = $derived.by(() => {
    if (monthly.length === 0) return null

    const dataMin = monthly[0].month
    const dataMax = monthly[monthly.length - 1].month
    const dataSpan = dataMax - dataMin

    let from = view?.from ?? dataMin
    let to = view?.to ?? dataMax
    if (dataSpan <= MIN_SPAN) {
      from = dataMin
      to = dataMax
    } else {
      const span = clamp(to - from, MIN_SPAN, dataSpan)
      from = clamp(from, dataMin, dataMax - span)
      to = from + span
    }

    const visible = monthly.filter((m) => m.month >= from && m.month <= to)
    if (visible.length === 0) return null

    // 按**可见**点数抽稀：放大后步长自动变小，被略过的月份重新出现
    const step = Math.max(1, Math.ceil(visible.length / MAX_POINTS))
    const points = []
    for (let i = 0; i < visible.length; i += step) points.push(visible[i])
    const tail = visible[visible.length - 1]
    if (points[points.length - 1] !== tail) points.push(tail)

    // Y 轴只按可见区间的峰值定标 —— 这正是放大能看清早期积累的原因
    const maxValue = Math.max(...points.map((p) => p.total_value), 1)

    return { dataMin, dataMax, from, to, visible, points, tail, maxValue }
  })

  function x(month) {
    return PAD.left + ((month - chart.from) / (chart.to - chart.from)) * PLOT_W
  }

  function y(value) {
    return PAD.top + (1 - value / chart.maxValue) * PLOT_H
  }

  function path(key) {
    return chart.points
      .map((p, i) => `${i === 0 ? 'M' : 'L'}${x(p.month).toFixed(1)},${y(p[key]).toFixed(1)}`)
      .join(' ')
  }

  function areaPath() {
    const top = chart.points
      .map((p) => `${x(p.month).toFixed(1)},${y(p.total_value).toFixed(1)}`)
      .join(' L')
    const base = y(0).toFixed(1)
    const left = x(chart.points[0].month).toFixed(1)
    const right = x(chart.points[chart.points.length - 1].month).toFixed(1)
    return `M${left},${base} L${top} L${right},${base} Z`
  }

  const yTicks = $derived([0, 0.25, 0.5, 0.75, 1].map((f) => f * chart.maxValue))

  // ── 目标达成标记 ──────────────────────────────────────────────
  //
  // 主要标注是**竖线**，不是横线。原因是竖线天然不受 Y 轴缩放影响：
  // 达成那个月曲线恰好穿过目标值，所以标记永远落在画布内。横线则不
  // 然 —— 目标高于当前视野峰值时会跑到画布外，用户什么也看不见。

  /**
   * 带序号的完整目标表。序号与「FIRE 进度」的行一一对应，同时**充当 key**
   * —— `label` 不保证唯一（同名档位、用户给两个目标起同一个名字），拿它做
   * key 会撞上 Svelte 的 `each_key_duplicate`，整张图直接白掉。
   */
  const indexed = $derived(goals.map((g, i) => ({ ...g, index: i + 1 })))

  /** 已达成、且落在当前视野内的目标。 */
  const markers = $derived.by(() => {
    if (!chart) return []
    return indexed.filter(
      (g) => g.month !== null && g.month >= chart.from && g.month <= chart.to,
    )
  })

  /**
   * 视野放不下的「提取」目标。它们画不出横线，但**绝不能就此消失** ——
   * 一条被静默省略的标注，比一条画在边界上的标注更误导。
   */
  const overhead = $derived.by(() =>
    indexed.filter((g) => g.criterion === 'value' && g.target > chart.maxValue),
  )

  /** 图上画得出的「提取」横线。 */
  const valueLines = $derived.by(() =>
    indexed.filter((g) => g.criterion === 'value' && g.target <= chart.maxValue),
  )

  /**
   * 这个年限内**根本没达成**的目标。图上无点可标，但得说出来 ——
   * 否则用户看到一条曲线、一张干净的图，会以为目标标记这个功能不存在，
   * 而不是「你的目标还没到」。
   */
  const unreached = $derived(indexed.filter((g) => g.month === null))

  /** chip 贴着图顶，左右各留半个宽度，免得被画布边缘切掉。 */
  function chipX(month) {
    return clamp(x(month), PAD.left + 12, W - PAD.right - 12)
  }

  /** X 轴刻度：跨度够大就按年，缩到两年以内就改按月。 */
  const xTicks = $derived.by(() => {
    const span = chart.to - chart.from
    const out = []

    if (span >= 24) {
      const years = span / 12
      const stride = Math.max(1, Math.ceil(years / 8))
      for (let yr = Math.ceil(chart.from / 12); yr * 12 <= chart.to; yr += stride) {
        if (yr > 0) out.push({ month: yr * 12, label: `${yr}年` })
      }
    } else {
      const stride = Math.max(1, Math.ceil(span / 8))
      for (let m = Math.ceil(chart.from); m <= chart.to; m += stride) {
        if (m > 0) out.push({ month: m, label: `第${m}月` })
      }
    }
    return out
  })

  /** 选中的那个月。存的是月份序号，取快照时按当前可见区间找最近的 —— 数据变了自会归位。 */
  const selected = $derived.by(() => {
    if (selectedMonth === null || !chart) return null

    // 选中的月份被平移出视野后就不显示了。不这么处理的话，最近的可见月份
    // 会把十字线吸在画布边缘上，读数悄悄换成了另一个月，看着像是没反应。
    if (selectedMonth < chart.from || selectedMonth > chart.to) return null

    let best = chart.visible[0]
    let bestGap = Infinity
    for (const snap of chart.visible) {
      const gap = Math.abs(snap.month - selectedMonth)
      if (gap < bestGap) {
        bestGap = gap
        best = snap
      }
    }
    return best
  })

  /** 浮层靠右时贴右边，免得被裁掉。 */
  const tipSide = $derived(
    selected && x(selected.month) / W > 0.62 ? 'right' : 'left',
  )

  // ── 坐标换算 ──────────────────────────────────────────────────

  function clientToMonth(clientX) {
    const rect = svgEl.getBoundingClientRect()
    const vbX = ((clientX - rect.left) / rect.width) * W
    const t = (vbX - PAD.left) / PLOT_W
    return chart.from + t * (chart.to - chart.from)
  }

  /**
   * 平移用「拖动开始时冻结的跨度」来换算，而不是当前 chart 的跨度。
   *
   * 平移过程中 from/to 一直在动，拿实时值换算就是在追逐自己的尾巴。
   * 跨度在平移中本来就不变（只有起点在移动），所以用冻结值既正确又稳。
   */
  function panMonthsPerPx() {
    const rect = svgEl.getBoundingClientRect()
    if (!dragStart) return 0
    return ((dragStart.to - dragStart.from) / PLOT_W) * (W / rect.width)
  }

  // ── 交互 ──────────────────────────────────────────────────────

  /**
   * 滚轮缩放。
   *
   * 刻意手工注册而不是写 `onwheel={...}`：Svelte 对 wheel / touch­move 这类
   * 事件默认挂 **passive** 监听器，那样 `preventDefault()` 会被浏览器无视，
   * 于是缩放的同时整个页面跟着滚 —— 必须显式 `passive: false`。
   */
  $effect(() => {
    const el = svgEl
    if (!el) return
    const handler = (event) => onWheel(event)
    el.addEventListener('wheel', handler, { passive: false })
    return () => el.removeEventListener('wheel', handler)
  })

  function onWheel(event) {
    if (!chart) return
    event.preventDefault()

    const anchor = clientToMonth(event.clientX)
    const factor = event.deltaY > 0 ? 1.16 : 1 / 1.16
    const span = chart.to - chart.from
    const next = clamp(span * factor, MIN_SPAN, chart.dataMax - chart.dataMin || MIN_SPAN)

    // 让锚点月份在缩放前后停在同一个像素位置上
    const ratio = (anchor - chart.from) / span
    const from = anchor - ratio * next
    view = { from, to: from + next }
  }

  function onPointerDown(event) {
    if (!chart || event.button !== 0) return
    svgEl.setPointerCapture(event.pointerId)
    dragging = true
    wasDragged = false
    dragStart = { x: event.clientX, from: chart.from, to: chart.to }
  }

  function onPointerMove(event) {
    if (!dragging || !dragStart) return
    const dx = event.clientX - dragStart.x
    if (Math.abs(dx) > 3) wasDragged = true
    if (!wasDragged) return

    const shift = dx * panMonthsPerPx()
    // 拖右看左：像抓住纸带往右拉，露出的是更早的部分
    view = { from: dragStart.from - shift, to: dragStart.to - shift }
  }

  function onPointerUp(event) {
    if (!dragging) return
    dragging = false
    dragStart = null
    if (!wasDragged) {
      // 纯点击 → 选中最近的月份。拖动结束不该顺带选中，那太容易误触。
      const target = Math.round(clientToMonth(event.clientX))
      selectedMonth = clamp(target, chart.dataMin, chart.dataMax)
    }
  }

  function onKeyDown(event) {
    const step = Math.max(1, Math.round((chart.to - chart.from) / 12))
    const handlers = {
      ArrowLeft: () => pan(-step),
      ArrowRight: () => pan(step),
      '+': () => zoom(1 / 1.5),
      '=': () => zoom(1 / 1.5),
      '-': () => zoom(1.5),
      Escape: () => {
        selectedMonth = null
        view = null
      },
    }
    const handler = handlers[event.key]
    if (!handler) return
    event.preventDefault()
    handler()
  }

  function zoom(factor) {
    if (!chart) return
    const center = (chart.from + chart.to) / 2
    const span = chart.to - chart.from
    const next = clamp(span * factor, MIN_SPAN, chart.dataMax - chart.dataMin || MIN_SPAN)
    view = { from: center - next / 2, to: center + next / 2 }
  }

  function pan(months) {
    view = { from: chart.from + months, to: chart.to + months }
  }

  function reset() {
    view = null
    selectedMonth = null
  }

  const zoomed = $derived(
    chart && (chart.from > chart.dataMin + 0.5 || chart.to < chart.dataMax - 0.5),
  )
</script>

<Card title="增长曲线">
  {#snippet headExtra()}
    <div class="head-tools">
      {#if chart}
        <span class="tiny range">
          {#if zoomed}
            {formatMonths(Math.round(chart.from))} – {formatMonths(Math.round(chart.to))}
          {:else}
            全程 {formatMonths(chart.dataMax)}
          {/if}
        </span>
        <button class="ghost icon-btn" onclick={() => zoom(1 / 1.5)} title="放大">＋</button>
        <button class="ghost icon-btn" onclick={() => zoom(1.5)} title="缩小">−</button>
        <!-- 叫「全程」而不是「重置」：页头那个「重置」是恢复整份配置的，
             两个同名按钮挨在一起会让人不敢点。 -->
        <button
          class="ghost icon-btn"
          onclick={reset}
          disabled={!zoomed && selectedMonth === null}
          title="回到全程视图">全程</button
        >
      {/if}
    </div>
  {/snippet}

  {#if !chart}
    <p class="muted empty">还没有数据。</p>
  {:else}
    <div class="chart-wrap">
      <svg
        bind:this={svgEl}
        viewBox="0 0 {W} {H}"
        role="img"
        aria-label="组合总值随时间的增长曲线。可滚轮缩放、拖动平移、点击查看单月明细；键盘方向键平移，加减号缩放，Esc 重置。"
        tabindex="0"
        class:grabbing={dragging}
        onpointerdown={onPointerDown}
        onpointermove={onPointerMove}
        onpointerup={onPointerUp}
        onpointercancel={onPointerUp}
        onkeydown={onKeyDown}
      >
        <defs>
          <linearGradient id="fill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--accent)" stop-opacity="0.22" />
            <stop offset="100%" stop-color="var(--accent)" stop-opacity="0.02" />
          </linearGradient>
          <clipPath id="plot">
            <rect x={PAD.left} y={PAD.top} width={PLOT_W} height={PLOT_H} />
          </clipPath>
        </defs>

        {#each yTicks as t (t)}
          <line x1={PAD.left} y1={y(t)} x2={W - PAD.right} y2={y(t)} stroke="var(--border)" />
          <text x={PAD.left - 8} y={y(t) + 4} text-anchor="end" class="tick">
            {t === 0 ? '0' : moneyCompact(t, currency, rates)}
          </text>
        {/each}

        {#each xTicks as tk (tk.month)}
          <text x={x(tk.month)} y={H - 8} text-anchor="middle" class="tick">{tk.label}</text>
        {/each}

        <g clip-path="url(#plot)">
          <path d={areaPath()} fill="url(#fill)" />
          <path
            d={path('total_invested')}
            fill="none"
            stroke="var(--text-tertiary)"
            stroke-width="1.5"
            stroke-dasharray="4 3"
          />
          <path
            d={path('total_value')}
            fill="none"
            stroke="var(--accent)"
            stroke-width="2.5"
            stroke-linejoin="round"
            stroke-linecap="round"
          />

          <!-- 「提取」目标的水平门槛线。只在目标落在当前 Y 轴范围内时画 ——
               超出的部分由下面的 overhead 提示接手，不静默省略。 -->
          {#each valueLines as g (g.index)}
            <line
              x1={PAD.left}
              y1={y(g.target)}
              x2={W - PAD.right}
              y2={y(g.target)}
              stroke="var(--text-tertiary)"
              stroke-width="1"
              stroke-dasharray="6 4"
              opacity="0.6"
            />
          {/each}

          <!-- 达成时点的竖线。放在曲线之上、选中十字线之下。 -->
          {#each markers as g (g.index)}
            <line
              x1={x(g.month)}
              y1={PAD.top}
              x2={x(g.month)}
              y2={PAD.top + PLOT_H}
              stroke="var(--accent)"
              stroke-width="1"
              stroke-dasharray="3 3"
              opacity="0.55"
            />
            <!-- 圆点只对「提取」目标画：它的 target 是组合总值，那个月曲线
                 恰好穿过它。吃息目标的 target 是**年化股息**，画到这条
                 「组合总值」的 Y 轴上毫无意义（股息比总值小两个数量级，
                 点会落在轴外），所以只留竖线。 -->
            {#if g.criterion === 'value'}
              <circle cx={x(g.month)} cy={y(g.target)} r="3.5" fill="var(--accent)" opacity="0.75" />
            {/if}
          {/each}

          {#if selected}
            <line
              x1={x(selected.month)}
              y1={PAD.top}
              x2={x(selected.month)}
              y2={PAD.top + PLOT_H}
              stroke="var(--accent)"
              stroke-width="1"
              stroke-dasharray="3 3"
              opacity="0.7"
            />
            <circle
              cx={x(selected.month)}
              cy={y(selected.total_value)}
              r="4.5"
              fill="var(--accent)"
              stroke="var(--bg)"
              stroke-width="2"
            />
          {/if}
        </g>

        <!-- 序号 chip 放在裁剪组**外面**：贴着图顶，被裁掉就没意义了。 -->
        {#each markers as g (g.index)}
          <g class="chip" transform="translate({chipX(g.month)}, {PAD.top + 3})">
            <rect x="-10" y="-1" width="20" height="16" rx="8" />
            <text x="0" y="11" text-anchor="middle">{g.index}</text>
          </g>
        {/each}
      </svg>

      {#if selected}
        {@const gain = selected.total_value - selected.total_invested}
        <div
          class="tip {tipSide}"
          style="left: {(x(selected.month) / W) * 100}%"
        >
          <div class="tip-head">
            <strong>{formatMonths(selected.month)}</strong>
            <button class="tip-close" onclick={() => (selectedMonth = null)} title="关闭">✕</button>
          </div>
          <dl>
            <div class="tip-row emphasis">
              <dt>组合总值</dt>
              <dd>{money(selected.total_value, currency, rates)}</dd>
            </div>
            <div class="tip-row">
              <dt>累计投入</dt>
              <dd>{money(selected.total_invested, currency, rates)}</dd>
            </div>
            <div class="tip-row">
              <dt>账面增值</dt>
              <dd class:positive={gain > 0}>{money(gain, currency, rates)}</dd>
            </div>
            <div class="tip-row">
              <dt>其中现金</dt>
              <dd>{money(selected.cash, currency, rates)}</dd>
            </div>
            <div class="tip-row">
              <dt>本月股息</dt>
              <dd>{money(selected.month_dividend, currency, rates)}</dd>
            </div>
            <div class="tip-row">
              <dt>累计股息</dt>
              <dd>{money(selected.cumulative_dividend, currency, rates)}</dd>
            </div>
            {#if selected.cumulative_tax > 0}
              <div class="tip-row">
                <dt>累计税</dt>
                <dd class="negative">−{money(selected.cumulative_tax, currency, rates)}</dd>
              </div>
            {/if}
          </dl>

          {#if selected.assets?.length}
            <div class="tip-assets">
              {#each selected.assets as a (a.symbol)}
                <div class="tip-row">
                  <dt>{a.symbol}</dt>
                  <dd>{money(a.value, currency, rates)}</dd>
                </div>
              {/each}
            </div>
          {/if}
        </div>
      {/if}
    </div>

    <div class="legend">
      <span class="key"><i class="swatch accent"></i>组合总值</span>
      <span class="key"><i class="swatch dashed"></i>累计投入本金</span>
      {#if markers.length > 0 || overhead.length > 0}
        <span class="key"><i class="swatch goal"></i>目标达成时点</span>
      {/if}
      <span class="key tiny">滚轮缩放 · 拖动平移 · 点击看单月</span>
    </div>

    {#if unreached.length > 0}
      <p class="tiny goal-note">
        {unreached.length} 个目标在这个年限内没有达成时间，图上无从标注
        {#if unreached.length === 1}（{unreached[0].label}）{/if}。
      </p>
    {/if}

    {#if overhead.length > 0}
      <!-- 「目标高过当前视野峰值」这句话**曾经画在图里**（贴顶、右对齐）。
           两个毛病：它和贴顶的序号 chip 抢同一条带，chip 一多就压在字上；
           而且右对齐正好把末字顶到画布边缘，窄一点就被切。
           它本来就是在说「什么东西没画出来」—— 和上面那句同类，
           放在图外与图例作伴才合身，也就再没有碰撞这回事。 -->
      <p class="tiny goal-note">
        {overhead.length} 个提取目标高过当前视野，横线画不出来（放小或缩短年限可见）
        {#if overhead.length === 1}：{overhead[0].label}{/if}。
      </p>
    {/if}

    {#if markers.length > 0}
      <!-- chip 上的序号在图里只是个记号，含义得在这儿落地。 -->
      <ol class="goal-legend">
        {#each markers as g (g.index)}
          <li>
            <span class="num">{g.index}</span>
            <span class="name">{g.label}</span>
            <span class="tiny">第 {formatMonths(g.month)}</span>
          </li>
        {/each}
      </ol>
    {/if}
  {/if}
</Card>

<style>
  .head-tools {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 0 0 auto;
  }

  .range {
    margin-right: 2px;
    font-variant-numeric: tabular-nums;
  }

  .icon-btn {
    font-size: 12px;
    line-height: 1;
    padding: 5px 8px;
    min-width: 26px;
  }

  .icon-btn:disabled {
    opacity: 0.35;
    cursor: default;
  }

  .chart-wrap {
    position: relative;
    width: 100%;
  }

  svg {
    width: 100%;
    height: auto;
    display: block;
    overflow: visible;
    cursor: crosshair;
    /* pan-y：横向拖动归图表（平移），纵向仍然滚动页面 ——
       设成 none 会让手机上滑过图表时整页卡住，那是很恼人的。 */
    touch-action: pan-y;
    outline: none;
  }

  svg:focus-visible {
    box-shadow: 0 0 0 3px var(--accent-soft, rgba(0, 113, 227, 0.25));
    border-radius: var(--radius);
  }

  svg.grabbing {
    cursor: grabbing;
  }

  .tick {
    font-size: 10px;
    fill: var(--text-tertiary);
    font-family: var(--font-sans);
  }

  /* ── 目标标记 ─────────────────────────────────────────────── */

  .chip rect {
    fill: var(--accent);
  }

  .chip text {
    font-size: 10px;
    font-weight: 600;
    fill: #fff;
    font-family: var(--font-sans);
  }

  .goal-note {
    margin: var(--gap-xs) 0 0;
  }

  .goal-legend {
    display: flex;
    flex-wrap: wrap;
    gap: var(--gap-sm) var(--gap);
    margin: var(--gap-xs) 0 0;
    padding: 0;
    list-style: none;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .goal-legend li {
    display: flex;
    align-items: center;
    gap: 5px;
  }

  .goal-legend .num {
    display: grid;
    place-items: center;
    width: 16px;
    height: 16px;
    border-radius: 50%;
    background: var(--accent);
    color: #fff;
    font-size: 10px;
    font-weight: 600;
    flex: 0 0 auto;
  }

  /* ── 单月明细浮层 ─────────────────────────────────────────── */

  .tip {
    position: absolute;
    top: 8px;
    transform: translateX(12px);
    min-width: 176px;
    padding: 10px 12px;
    border-radius: 12px;
    background: var(--bg-elevated, var(--bg));
    border: 1px solid var(--border);
    box-shadow: 0 8px 28px rgba(0, 0, 0, 0.14);
    font-size: 12px;
    backdrop-filter: blur(12px);
    /* 浮层只是**读**数用的，不能挡住底下的画布 ——
       否则浮层盖住的那块区域拖不动、滚不动，像图表坏了一半。
       只有关闭按钮需要重新接受点击。 */
    pointer-events: none;
  }

  .tip.right {
    transform: translateX(calc(-100% - 12px));
  }

  .tip-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-bottom: 6px;
    padding-bottom: 6px;
    border-bottom: 1px solid var(--border);
  }

  .tip-close {
    font: inherit;
    font-size: 11px;
    line-height: 1;
    padding: 2px 4px;
    border: none;
    background: none;
    color: var(--text-tertiary);
    cursor: pointer;
    pointer-events: auto; /* 浮层整体不吃事件，这个按钮要单独开回来 */
  }

  .tip-close:hover {
    color: var(--text);
  }

  .tip dl,
  .tip-assets {
    margin: 0;
    display: flex;
    flex-direction: column;
    gap: 3px;
  }

  .tip-assets {
    margin-top: 6px;
    padding-top: 6px;
    border-top: 1px solid var(--border);
    color: var(--text-secondary);
  }

  .tip-row {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    gap: 14px;
  }

  .tip-row dt {
    color: var(--text-secondary);
    white-space: nowrap;
  }

  .tip-row dd {
    margin: 0;
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
  }

  .tip-row.emphasis dd {
    font-weight: 600;
  }

  .positive {
    color: var(--positive, #34c759);
  }

  .negative {
    color: var(--negative);
  }

  /* ── 图例 ─────────────────────────────────────────────────── */

  .legend {
    display: flex;
    align-items: center;
    gap: var(--gap);
    margin-top: var(--gap-sm);
    font-size: 12px;
    color: var(--text-secondary);
  }

  .key {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .key.tiny {
    margin-left: auto;
    color: var(--text-tertiary);
  }

  .swatch {
    display: inline-block;
    width: 14px;
    height: 2px;
    border-radius: 1px;
    flex: 0 0 auto;
  }

  .swatch.accent {
    background: var(--accent);
    height: 3px;
  }

  .swatch.dashed {
    background: repeating-linear-gradient(
      90deg,
      var(--text-tertiary) 0 4px,
      transparent 4px 7px
    );
  }

  /* 竖线的图例：一小段竖虚线，与图上标记同色同形。 */
  .swatch.goal {
    width: 2px;
    height: 13px;
    background: repeating-linear-gradient(
      180deg,
      var(--accent) 0 3px,
      transparent 3px 6px
    );
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }
</style>

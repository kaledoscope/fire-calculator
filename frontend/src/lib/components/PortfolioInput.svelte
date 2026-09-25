<script>
  import Card from './Card.svelte'
  import { api } from '../api.js'
  import { dropPlanSymbol, renamePlanSymbol } from '../config.js'
  import { defaultAsset } from '../defaults.js'
  import { money, percent } from '../format.js'

  /** FR-002 标的管理 + FR-003 现金管理 + FR-005 参数抓取 */
  let { config, rates = null } = $props()

  let expanded = $state({})

  /**
   * 抓取状态，**按标的代码**归档，不按行号 —— 删掉一行不会让别的行错位，
   * 而且这些元信息本来就是标的的属性，不是那一行的属性。
   *
   * 刻意放在 `config` 外面：它一变（比如从「抓取中」变成「已抓取」）不该
   * 触发重算 —— 徽章不是模型的一部分。放进 config 还会被写进保存的配置里。
   */
  let quotes = $state({})
  let lookbacks = $state({})

  /** 每个标的的请求序号，用来丢弃过期响应（乱序返回时不能让旧结果覆盖新结果）。 */
  const seqs = new Map()

  const totalWeight = $derived(
    config.assets.reduce((sum, a) => sum + (a.target_weight || 0), 0),
  )
  const cashWeight = $derived(Math.max(0, 1 - totalWeight))
  const overAllocated = $derived(totalWeight > 1.0000001)

  function addAsset() {
    config.assets.push(defaultAsset('', 0))
  }

  function removeAsset(index) {
    // 计划里那一笔也要一起清掉：留着它就是一笔查不到价格、会被引擎静默
    // 跳过的钱，界面上的「计划合计」还会把它算进去，与显示的各行对不上。
    dropPlanSymbol(config, config.assets[index]?.symbol)
    config.assets.splice(index, 1)
  }

  function toggle(index) {
    expanded[index] = !expanded[index]
  }

  const keyOf = (symbol) => (symbol || '').trim().toUpperCase()

  /**
   * 把抓来的参数填进标的。
   *
   * 会**覆盖**手输的参数 —— 参数属于标的，改了标的代码，旧参数就没有意义了。
   * 只在标的代码变更时自动触发，所以不会在用户调参的过程中把值抢回去。
   */
  function applyQuote(asset, q) {
    if (q.last_price) {
      // 价格进的是输入框（step 0.01），留两位小数纯粹是展示层的事
      asset.price = Math.round(q.last_price * 100) / 100
    }
    asset.params.price_growth = q.params.price_growth
    asset.params.dividend_yield = q.params.dividend_yield
    asset.params.dividend_growth = q.params.dividend_growth
    asset.params.expense_ratio = 0 // A23：抓取模式强制 0，费用已含在价格里
    asset.params.source = 'fetched'
    asset.params.lookback_years = q.lookback_years
  }

  /**
   * 两个价格来源，**并行**发出去。
   *
   * 实测：stockanalysis 约 1.2 秒且稳定；Nasdaq 约 50 秒，一半的请求直接
   * 超时。串行的话用户要盯着转圈等近一分钟才看见第一个数字。所以两个一起
   * 发、谁先回谁先渲染；Nasdaq 后到且成功，就盖掉先显示的那份。
   *
   * 慢的那个再慢也不挡路，快的那份哪天接口没了也只是退回「只有 Nasdaq」。
   */
  const SOURCES = ['stockanalysis', 'nasdaq']

  /** 展示优先级：数字大的覆盖小的。反向覆盖会把已经显示好的数据换坏。 */
  const SOURCE_RANK = { stockanalysis: 1, nasdaq: 2 }

  const SOURCE_LABEL = { stockanalysis: 'stockanalysis', nasdaq: 'Nasdaq' }

  /**
   * 某个来源没答上话时点名说它一句。
   *
   * **两种没答上话不是一回事**：
   *   - 连不上 / 超时  → 「来源暂时无法获取」，等一下重试就好
   *   - `bad_data`     → 数据源答了，但它自己前后矛盾（历史末价与它自报的现价
   *                      差了上千倍），这份数据不能用。这不是用户填错了代码，
   *                      也不是网络的问题，所以**不能**说「无法获取」——那是假话。
   *
   * 两种都只是备注：屏幕上已经有一份正确数据时，缺一个来源不是错误。
   * 所以这句话里没有「失败」「错误」这类词，界面上也不标红。
   */
  function missNote(source, failure, shownSource) {
    const label = SOURCE_LABEL[source] ?? source
    if (failure !== 'bad_data') return `${label} 来源暂时无法获取`
    // 屏幕上这份**就是它上一次给的**（比如用户点了「更新数据」重抓一次）。
    // 这时说「已忽略」会自相矛盾 —— 旁边那行还写着「来源 Nasdaq」。
    // 该说的是「这一次的答复不采用」。
    return shownSource === source
      ? `${label} 本次报价异常，沿用上次数据`
      : `${label} 报价异常，已忽略`
  }

  /**
   * 把缺的来源拼成一句。两个都没答上就不点名了，点名反而啰嗦。
   *
   * `missing` 里存的是**来源名**而不是拼好的句子：同一次提问里两个来源可能
   * 因为不同原因没答上话，各说各的才准确。
   */
  function missingLabel(missing, failures = {}, shownSource = null) {
    return missing.length >= SOURCES.length
      ? '数据源暂时无法获取'
      : missing.map((s) => missNote(s, failures[s], shownSource)).join('；')
  }

  /**
   * 实际用上的历史跨度是不是比请求的窗口短。
   *
   * 「不够就退回成立以来」本来就该这么做（`derive_params` 一直是这么算的），
   * 错的是**不说** —— 界面写着「回看 15 年」，用户以为参数来自 15 年，
   * 实际只有 3 年。两个数都是后端给的，这里只比大小，不做计算。
   */
  function shortHistory(q) {
    return (
      q?.historyYears != null &&
      q?.lookbackYears != null &&
      q.historyYears < q.lookbackYears - 0.5
    )
  }

  async function fetchQuote(symbol, { force = false } = {}) {
    const key = keyOf(symbol)
    if (!key) return

    // 一个序号对应「一次提问」。两个来源、以及用户连着改几次代码，共用一个
    // 计数器 —— 只要来了更新的一次提问，在途的旧响应就全部作废。
    const seq = (seqs.get(key) ?? 0) + 1
    seqs.set(key, seq)

    const shown = quotes[key]
    // 已经有数据在屏幕上就**不清空、不显示转圈** —— 数字留在原地，只在
    // 旁边挂一句「更新中」。清空会让整个面板闪一下，比慢一点更难受。
    // missing 一并清零：每问一次都重新数一遍谁没答上话。
    quotes[key] =
      shown?.status === 'ok'
        ? { ...shown, pending: SOURCES.length, missing: [] }
        : { status: 'loading', pending: SOURCES.length }

    await Promise.all(
      SOURCES.map(async (source) => {
        try {
          const q = await api.quote(key, lookbacks[key] ?? 10, { source, force })
          if (seqs.get(key) !== seq) return // 已有更新的提问在跑，丢弃这次结果
          settle(key, source, q)
        } catch (err) {
          if (seqs.get(key) !== seq) return
          settle(key, source, { available: false, reason: err?.message ?? '抓取失败' })
        }
      }),
    )
  }

  /** 一个来源回来了：合并进该标的的状态，并按优先级决定要不要改写画面。 */
  function settle(key, source, q) {
    const current = quotes[key] ?? { status: 'loading' }
    const next = { ...current, pending: Math.max(0, (current.pending ?? 1) - 1) }
    const rank = SOURCE_RANK[source] ?? 0

    if (q.available) {
      if (rank >= (current.rank ?? 0)) {
        const asset = config.assets.find((a) => keyOf(a.symbol) === key)
        if (asset) applyQuote(asset, q)
        Object.assign(next, {
          status: 'ok',
          rank,
          source,
          name: q.name,
          asOf: q.as_of,
          fromCache: q.from_cache,
          fee: q.expense_ratio_info,
          assetClass: q.asset_class,
          lookbackYears: q.lookback_years,
          historyYears: q.params?.history_years ?? null,
        })
      }
      next.known = q.known
      next.reason = null
      // 这个来源答上了，把它从「没答话」名单里划掉（重试场景会用上）
      next.missing = (next.missing ?? []).filter((n) => n !== source)
      if (next.failures) delete next.failures[source]
    } else {
      next.known = q.known
      // 用的是后端给的 failure，不是去匹配中文文案 —— 文案一改，这种判断
      // 就会悄悄失效，而且失效得无声无息。
      const authoritative = q.failure === 'unknown_symbol' || q.failure === 'bad_source'
      const allDone = next.pending === 0

      // **先记账，不管另一个来源到了没有。**
      // 谁先返回是网络的事，不该决定这句话出不出现：Nasdaq 被拒连时可能比
      // stockanalysis 还快，那时它的失败先落地，若只在「已有数据」时才记，
      // 这条备注就凭空丢了 —— 而屏幕上明明只有一份数据。
      if (!next.missing?.includes(source)) {
        next.missing = [...(next.missing ?? []), source]
      }
      // 连原因一起记：脚注要说的是「连不上」还是「数据自相矛盾」，
      // 两者的应对完全不同（前者等一下，后者别信这个数）。
      next.failures = { ...(next.failures ?? {}), [source]: q.failure ?? 'unreachable' }

      if (next.status !== 'ok' && (authoritative || allDone)) {
        // 「查无此标的」是权威答复，立刻报；网络类的先等等另一个来源，
        // 否则快路径一抖动就报错，而下一秒数据就回来了。
        next.status = 'error'
        // 权威理由（「没这个代码」）优先于网络理由（「连不上」）：前者
        // 可操作，后者只会让人去查自己的网络。
        if (authoritative || !next.reason) next.reason = q.reason
      }
    }

    next.refreshing = next.status === 'ok' && next.pending > 0
    quotes[key] = next
  }

  /** 切换回看窗口：缓存本来就有 15 年，所以这一步不会再打网络请求。 */
  function setLookback(symbol, years) {
    const key = keyOf(symbol)
    lookbacks[key] = years
    fetchQuote(key)
  }

  function fetchAll() {
    for (const asset of config.assets) fetchQuote(asset.symbol, { force: true })
  }

  function onSymbolChange(event, index) {
    // 输入框用的是 value={...} 而不是 bind:，所以此刻 asset.symbol 还是旧值 ——
    // 正好拿它去把定投计划里那笔月投额挪过来（见 lib/config.js 的说明）
    const previous = config.assets[index].symbol
    const next = keyOf(event.currentTarget.value)

    config.assets[index].symbol = next
    if (next !== previous) renamePlanSymbol(config, previous, next)
    fetchQuote(next)
  }
</script>

<Card title="投资组合">
  {#snippet headExtra()}
    <div class="head-actions">
      {#if config.assets.length > 0}
        <button class="ghost" onclick={fetchAll}>⟳ 抓取参数</button>
      {/if}
      <button class="ghost" onclick={addAsset}>＋ 添加标的</button>
    </div>
  {/snippet}

  {#if config.assets.length === 0}
    <p class="muted empty">还没有标的。点右上角「添加标的」开始。</p>
  {/if}

  <div class="assets">
    {#each config.assets as asset, i (i)}
      {@const key = keyOf(asset.symbol)}
      {@const quote = quotes[key]}
      <div class="asset" class:invalid={overAllocated}>
        <div class="row main">
          <div class="field symbol">
            <label for="sym-{i}">
              标的代码
              {#if quote?.status === 'loading'}
                <span class="dot busy" title="正在抓取"></span>
              {:else if quote?.status === 'ok'}
                <span class="dot ok" title="已抓取历史参数"></span>
              {:else if quote?.status === 'error'}
                <span class="dot bad" title="抓取失败"></span>
              {/if}
            </label>
            <input
              id="sym-{i}"
              value={asset.symbol}
              placeholder="SCHD"
              autocapitalize="characters"
              spellcheck="false"
              onchange={(e) => onSymbolChange(e, i)}
            />
          </div>

          <div class="field">
            <label for="w-{i}">目标占比</label>
            <div class="suffixed">
              <input
                id="w-{i}"
                type="number"
                step="1"
                min="0"
                max="100"
                value={((asset.target_weight || 0) * 100).toFixed(0)}
                oninput={(e) =>
                  (asset.target_weight = (Number(e.currentTarget.value) || 0) / 100)}
              />
              <span class="suffix">%</span>
            </div>
          </div>

          <div class="field">
            <label for="s-{i}">已拥有股数</label>
            <input id="s-{i}" type="number" step="any" min="0" bind:value={asset.shares} />
          </div>

          <div class="field">
            <label for="p-{i}">当前股价</label>
            <input id="p-{i}" type="number" step="any" min="0" bind:value={asset.price} />
          </div>

          <button class="icon" onclick={() => removeAsset(i)} title="删除">✕</button>
        </div>

        <div class="row sub">
          <button class="ghost small" onclick={() => toggle(i)}>
            {expanded[i] ? '▾' : '▸'} 增长参数
          </button>
          <!-- 每条统计各自 nowrap，换行只发生在条目之间。
               原先是一整句连着排，于是「股息率 3.50%」会在中间断开，
               数字掉到下一行 —— 看得人以为是两条不同的信息。 -->
          <div class="stats tiny">
            <span class="stat">
              初始市值 {money(asset.shares * asset.price, config.display_currency, rates)}
            </span>
            <span class="stat">股价年增长 {percent(asset.params.price_growth, 1)}</span>
            <span class="stat">股息率 {percent(asset.params.dividend_yield, 2)}</span>
          </div>
        </div>

        <!-- FR-005：参数的来龙去脉。用户得能判断这个数该不该信。 -->
        {#if key}
          <div class="quote-row">
            {#if quote?.status === 'loading'}
              <span class="tiny">搜索中…</span>
            {:else if quote?.status === 'ok'}
              <span class="tiny">
                {#if quote.name && quote.name !== key}
                  <span class="muted-name">{quote.name}</span> ·
                {/if}
                数据截至 {quote.asOf} · 来源 <strong>{SOURCE_LABEL[quote.source] ?? quote.source}</strong>
                {#if quote.fee != null}
                  · 管理费 {percent(quote.fee, 2)}
                  <span class="hint" title="管理费已从基金资产中每日扣除，体现在历史价格里，因此不参与计算"
                    >（仅供参考）</span
                  >
                {/if}
                {#if quote.refreshing}
                  <span class="muted-name">· {SOURCE_LABEL.nasdaq} 查询中…</span>
                {/if}
                {#if quote.missing?.length}
                  <!-- 数据已经对了，只是某个来源没答上话 —— 一句脚注，不是警报 -->
                  <span class="src-missing"
                    >（{missingLabel(quote.missing, quote.failures, quote.source)}）</span
                  >
                {/if}
              </span>

              <div class="lookback" role="group" aria-label="回看窗口">
                {#if quote.fromCache}
                  <!-- 屏幕上这份来自本地已有的数据，用户可能想让它是当天的 -->
                  <button
                    class="ghost small"
                    onclick={() => fetchQuote(key, { force: true })}
                    title="忽略本地数据，重新向数据源索取"
                  >
                    ⟳ 更新数据
                  </button>
                {/if}
                {#if shortHistory(quote)}
                  <!-- 请求了 15 年，实际只有 3 年多。不说的话，用户会以为
                       参数来自 15 年 —— 「回看 15 年」这个说法就成了误导。 -->
                  <span
                    class="tiny hist-note"
                    title="这个标的上市还没那么久，参数是用它成立以来的全部数据算的"
                  >
                    历史仅 {quote.historyYears.toFixed(1)} 年，已用成立以来
                  </span>
                {/if}
                <span class="tiny">回看</span>
                {#each [10, 15] as years (years)}
                  <button
                    class="chip"
                    class:active={(quote.lookbackYears ?? 10) === years}
                    onclick={() => setLookback(key, years)}
                  >
                    {years} 年
                  </button>
                {/each}
              </div>
            {:else if quote?.status === 'error'}
              <span class="tiny err-text">{quote.reason}</span>
              <button class="ghost small" onclick={() => fetchQuote(key, { force: true })}>
                重试
              </button>
            {:else}
              <span class="tiny">
                当前是示例参数。
                <button class="link" onclick={() => fetchQuote(key)}>抓取真实历史数据</button>
              </span>
            {/if}
          </div>

          <!-- 代码清单里没有它。**只是提示，不是拒绝** —— 清单是快照会过时，
               新上市的代码必然不在里面，所以手输参数的通道永远开着。 -->
          {#if quote?.known === false}
            <p class="tiny unknown-note">
              代码清单里没有 <strong>{key}</strong> —— 可能是拼错了，也可能是新上市、清单还没收录。
              确认无误的话，点「增长参数」手动填写。
            </p>
          {/if}
        {/if}

        {#if expanded[i]}
          <div class="row params">
            <div class="field">
              <label for="pg-{i}">股价年增长</label>
              <div class="suffixed">
                <input
                  id="pg-{i}"
                  type="number"
                  step="0.1"
                  value={((asset.params.price_growth || 0) * 100).toFixed(1)}
                  oninput={(e) =>
                    (asset.params.price_growth = (Number(e.currentTarget.value) || 0) / 100)}
                />
                <span class="suffix">%</span>
              </div>
            </div>

            <div class="field">
              <label for="dy-{i}">股息率</label>
              <div class="suffixed">
                <input
                  id="dy-{i}"
                  type="number"
                  step="0.1"
                  value={((asset.params.dividend_yield || 0) * 100).toFixed(2)}
                  oninput={(e) =>
                    (asset.params.dividend_yield =
                      (Number(e.currentTarget.value) || 0) / 100)}
                />
                <span class="suffix">%</span>
              </div>
            </div>

            <div class="field">
              <label for="dg-{i}">股息年增长</label>
              <div class="suffixed">
                <input
                  id="dg-{i}"
                  type="number"
                  step="0.1"
                  value={((asset.params.dividend_growth || 0) * 100).toFixed(1)}
                  oninput={(e) =>
                    (asset.params.dividend_growth =
                      (Number(e.currentTarget.value) || 0) / 100)}
                />
                <span class="suffix">%</span>
              </div>
            </div>

            <div class="field">
              <label for="er-{i}">费用率</label>
              <div class="suffixed">
                <input
                  id="er-{i}"
                  type="number"
                  step="0.01"
                  disabled={asset.params.source !== 'manual'}
                  value={((asset.params.expense_ratio || 0) * 100).toFixed(2)}
                  oninput={(e) =>
                    (asset.params.expense_ratio =
                      (Number(e.currentTarget.value) || 0) / 100)}
                />
                <span class="suffix">%</span>
              </div>
            </div>
          </div>

          {#if asset.params.source !== 'manual'}
            <p class="tiny fee-note">
              抓取模式下费用率强制为 0 —— ETF 管理费已从基金资产中每日扣除，
              已经体现在历史价格里了，再扣一次就是重复计算。
            </p>
          {:else}
            <p class="tiny fee-note">
              手输的是**指数**回报时才需要填费用率；若填的是 ETF 自身历史回报，留 0。
            </p>
          {/if}
        {/if}
      </div>
    {/each}
  </div>

  <div class="summary" class:over={overAllocated}>
    <div class="weight-bar">
      {#each config.assets as asset, i (i)}
        <span
          class="seg"
          style="width: {Math.min(100, (asset.target_weight || 0) * 100)}%"
          title="{asset.symbol || '未命名'} {percent(asset.target_weight, 0)}"
        ></span>
      {/each}
      <span class="seg cash" style="width: {cashWeight * 100}%"></span>
    </div>

    <div class="legend">
      <span>股票合计 <strong>{percent(totalWeight, 0)}</strong></span>
      <span class="cash-legend">
        现金 <strong>{percent(cashWeight, 0)}</strong>
        <span class="tiny">（自动派生，按 {percent(config.cash.annual_rate, 1)} 单利计息）</span>
      </span>
    </div>
  </div>

  {#if overAllocated}
    <div class="banner error" role="alert">
      目标占比合计 {percent(totalWeight, 0)}，超过 100%。请下调。
    </div>
  {/if}
</Card>

<style>
  .assets {
    display: flex;
    flex-direction: column;
    gap: var(--gap-sm);
  }

  .asset {
    background: var(--bg-sunken);
    border-radius: var(--radius);
    padding: var(--gap-sm) var(--gap-sm) 8px;
  }

  .asset.invalid {
    box-shadow: inset 0 0 0 1.5px var(--negative);
  }

  .main {
    align-items: flex-end;
  }

  /* 标的代码不可能超过 5 位，照内容定宽就好。
     原先按比例分（flex: 1.2）会随视口变化，窄一点就把第 5 位吃掉；
     而且大写字母比旁边的数字宽，按同一个比例分本来就不合理。

     选择器**必须**写成 `.field.symbol`：这个元素两个类都有，而下面 `.field`
     的 `flex: 1` 与光写一个 `.symbol` 权重相同，只能靠先后顺序决胜 ——
     `.symbol` 排在前面，于是这条规则一直是死的：输入框照 7em 画成 105px，
     盒子却被压成 60px，多出的 45px 向右溢出去，正好盖住「目标占比」那一格。 */
  .field.symbol {
    flex: 0 0 auto;
  }

  .symbol input {
    /* 5 个大写字母 ≈ 3.3em，加左右 padding 1.6em，再留点余量。
       用 em 而不是 px：字号变了宽度跟着变，不会又切掉一位。 */
    width: 7em;
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
    padding-right: 28px;
  }

  .sub {
    align-items: center;
    gap: var(--gap-sm);
    margin-top: 2px;
  }

  .stats {
    display: flex;
    flex-wrap: wrap;
    justify-content: flex-end;
    flex: 1;
    min-width: 0;
    /* 条目之间只靠间距分开，**不用「·」伪元素** —— 换行到哪里由宽度决定，
       伪元素不知道自己落在行首还是行中：挂在前一条尾巴上，行末会多一个点；
       挂在后一条脑袋上，行首会多一个点。两种都试过，两种都难看。
       间距在换行处自然消失，它不需要知道自己在第几行。 */
    gap: 2px 14px;
    color: var(--text-secondary);
  }

  .stat {
    white-space: nowrap;
  }

  .ghost.small {
    font-size: 12px;
    padding: 4px 8px;
    white-space: nowrap;
    flex: 0 0 auto;
  }

  .params {
    margin-top: 4px;
    padding-top: var(--gap-sm);
    border-top: 1px solid var(--border);
  }

  .fee-note {
    margin-top: 6px;
    line-height: 1.4;
  }

  /* ── FR-005 抓取状态 ───────────────────────────────────────── */

  .head-actions {
    display: flex;
    gap: 6px;
    flex: 0 0 auto;
  }

  .dot {
    display: inline-block;
    width: 6px;
    height: 6px;
    border-radius: 50%;
    margin-left: 5px;
    vertical-align: 1px;
  }

  .dot.ok {
    background: var(--positive, #34c759);
  }

  .dot.bad {
    background: var(--negative);
  }

  .dot.busy {
    background: var(--accent);
    animation: pulse 1s ease-in-out infinite;
  }

  @keyframes pulse {
    50% {
      opacity: 0.25;
    }
  }

  .quote-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    margin-top: 6px;
    line-height: 1.5;
  }

  .quote-row > .tiny {
    flex: 1 1 auto;
    min-width: 0;
  }

  .ok-text {
    color: var(--positive, #34c759);
    font-weight: 600;
  }

  .err-text {
    color: var(--negative);
  }

  .unknown-note {
    margin-top: 4px;
    line-height: 1.45;
    color: var(--text-secondary);
  }

  .unknown-note strong {
    color: var(--text);
    font-family: var(--mono, ui-monospace, SFMono-Regular, Menlo, monospace);
  }

  .muted-name {
    color: var(--text-secondary);
  }

  /* 缺一个来源的备注：比正文更轻，**不用 --negative** —— 它是说明不是错误 */
  .src-missing {
    margin-left: 4px;
    color: var(--text-tertiary);
  }

  /* 历史不够长时的说明。也不用 --negative：参数本身是对的（退回成立以来正是
     应有的行为），这里只是把「回看 15 年」这句话补完整。 */
  .hist-note {
    white-space: nowrap;
  }

  .hint {
    color: var(--text-tertiary);
    cursor: help;
    border-bottom: 1px dotted var(--text-tertiary);
  }

  .lookback {
    display: flex;
    align-items: center;
    gap: 4px;
    flex: 0 0 auto;
  }

  .chip {
    font: inherit;
    font-size: 11px;
    line-height: 1;
    padding: 4px 8px;
    border-radius: 999px;
    border: 1px solid var(--border);
    background: transparent;
    color: var(--text-secondary);
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .chip:hover {
    border-color: var(--accent);
    color: var(--accent);
  }

  .chip.active {
    background: var(--accent);
    border-color: var(--accent);
    color: #fff;
  }

  .link {
    font: inherit;
    font-size: inherit;
    padding: 0;
    border: none;
    background: none;
    color: var(--accent);
    cursor: pointer;
    text-decoration: underline;
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }

  .summary {
    margin-top: var(--gap);
  }

  .weight-bar {
    display: flex;
    height: 8px;
    border-radius: 4px;
    overflow: hidden;
    background: var(--bg-sunken);
    gap: 1.5px;
  }

  .seg {
    background: var(--accent);
    opacity: 0.85;
  }

  .seg:nth-child(2n) {
    opacity: 0.65;
  }

  .seg.cash {
    background: var(--text-tertiary);
    opacity: 0.35;
  }

  .legend {
    display: flex;
    justify-content: space-between;
    gap: var(--gap);
    margin-top: 8px;
    font-size: 13px;
    color: var(--text-secondary);
  }

  /* 窄屏不再单独给代码框开后门。
     这里原先有一条 `@media (max-width: 900px) { .symbol { flex: 1 1 140px } }`，
     本意是窄屏让代码框跟着自适应。它有两重毛病：一是权重比不上 `.field`，
     从来没生效过（真正的宽度来自另一条 `width: 100%`，纯属意外）；二是就算
     生效也是错的 —— 代码最多 5 位，`flex: 1 1 140px` 会让它跟别的格子一起
     长到 285px，比目标占比还宽，看着莫名其妙。
     内容多宽就多宽，在所有宽度下都成立，不需要分屏幕尺寸。 */

  .legend strong {
    color: var(--text);
    font-weight: 600;
  }

  .cash-legend {
    text-align: right;
  }

  .summary.over .weight-bar {
    box-shadow: 0 0 0 1.5px var(--negative);
  }
</style>

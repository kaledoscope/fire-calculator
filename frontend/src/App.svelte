<script>
  import ConfigBar from './lib/components/ConfigBar.svelte'
  import GoalInput from './lib/components/GoalInput.svelte'
  import PlanInput from './lib/components/PlanInput.svelte'
  import PortfolioInput from './lib/components/PortfolioInput.svelte'
  import ResultsPanel from './lib/components/ResultsPanel.svelte'
  import SettingsInput from './lib/components/SettingsInput.svelte'
  import Welcome from './lib/components/Welcome.svelte'
  import { sanitizeConfig } from './lib/config.js'
  import { api, ApiError } from './lib/api.js'
  import { clone, defaultConfig } from './lib/defaults.js'

  /** 顶层编排。只做三件事：持有状态、调度计算、摆版面。
   *  它不知道任何公式 —— 计算全部在后端（SRS §2.1 分层纪律）。
   */

  let started = $state(false)
  let config = $state(defaultConfig())
  let result = $state(null)
  let growthRate = $state(0)
  let rates = $state(null)
  let error = $state(null)
  let loading = $state(false)

  const currencies = [
    { id: 'USD', label: 'USD $' },
    { id: 'CNY', label: 'CNY ¥' },
    { id: 'JPY', label: 'JPY ¥' },
  ]

  // 汇率只用于显示换算，抓一次就够（FX-1：绝不参与计算）
  $effect(() => {
    api
      .fx()
      .then((r) => (rates = r))
      .catch(() => (rates = null))
  })

  // ── 计算由用户发起，不再「一动就重算」 ──────────────────────
  //
  // 原先配置一变就重算（350ms 防抖）。这在只有几个数字的时候没问题，但
  // 「360」要依次经过 3 / 36 / 360，中间那两次也在算 —— 屏幕上的期末总值
  // 啪啪啪地变，用户填到一半就被打断，还看不出什么时候算完了。
  //
  // 现在拆成两件事：
  //   **是否该重算由数据推导**（`dirty`）—— 所以「参数已改 · 待计算」永远准；
  //   **要不要真的算由显式动作决定** —— 按钮、⌘↵，以及下拉框这类
  //   「一次成型、不存在填到一半」的控件。
  //
  // JSON.stringify 有两个作用：一是把当前配置固化成快照，请求体与触发它的
  // 事实严格一致；二是**深度触碰每个字段**，$derived 才能对嵌套修改建立依赖
  // —— 否则改 `assets[0].price` 这种深层字段不会触发。
  const payloadJson = $derived.by(() => {
    try {
      return JSON.stringify(sanitizeConfig(config))
    } catch {
      return null // 序列化不了就当作没有可比对的快照，宁可不算也不要抛
    }
  })

  /** 产生当前 `result` 的那份快照。null 表示「还没算过」。 */
  let computedJson = $state(null)

  /** 屏幕上的数是不是已经跟不上参数了。 */
  const dirty = $derived(payloadJson !== null && payloadJson !== computedJson)

  /** 正在算的那份快照。用来挡掉「同一样东西算两遍」。 */
  let inflightJson = null
  let controller = null
  let seq = 0

  async function run() {
    const snapshot = payloadJson
    if (!started || !snapshot) return
    // 显式调用与委托监听可能在同一拍里各来一次（比如「重置」），
    // 同一份快照没必要算两遍 —— 后一次会白白把前一次掐掉。
    if (snapshot === inflightJson) return

    inflightJson = snapshot
    const mine = ++seq
    controller?.abort()
    controller = new AbortController()
    const signal = controller.signal

    loading = true
    try {
      // 提交前先滤掉还没填代码的草稿行 —— 后端要求 symbol 非空，这是对的
      // （没代码的标的无从定价），所以不该让它看见草稿。详见 lib/config.js
      const body = JSON.parse(snapshot)
      const [computed, blended] = await Promise.all([
        api.compute(body, signal),
        api.growthRate(body).catch(() => ({ growth_rate: 0 })),
      ])
      if (mine !== seq) return // 已有更新的一次计算在跑，这次的结果作废
      result = computed
      growthRate = blended.growth_rate ?? 0
      computedJson = snapshot
      error = null
    } catch (err) {
      if (err?.name === 'AbortError') return
      if (mine !== seq) return
      error = err instanceof ApiError ? err : new ApiError('计算失败')
      // 出错时**保留上一次的结果** —— 输入到一半的中间态不该把屏幕清空
    } finally {
      if (mine === seq) {
        loading = false
        inflightJson = null
      }
    }
  }

  /** 不脏就什么都不做 —— 所以多点一次也没有代价，委托监听才敢铺开。 */
  function commit() {
    if (dirty) run()
  }

  // 委托到两个监听上，而不是去改十个调用点。
  //
  // **表单控件必须在 `change` 上听，不能在 `click` 上听。** 勾选框和下拉框的
  // 值不是点击时改的：浏览器把「切换选中」当作这次点击的**默认动作**，
  // 先派发 click、再改值、最后才派发 input / change。挂在 click 上会早一步
  // 读到旧配置 —— 于是 `dirty` 还是 false，`commit()` 一声不响地什么也不做，
  // 界面看着就像「勾了但没反应」。而 `change` 冒泡到 document 时，Svelte 自己
  // 注册在元素上的绑定处理器已经跑完了，读到的才是新值。
  //
  // 只认「一次成型」的控件：下拉、勾选框、单选、按钮。文本框里每一次击键
  // 都不算 —— 那正是用户说的「啪啪啪地变」。
  $effect(() => {
    const onFormChange = (event) => {
      const t = event.target
      if (t instanceof HTMLSelectElement) return commit()
      if (t instanceof HTMLInputElement && (t.type === 'checkbox' || t.type === 'radio'))
        return commit()
      // 其余（文本框、数字框）等用户点「计算」
    }

    const onClick = (event) => {
      const t = event.target
      if (t instanceof Element && t.closest('button')) return commit()
    }

    const onKey = (event) => {
      if ((event.metaKey || event.ctrlKey) && event.key === 'Enter') {
        event.preventDefault()
        commit() // 不脏就什么都不做，所以随便按也没有代价
      }
    }

    document.addEventListener('change', onFormChange)
    document.addEventListener('click', onClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('change', onFormChange)
      document.removeEventListener('click', onClick)
      document.removeEventListener('keydown', onKey)
    }
  })

  function pickMarket() {
    started = true
    run() // 进主界面的第一屏不该是空的
  }

  function loadConfig(loaded) {
    // 「载入」是先从后端取回来、再把配置交过来的，中间隔着一次网络往返 ——
    // 点击事件早就冒泡完了，委托监听等不到它。所以这里得自己喊一声。
    config = clone(loaded)
    run()
  }

  function reset() {
    if (!confirm('恢复默认配置？当前未保存的改动会丢失。')) return
    config = defaultConfig()
    commit()
  }
</script>

{#if !started}
  <Welcome onSelect={pickMarket} />
{:else}
  <header>
    <div class="inner">
      <button class="brand" onclick={() => (started = false)} title="回到市场选择">
        未来收益规划器
      </button>

      <div class="tools">
        <label class="sr-only" for="currency">显示货币</label>
        <select
          id="currency"
          class="currency"
          bind:value={config.display_currency}
          title="只影响结果的显示换算。所有输入一律按美元填 —— 美股价格本来就是美元，汇率不参与任何计算。"
        >
          {#each currencies as c (c.id)}
            <option value={c.id}>{c.label}</option>
          {/each}
        </select>

        <ConfigBar {config} onLoad={loadConfig} />

        <button class="ghost" onclick={reset}>重置</button>
      </div>
    </div>
  </header>

  <main>
    {#if error}
      <div class="banner error top" role="alert">
        <span>
          {error.message}
          {#if error.field}
            <span class="field">（{error.field}）</span>
          {/if}
        </span>
      </div>
    {/if}

    <!-- 铁律 Ⅱ：四块输入各自独立。删掉任意一块，其余照常工作 ——
         比如删掉 FIRE 目标，报告里就少掉 FIRE 那一节，其余一字不动。 -->
    <div class="layout">
      <div class="column inputs">
        <PortfolioInput {config} {rates} />
        <PlanInput {config} />
        <GoalInput {config} {rates} />
        <SettingsInput {config} />
      </div>

      <div class="column outputs">
        <ResultsPanel
          {result}
          {growthRate}
          currency={config.display_currency}
          {rates}
          {loading}
          {dirty}
          onCompute={commit}
        />
      </div>
    </div>

    <footer>
      <p class="tiny">
        所有数字都是**确定性外推**，不是预测。它回答的是「如果未来一直这样，会怎样」，
        而不是「未来会怎样」。
      </p>
    </footer>
  </main>
{/if}

<style>
  header {
    position: sticky;
    top: 0;
    z-index: 20;
    background: color-mix(in srgb, var(--bg) 82%, transparent);
    backdrop-filter: saturate(180%) blur(20px);
    -webkit-backdrop-filter: saturate(180%) blur(20px);
    border-bottom: 1px solid var(--border);
  }

  .inner {
    max-width: 1680px;
    margin: 0 auto;
    padding: 10px var(--gap-lg);
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: var(--gap);
  }

  .brand {
    font-size: 16px;
    font-weight: 600;
    letter-spacing: -0.02em;
    color: var(--text);
    background: transparent;
    padding: 6px 0;
    border-radius: 0;
  }

  .brand:hover:not(:disabled) {
    background: transparent;
    color: var(--accent);
  }

  .tools {
    display: flex;
    align-items: center;
    gap: var(--gap-sm);
    flex-wrap: wrap;
    justify-content: flex-end;
  }

  .currency {
    width: auto;
    font-size: 13px;
    padding: 6px 10px;
  }

  main {
    max-width: 1680px;
    margin: 0 auto;
    padding: var(--gap-lg);
  }

  .banner.top {
    margin-bottom: var(--gap);
  }

  .field {
    opacity: 0.75;
    font-family: var(--font-mono);
    font-size: 12px;
  }

  .layout {
    display: grid;
    /* 左列是输入（改参数的地方），右列是结果（图表 + 逐年明细 + 各项指标）。
       右列内容重得多，也才是用户真正盯着看的那一半，所以按 **1 : 2** 分，
       而不是对半分。这是成熟做法：Home Assistant 侧栏约 30/70，
       Blueprint 栅格的 span-8 / span-16 就是黄金比 1/3 : 2/3。

       左列的 480 是**下限不是定宽** —— 它一行里塞着 5 个控件、一条统计和
       「增长参数」，低于 480 那条统计就会断成两行。下限只在小窗口下兜底，
       宽屏上两列一起长。 */
    grid-template-columns: minmax(480px, 1fr) minmax(0, 2fr);
    gap: var(--gap-lg);
    align-items: start;
  }

  .column {
    min-width: 0;
  }

  .outputs {
    /* 输入列很长，结果列跟着滚会很难用 —— 但整列 sticky 又会被截断。
       这里不做 sticky，靠合理的卡片顺序让重要的数先出现。 */
    display: flex;
    flex-direction: column;
    gap: var(--gap);
  }

  footer {
    margin-top: var(--gap-xl);
    padding-top: var(--gap);
    border-top: 1px solid var(--border);
    text-align: center;
  }

  .sr-only {
    position: absolute;
    width: 1px;
    height: 1px;
    overflow: hidden;
    clip: rect(0 0 0 0);
    white-space: nowrap;
  }

  @media (max-width: 1080px) {
    .layout {
      grid-template-columns: 1fr;
      gap: var(--gap);
    }

    .inner {
      padding: 10px var(--gap);
      flex-wrap: wrap;
    }

    main {
      padding: var(--gap);
    }
  }
</style>

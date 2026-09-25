<script module>
  /**
   * 折叠状态存在哪儿。
   *
   * 存成一个对象、键是**卡片标题**而不是下标：标题就是这张卡的身份，
   * 加一张新卡不会让别人的状态串门。也刻意按标题而不是「左列/右列」分开存 ——
   * 卡片挪到别处时，用户对它的选择应该跟着走。
   */
  const STORE_KEY = 'dca.collapsed'

  function readStore() {
    try {
      return JSON.parse(localStorage.getItem(STORE_KEY) ?? '{}') ?? {}
    } catch {
      // 隐私模式下 localStorage 一读就抛。折叠状态不值得为它把页面弄白。
      return {}
    }
  }

  function remember(title, collapsed) {
    try {
      const all = readStore()
      if (collapsed) all[title] = true
      else delete all[title]
      localStorage.setItem(STORE_KEY, JSON.stringify(all))
    } catch {
      /* 存不上就算了 */
    }
  }
</script>

<script>
  /**
   * 统一的卡片外壳：标题 + 右上角控件 + 可折叠的主体。
   *
   * 之所以要做成一个组件而不是给每张卡各写一遍（铁律 Ⅱ）：十一张卡的头部
   * 结构从此**本来就一致**，也就不存在「这张有 h2、那张没有」这种漂移 ——
   * 期末总值当初就是这么漏掉大标题的。折叠的动效也只写一遍。
   *
   * 头部**永远在**：收起之后标题仍然看得见，否则用户不知道该点哪儿把它放回来。
   */
  let { title, collapsed = $bindable(false), headExtra = null, children } = $props()

  const bodyId = $props.id()

  let restored = false

  // 先读一次，之后只管写。`$effect` 只在浏览器里跑，所以服务端渲染出来的
  // 一定是展开的 —— 水合之后才收起，不会有「闪一下再收」的抖动。
  $effect(() => {
    if (restored) return
    restored = true
    collapsed = readStore()[title] === true
  })

  $effect(() => {
    if (!restored) return // 还没读就先别写，否则会用默认值把存着的状态盖掉
    remember(title, collapsed)
  })

  function toggle() {
    collapsed = !collapsed
  }
</script>

<div class="card" class:collapsed>
  <div class="section-head">
    <h2>{title}</h2>

    <div class="head-right">
      {@render headExtra?.()}
      <button
        class="chevron"
        type="button"
        aria-expanded={!collapsed}
        aria-controls={bodyId}
        title={collapsed ? '展开' : '收起'}
        onclick={toggle}
      >
        <!-- 向下的尖角；收起时整体转 -90° 变成向右 —— 平台惯例（iOS 的
             disclosure indicator 就是向右的），用户不用学。 -->
        <svg viewBox="0 0 16 16" width="15" height="15" aria-hidden="true">
          <path
            d="M4 6.2 8 10.2 12 6.2"
            fill="none"
            stroke="currentColor"
            stroke-width="1.7"
            stroke-linecap="round"
            stroke-linejoin="round"
          />
        </svg>
      </button>
    </div>
  </div>

  <div class="body" id={bodyId}>
    <div class="body-inner">
      {@render children?.()}
    </div>
  </div>
</div>

<style>
  /* ── 头部右侧：卡片自己的控件 + 折叠箭头 ─────────────────────── */

  .head-right {
    display: flex;
    align-items: baseline;
    justify-content: flex-end;
    gap: 10px;
    flex: 0 1 auto;
    min-width: 0;
  }

  .chevron {
    /* 箭头跟着标题那一行**居中**，不跟基线 —— 它是个图形不是文字，
       按基线对会让它掉到标题下面半行。 */
    align-self: center;
    flex: 0 0 auto;
    display: grid;
    place-items: center;
    width: 26px;
    height: 26px;
    padding: 0;
    border: none;
    border-radius: 50%;
    background: transparent;
    color: var(--text-tertiary);
    cursor: pointer;
    transition:
      background-color 200ms ease,
      color 200ms ease;
  }

  .chevron:hover {
    background: var(--bg-sunken);
    color: var(--text);
  }

  .chevron svg {
    display: block;
    transition: transform 380ms cubic-bezier(0.32, 0.72, 0, 1);
  }

  .collapsed .chevron svg {
    transform: rotate(-90deg);
  }

  /* ── 折叠 ──────────────────────────────────────────────────── */

  /* 高度用 `grid-template-rows: 1fr → 0fr` 做，不用 JS 量像素。
     关键在缓动：`cubic-bezier(0.32, 0.72, 0, 1)` 是**快出慢收**，
     起步就像被弹开、末尾稳稳停住 —— 这是 Apple 展开的手感。
     默认的 `ease` 两头都软，看着塌。 */
  .body {
    display: grid;
    grid-template-rows: 1fr;
    transition: grid-template-rows 380ms cubic-bezier(0.32, 0.72, 0, 1);
  }

  .collapsed .body {
    grid-template-rows: 0fr;
  }

  /* `overflow: hidden` 是 0fr 能生效的前提 —— 行高归零了，内容不裁就只是
     溢出到盒子外面继续显示。`min-height: 0` 同理：网格项默认不肯缩到内容高度
     以下，少了它 0fr 也白设。 */
  .body-inner {
    overflow: hidden;
    min-height: 0;
    opacity: 1;
    visibility: visible;
    /* 透明度与高度**错开**：内容 200ms 淡出、盒子 380ms 收完。
       两个一起动会糊成一团，看不出是「先空掉、再合上」。 */
    transition:
      opacity 200ms ease,
      visibility 0s linear 0s;
  }

  .collapsed .body-inner {
    opacity: 0;
    /* 收起后必须真的拿掉，否则里面那些输入框还能被 Tab 键选中 ——
       焦点跑进一个看不见的框里，用户完全不知道发生了什么事。 */
    visibility: hidden;
    transition:
      opacity 200ms ease,
      visibility 0s linear 200ms;
  }

  /* 收起时把头部下方那 16px 也一并收掉。不这么做的话，盒子会缩到
     「标题 + 16px 空白」，看着像没收干净。它和主体用同一条曲线同时长，
     两段位移合起来就是一个连贯的动作。 */
  .section-head {
    transition: margin-bottom 380ms cubic-bezier(0.32, 0.72, 0, 1);
  }

  .collapsed .section-head {
    margin-bottom: 0;
  }

  /* 系统里设了「减弱动态效果」就别动 —— 前庭敏感的用户会因为这类
     位移不适。这不是可选项，是平台规范。 */
  @media (prefers-reduced-motion: reduce) {
    .body,
    .body-inner,
    .section-head,
    .chevron svg {
      transition: none;
    }
  }
</style>

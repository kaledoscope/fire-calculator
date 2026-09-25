<script>
  /** 欢迎界面：选市场。设想.md 的第一步。 */
  let { onSelect } = $props()

  const markets = [
    { id: 'US', name: '美股', note: '默认美元计价', enabled: true },
    { id: 'HK', name: '港股', note: '尚未支持', enabled: false },
    { id: 'CN', name: 'A 股', note: '尚未支持', enabled: false },
    { id: 'JP', name: '日股', note: '尚未支持', enabled: false },
  ]
</script>

<div class="welcome">
  <div class="hero">
    <h1>未来收益规划器</h1>
    <p class="lede">
      输入你的组合与定投计划，算出复利会把它们带到哪里 ——<br />
      以及那个数字，够不够你不上班。
    </p>
  </div>

  <div class="markets">
    <p class="tiny pick">选择市场</p>
    <div class="grid">
      {#each markets as market (market.id)}
        <button
          class="market"
          class:disabled={!market.enabled}
          disabled={!market.enabled}
          onclick={() => market.enabled && onSelect(market.id)}
        >
          <span class="name">{market.name}</span>
          <span class="note">{market.note}</span>
        </button>
      {/each}
    </div>
    <p class="tiny footnote">
      本期只做美股。汇率只影响显示，不参与任何计算。
    </p>
  </div>
</div>

<style>
  .welcome {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: var(--gap-xl);
    padding: var(--gap-xl) var(--gap);
    text-align: center;
  }

  .hero {
    max-width: 620px;
  }

  .lede {
    margin-top: var(--gap);
    font-size: 19px;
    line-height: 1.5;
    color: var(--text-secondary);
    letter-spacing: -0.01em;
  }

  .markets {
    width: 100%;
    max-width: 620px;
  }

  .pick {
    margin-bottom: var(--gap-sm);
  }

  .grid {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
    gap: var(--gap-sm);
  }

  .market {
    display: flex;
    flex-direction: column;
    gap: 3px;
    align-items: flex-start;
    padding: 18px 20px;
    border-radius: var(--radius);
    background: var(--bg-elevated);
    box-shadow: var(--shadow-sm);
    text-align: left;
  }

  .market:not(.disabled):hover {
    box-shadow: var(--shadow);
    background: var(--bg-elevated);
  }

  .market.disabled {
    opacity: 0.45;
    cursor: not-allowed;
    box-shadow: none;
    background: var(--bg-sunken);
  }

  .name {
    font-size: 17px;
    font-weight: 600;
  }

  .note {
    font-size: 12px;
    color: var(--text-tertiary);
  }

  .footnote {
    margin-top: var(--gap);
  }
</style>

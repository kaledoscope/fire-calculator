<script>
  import Card from './Card.svelte'

  /** 计算设置 + 税务（FR-003 / FR-020 / A20~A24） */
  let { config } = $props()

  const horizons = [120, 180, 240, 300, 360, 420, 480, 600]

  function pct(field) {
    return {
      value: ((config[field.group][field.key] || 0) * 100).toFixed(field.decimals ?? 2),
      oninput: (e) => {
        config[field.group][field.key] = (Number(e.currentTarget.value) || 0) / 100
      },
    }
  }
</script>

<Card title="计算设置">
  <div class="row">
    <div class="field">
      <label for="horizon">计算年限</label>
      <select id="horizon" bind:value={config.settings.horizon_months}>
        {#each horizons as h (h)}
          <option value={h}>{h / 12} 年</option>
        {/each}
      </select>
    </div>

    <div class="field">
      <label for="dmode">股息去向</label>
      <select id="dmode" bind:value={config.settings.dividend_mode}>
        <option value="drip">再投资（DRIP）</option>
        <option value="cash">累积为现金</option>
      </select>
    </div>

    <div class="field">
      <label for="cashrate">现金年利率</label>
      <div class="suffixed">
        <input
          id="cashrate"
          type="number"
          step="0.1"
          value={((config.cash.annual_rate || 0) * 100).toFixed(1)}
          oninput={(e) =>
            (config.cash.annual_rate = (Number(e.currentTarget.value) || 0) / 100)}
        />
        <span class="suffix">%</span>
      </div>
    </div>

    <div class="field">
      <label for="infl">通胀率</label>
      <div class="suffixed">
        <input
          id="infl"
          type="number"
          step="0.1"
          value={((config.settings.inflation_rate || 0) * 100).toFixed(1)}
          oninput={(e) =>
            (config.settings.inflation_rate =
              (Number(e.currentTarget.value) || 0) / 100)}
        />
        <span class="suffix">%</span>
      </div>
    </div>
  </div>

  <label class="check">
    <input type="checkbox" bind:checked={config.settings.rebalance_annually} />
    <span>每年再平衡一次，把偏离的配比拉回目标</span>
  </label>

  <div class="divider"></div>

  <h3 class="sub-head">税务</h3>
  <div class="row">
    <div class="field">
      <label for="dtax">股息税</label>
      <div class="suffixed">
        <input
          id="dtax"
          type="number"
          step="1"
          {...pct({ group: 'tax', key: 'dividend_tax', decimals: 1 })}
        />
        <span class="suffix">%</span>
      </div>
    </div>

    <div class="field">
      <label for="ctax">资本利得税</label>
      <div class="suffixed">
        <input
          id="ctax"
          type="number"
          step="1"
          {...pct({ group: 'tax', key: 'capital_gains_tax', decimals: 1 })}
        />
        <span class="suffix">%</span>
      </div>
    </div>
  </div>

  <p class="tiny note">
    默认股息税 10% —— 中国内地税务居民提交 W-8BEN 后适用中美税收协定税率
    （未提交则默认 30%）。资本利得美国不对非居民征收，故默认 0%，
    <strong>但这不代表无需申报</strong>。股息税在派息时扣，所以 DRIP 买回的是税后股息。
  </p>
  <p class="tiny note">
    现金按月计息、<strong>单利</strong>：利息只按本金余额计，利息本身不再生息。
  </p>
</Card>

<style>
  .divider {
    height: 1px;
    background: var(--border);
    margin: var(--gap) 0;
  }

  .sub-head {
    margin-bottom: var(--gap-sm);
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

  .check {
    display: flex;
    align-items: center;
    gap: 9px;
    margin-top: var(--gap);
    font-size: 14px;
    color: var(--text);
    cursor: pointer;
  }

  .check input {
    width: 16px;
    height: 16px;
    flex: 0 0 auto;
    accent-color: var(--accent);
    cursor: pointer;
  }

  .note {
    margin-top: var(--gap-sm);
    line-height: 1.5;
  }

  .note strong {
    color: var(--text-secondary);
  }
</style>

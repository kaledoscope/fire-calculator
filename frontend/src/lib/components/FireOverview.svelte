<script>
  import Card from './Card.svelte'
  import { money, moneyCompact, months, percent } from '../format.js'

  /** FR-012 输出侧：FIRE 目标的达成情况。
   *
   * 两种退休方式共用一种行 —— 后端把它们都化成 `FireGoalHit`，只靠
   * `criterion` 区分判据，所以这里不必为每种模式各写一套渲染。
   *
   * 全部数字（门槛、达成度、通胀折算后的名义额）都由后端算好。这里连
   * 「还差多少」都不自己减 —— 后端给的是「当前 $Y / 目标 $Z / 达成度 P%」，
   * 三个数已经把差额说尽了，前端再做一次减法就是第二份口径。
   */
  let { fire = null, monthly = [], currency = 'USD', rates = null } = $props()

  const goals = $derived(fire?.goals ?? [])
  const isIncome = $derived(fire?.mode === 'income')

  const finalValue = $derived(monthly.length ? monthly[monthly.length - 1].total_value : 0)
  /** 期末是第几个月 —— 未达成时要说清「这是第几个月的门槛」。 */
  const lastMonth = $derived(monthly.length ? monthly[monthly.length - 1].month : 1)

  /** 退休后购买力的年变化（名义股息增长 − 通胀）。只在吃息模式下有值。 */
  const real = $derived(fire?.real_dividend_growth ?? null)
</script>

<Card title="FIRE 进度">
  {#snippet headExtra()}
    <span class="tiny">期末 {moneyCompact(finalValue, currency, rates)}</span>
  {/snippet}

  {#if goals.length === 0}
    <p class="muted empty">
      {#if isIncome}
        还没设定目标。在上方「FIRE 目标」里填一个想覆盖的月支出。
      {:else}
        还没设定 FIRE 目标。在上方「FIRE 目标」里勾一个档位试试。
      {/if}
    </p>
  {:else}
    <ul class="list">
      {#each goals as g (g.label + g.criterion)}
        <li class="item" class:done={g.reached}>
          <div class="top">
            <span class="badge">{g.criterion === 'income' ? '吃息' : '提取'}</span>
            <span class="label">{g.label}</span>
            <span class="amount">
              {money(g.target, currency, rates)}
              {#if g.criterion === 'income'}<span class="per">/年</span>{/if}
            </span>
          </div>

          {#if g.criterion === 'income'}
            <!-- 今日购买力 → 达成当年名义额。**必须并排列出**：通胀折算
                 是只看不见的手，不并排就只会看到目标「自己变大了」。 -->
            <div class="infl">
              <div class="cell">
                <span class="tiny">今日购买力</span>
                <strong>{money(g.monthly_today, currency, rates)}<span class="per">/月</span></strong>
                <span class="tiny sub">{money(g.target_today, currency, rates)}/年</span>
              </div>

              <span class="arrow" aria-hidden="true">→</span>

              <div class="cell">
                <span class="tiny">
                  {g.reached ? '达成当年门槛' : `第 ${months(lastMonth)}的门槛`}
                </span>
                <strong>{money(g.target, currency, rates)}<span class="per">/年</span></strong>
                <span class="tiny sub">
                  {#if g.inflation_adjusted}
                    按通胀折算后
                  {:else}
                    未按通胀折算
                  {/if}
                </span>
              </div>
            </div>
          {/if}

          <div class="bar">
            <span class="fill" style="width: {Math.min(100, g.progress * 100)}%"></span>
          </div>

          <div class="foot">
            <span class="tiny hint">
              {#if g.criterion === 'income'}
                <!-- 写「期末」而不是「当前」：这两个数取自序列**最后一个月**，
                     而已达成的目标上面写的是「达成当年门槛」—— 一个第 13 年、
                     一个第 30 年，用「当前」去指后者只会让人以为对不上账，
                     恰恰是列出税前 / 税后想避免的那种困惑。 -->
                期末税后股息 {money(g.current_annual_net, currency, rates)}/年
                <span class="muted">· 税前 {money(g.current_annual_gross, currency, rates)}</span>
              {:else}
                {g.detail}
              {/if}
            </span>
            <span class="when">
              {#if g.reached}
                第 {months(g.month)} 达成
              {:else}
                这个年限内达不到 · 已到 {Math.round(g.progress * 100)}%
              {/if}
            </span>
          </div>
        </li>
      {/each}
    </ul>

    {#if isIncome && real !== null}
      <!-- 这条是口径台阶，不是误差：达成月份按「股息一直再投资」推算，
           而吃息退休恰恰意味着停止再投资。股数从此恒定，名义股息只按
           {dividend_growth} 增长 —— 它跑不跑得过通胀，决定了这笔退休金
           是保值还是慢慢缩水。 -->
      <p class="note" class:warn={real < 0}>
        {#if real < 0}
          退休后本金不动、股数不再增加，股息只按 {percent(fire.dividend_growth, 1)}/年 增长，
          而通胀是 {percent(fire.inflation_rate, 1)}/年 ——
          <strong>实际购买力每年缩水约 {percent(-real, 1)}</strong>。
          上面的达成时间是按「股息一直再投资」推出来的，退休后不再成立。
        {:else}
          退休后本金不动，股息年增 {percent(fire.dividend_growth, 1)}，
          跑得赢 {percent(fire.inflation_rate, 1)} 的通胀，购买力不缩水。
        {/if}
      </p>
    {/if}
  {/if}
</Card>

<style>
  .list {
    display: flex;
    flex-direction: column;
    gap: var(--gap);
  }

  .top {
    display: flex;
    align-items: baseline;
    gap: var(--gap-xs);
    margin-bottom: 7px;
  }

  .badge {
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 5px;
    background: var(--bg-sunken);
    color: var(--text-tertiary);
    flex: 0 0 auto;
  }

  .done .badge {
    background: color-mix(in srgb, var(--accent) 12%, transparent);
    color: var(--accent);
  }

  .label {
    font-size: 14px;
    font-weight: 500;
    flex: 1;
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .amount {
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
    white-space: nowrap;
  }

  .per {
    font-size: 11px;
    font-weight: 400;
    color: var(--text-tertiary);
    margin-left: 1px;
  }

  .done .amount {
    color: var(--accent);
  }

  /* ── 今日购买力 → 达成当年 ────────────────────────────────── */

  .infl {
    display: flex;
    align-items: center;
    gap: var(--gap-sm);
    margin-bottom: 9px;
    padding: 9px 11px;
    border-radius: var(--radius-sm);
    background: var(--bg-sunken);
  }

  .cell {
    display: flex;
    flex-direction: column;
    gap: 1px;
    min-width: 0;
    flex: 1;
  }

  .cell strong {
    font-size: 15px;
    font-weight: 600;
    font-variant-numeric: tabular-nums;
    letter-spacing: -0.01em;
  }

  .cell .sub {
    font-variant-numeric: tabular-nums;
  }

  .arrow {
    flex: 0 0 auto;
    color: var(--text-tertiary);
    font-size: 13px;
  }

  /* ── 进度条 ───────────────────────────────────────────────── */

  .bar {
    height: 5px;
    border-radius: 3px;
    background: var(--bg-sunken);
    overflow: hidden;
  }

  .fill {
    display: block;
    height: 100%;
    border-radius: 3px;
    background: var(--accent);
    opacity: 0.85;
    transition: width 0.35s cubic-bezier(0.25, 0.1, 0.25, 1);
  }

  .done .bar {
    background: color-mix(in srgb, var(--accent) 16%, transparent);
  }

  .foot {
    display: flex;
    justify-content: space-between;
    gap: var(--gap-sm);
    margin-top: 6px;
  }

  .hint {
    min-width: 0;
  }

  .when {
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    color: var(--text-secondary);
    text-align: right;
    flex: 0 0 auto;
  }

  .done .when {
    color: var(--accent);
    font-weight: 500;
  }

  /* ── 可持续性脚注 ─────────────────────────────────────────── */

  .note {
    margin-top: var(--gap);
    padding-top: var(--gap-sm);
    border-top: 1px solid var(--border);
    font-size: 12px;
    line-height: 1.6;
    color: var(--text-secondary);
  }

  /* 缩水用灰不用红：这不是错误，是吃息退休的固有性质 ——
     标红等于告诉用户「你算错了」，而它算得完全正确。 */
  .note.warn strong {
    color: var(--text);
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
    line-height: 1.5;
  }
</style>

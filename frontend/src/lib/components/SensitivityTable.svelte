<script>
  import Card from './Card.svelte'
  import { months, monthDelta, percent } from '../format.js'

  /** FR-014：敏感度 —— 增长率差一点，结果差多少。
   *
   * 后端已经按「基准 → 逐级下调」的顺序给好（delta = 0 打头），
   * 这里原样渲染，基准行在第一行。
   */
  let { sensitivity = [], baseMonth = null, baseLabel = '目标' } = $props()
</script>

<Card title="敏感度">
  {#snippet headExtra()}
    <span class="tiny">锚定目标：{baseLabel}</span>
  {/snippet}

  {#if sensitivity.length === 0}
    <p class="muted empty">没有可达成的目标档位，算不出敏感度。</p>
  {:else}
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>情景</th>
            <th class="num">达成时间</th>
            <th class="num">相对基准</th>
          </tr>
        </thead>
        <tbody>
          {#each sensitivity as row (row.delta)}
            <tr class:base={row.delta === 0}>
              <td>
                {#if row.delta === 0}
                  <span class="tag base-tag">基准</span>
                {:else}
                  <span class="tag" class:down={row.delta < 0}>
                    {percent(row.delta, 1)}
                  </span>
                {/if}
              </td>
              <td class="num">{months(row.month)}</td>
              <td class="num" class:slip={row.month !== null && baseMonth !== null && row.month > baseMonth}>
                {row.delta === 0 ? '—' : monthDelta(baseMonth, row.month)}
              </td>
            </tr>
          {/each}
        </tbody>
      </table>
    </div>

    <p class="tiny note">
      年化增长率每差 1 个百分点，达成目标的时间就会差出好几年 ——
      这就是为什么不要把某一年的收益率当成长期预期。
    </p>
  {/if}
</Card>

<style>
  .table-wrap {
    margin: 0 calc(-1 * var(--gap-sm));
    overflow-x: auto;
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  tr.base td {
    background: color-mix(in srgb, var(--accent) 6%, transparent);
  }

  .tag {
    display: inline-block;
    padding: 2px 8px;
    border-radius: 6px;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
    background: var(--bg-sunken);
    color: var(--text-secondary);
  }

  .tag.down {
    color: var(--negative);
    background: color-mix(in srgb, var(--negative) 10%, transparent);
  }

  .base-tag {
    color: var(--accent);
    background: color-mix(in srgb, var(--accent) 12%, transparent);
  }

  td.slip {
    color: var(--negative);
  }

  .empty {
    padding: var(--gap) 0;
    text-align: center;
  }

  .note {
    margin-top: var(--gap);
    line-height: 1.5;
  }
</style>

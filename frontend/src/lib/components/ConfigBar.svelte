<script>
  import { api, ApiError } from '../api.js'
  import { sanitizeConfig } from '../config.js'

  /** FR-018：配置的存 / 取 / 删。期 1 存 JSON 文件，期 6 换 PostgreSQL（A25）。 */
  let { config, onLoad } = $props()

  /**
   * 列表接口给的是 **对象**：`{name, modified_at}`，不是一串名字。
   *
   * 这里原先把整个对象当名字用，于是 `<option value={entry}>` 把对象
   * 转成了字符串 `"[object Object]"` —— 名字显示成那串鬼东西，选中后
   * 「载入」去请求 `/api/configs/[object Object]` 拿到 404，保存也被后端
   * 的名字校验挡下。表现就是「存了，但存不上」，而盘上的文件其实好好的。
   */
  let entries = $state([])
  let selected = $state('')
  let busy = $state(false)
  let error = $state(null)
  let notice = $state(null)

  async function refresh() {
    try {
      const data = await api.listConfigs()
      entries = data.configs ?? []
      // 选中的那份要是被别处删了，别继续举着一个不存在的名字
      if (selected && !entries.some((e) => e.name === selected)) selected = ''
    } catch (err) {
      error = err instanceof ApiError ? err.message : '取不到配置列表'
    }
  }

  /** 选中的那份存在什么时候。回答的是「我刚才到底存上没有」。 */
  const savedAt = $derived(entries.find((e) => e.name === selected)?.modified_at ?? null)

  $effect(() => {
    refresh()
  })

  function flash(message) {
    notice = message
    setTimeout(() => (notice = null), 2400)
  }

  /**
   * 后端给的是 UTC 的 ISO 串，显示成本机时间的一句话。
   * 纯粹是展示，和 money/percent 一样，不参与任何计算。
   */
  function when(iso) {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    const p = (n) => String(n).padStart(2, '0')
    return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`
  }

  async function save() {
    const name = (selected || prompt('配置名称') || '').trim()
    if (!name) return
    busy = true
    error = null
    try {
      // 与 /api/compute 用同一份整理逻辑：存下去的必须是能读回来的配置，
      // 否则草稿行会存成一个加载时就报错的坏文件
      await api.saveConfig(name, sanitizeConfig(config))
      selected = name
      await refresh()
      flash(`已保存「${name}」`)
    } catch (err) {
      error = err instanceof ApiError ? err.message : '保存失败'
    } finally {
      busy = false
    }
  }

  async function load() {
    if (!selected) return
    busy = true
    error = null
    try {
      onLoad(await api.loadConfig(selected))
      flash(`已载入「${selected}」`)
    } catch (err) {
      error = err instanceof ApiError ? err.message : '载入失败'
    } finally {
      busy = false
    }
  }

  async function remove() {
    if (!selected) return
    if (!confirm(`删除配置「${selected}」？此操作不可撤销。`)) return
    busy = true
    error = null
    try {
      await api.deleteConfig(selected)
      const gone = selected
      selected = ''
      await refresh()
      flash(`已删除「${gone}」`)
    } catch (err) {
      error = err instanceof ApiError ? err.message : '删除失败'
    } finally {
      busy = false
    }
  }
</script>

<div class="bar">
  <select bind:value={selected} disabled={busy} aria-label="选择配置">
    <option value="">未选择配置</option>
    {#each entries as entry (entry.name)}
      <option value={entry.name}>{entry.name}</option>
    {/each}
  </select>

  <button class="ghost" onclick={save} disabled={busy}>保存</button>
  <button class="ghost" onclick={load} disabled={busy || !selected}>载入</button>
  <button class="ghost danger" onclick={remove} disabled={busy || !selected}>删除</button>

  {#if savedAt}
    <span class="tiny saved-at" title="这份配置存到盘上的时间">
      存于 {when(savedAt)}
    </span>
  {/if}
  {#if notice}<span class="tiny notice">{notice}</span>{/if}
  {#if error}<span class="tiny err">{error}</span>{/if}
</div>

<style>
  .bar {
    display: flex;
    align-items: center;
    gap: var(--gap-xs);
  }

  select {
    width: auto;
    min-width: 150px;
    max-width: 220px;
    font-size: 13px;
    padding: 6px 10px;
  }

  .ghost {
    font-size: 13px;
    padding: 6px 12px;
    white-space: nowrap;
  }

  .ghost.danger:not(:disabled) {
    color: var(--negative);
  }

  .notice {
    color: var(--positive);
    white-space: nowrap;
  }

  .saved-at {
    color: var(--text-tertiary);
    white-space: nowrap;
  }

  .err {
    color: var(--negative);
    white-space: nowrap;
    max-width: 220px;
    overflow: hidden;
    text-overflow: ellipsis;
  }
</style>

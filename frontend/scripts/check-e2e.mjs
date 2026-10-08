/**
 * 真浏览器端到端自检。
 *
 * 为什么在 SSR 渲染自检之外还要这一层：`check:render` 只验证「首屏渲染没
 * 白屏」，它跑不了事件。而增长曲线的缩放/平移/点选、以及「输入标的自动抓取」
 * 这类交互，恰恰是构建通过、首屏也正常、却依然可能是坏的。写这个脚本时它
 * 立刻抓到了两个真 bug：浮层挡住了画布导致拖不动、以及滚轮监听被注册成
 * passive 导致缩放时整页跟着滚。
 *
 * 需要**两个服务都在跑**：
 *     uv run uvicorn backend.main:app --port 8000
 *     cd frontend && npm run dev
 * 然后 `npm run check:e2e`。
 *
 * 用系统已装的 Chrome（channel: 'chrome'），不下载额外浏览器。
 */

import { chromium } from 'playwright'

// 用 localhost：Vite 默认绑在 [::1]:5173（IPv6），写 127.0.0.1 会连不上。
// 名字叫 BASE 而不是 URL —— 模块顶层的 `const URL` 会遮蔽全局的 URL 构造函数，
// 于是下面 `new URL(...)` 抛 "URL is not a constructor"，还被前置检查的 catch
// 吞成「服务没起来」，白白查半天。
const BASE = process.env.E2E_URL ?? 'http://localhost:5173/'

const results = []
const ok = (name, pass, detail = '') =>
  results.push({ name, pass, detail: String(detail).replace(/\s+/g, ' ').slice(0, 120) })

// 跳过：前一步坏了，这一步无从谈起。记成通过但**注明跳过** —— 分母保持可比，
// 而真正坏掉的那一步自己会红。硬跑下去只会抛一句超时，把「其实是前一步坏的」
// 这个信息埋掉。
const skip = (name, why = '') => results.push({ name, pass: true, detail: `跳过（${why}）` })

function report() {
  let failed = 0
  for (const r of results) {
    if (!r.pass) failed++
    console.log(`${r.pass ? '✓' : '✗'} ${r.name}${r.detail ? '  「' + r.detail + '」' : ''}`)
  }
  console.log(`\n${results.length - failed}/${results.length} 通过`)
  return failed
}

// 断言中途抛错（元素没等到、超时）时也要把**已经跑完**的断言打出来。
// 否则看到的只有一句 TimeoutError，连前 20 条是过是败都不知道。
for (const ev of ['uncaughtException', 'unhandledRejection']) {
  process.on(ev, (err) => {
    ok('自检脚本跑到底', false, `${ev}: ${err?.message ?? err}`)
    console.log('')
    report()
    process.exit(1)
  })
}

// ── 前置检查：服务没起来就给一句人话，别抛一堆 fetch 报错 ────────────
try {
  const health = await fetch(new URL('/api/health', BASE))
  if (!health.ok) throw new Error(String(health.status))
} catch (err) {
  // 把真实原因带出来：上次因为这里吞掉了 "URL is not a constructor"，
  // 服务明明在跑却报了「连不上」，查了很久。
  console.error(`连不上 ${BASE} —— 请先启动后端（8000）与前端 dev server。`)
  console.error(`  原因：${err?.message ?? err}`)
  process.exit(2)
}

const browser = await chromium.launch({ channel: 'chrome' })
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } })

const consoleErrors = []
// 「Failed to load resource … 422」是**刻意**的：后端用非 2xx 表达校验失败，
// 界面接住并显示成横幅（重号标的、超配占比都是这样）。浏览器会把它记成
// console error，但那不是脚本出错。真要看的是 pageerror 和别的前端异常。
const isExpectedHttpNoise = (t) => /Failed to load resource/.test(t)
page.on('console', (m) => {
  if (m.type() === 'error' && !isExpectedHttpNoise(m.text())) consoleErrors.push(m.text())
})
page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message))

const bodyText = () => page.locator('body').innerText()
const bannerText = async () => (await page.locator('.banner.error').allInnerTexts()).join(' | ')
const planTotal = async () =>
  (await page.locator('.total').first().innerText()).replace(/\s+/g, ' ')
const planInputs = async () => {
  const out = []
  for (const el of await page.locator('.amounts input').all()) out.push(await el.inputValue())
  return out
}
const invested = async () => {
  // 「累计投入」统计卡里的金额
  const m = (await bodyText()).match(/累计投入\s*\n\s*(\$[\d,.]+[KMB]?)/)
  return m?.[1] ?? '?'
}

/** 等到计算都落定。 */
async function settle(ms = 2200) {
  await page.waitForTimeout(ms)
}

/**
 * 点「计算」并等它落定。
 *
 * 计算是**显式**的：在文本框里改参数不会自动重算 —— 否则用户填到一半，
 * 屏幕上的期末总值就一直变（「360」要经过 3、36、360 三次）。所以凡是要
 * 看结果的用例，改完文本框都得点一下这里。
 *
 * 下拉框、勾选框、按钮这类「一次成型、不存在填到一半」的控件不在此列，
 * 它们自己会算 —— ⑪ 专门盯着这条。
 */
async function compute(ms = 2600) {
  await page.getByRole('button', { name: '计算', exact: true }).click()
  await settle(ms)
}

await page.goto(BASE, { waitUntil: 'networkidle' })

// ══ 主流程 ═══════════════════════════════════════════════════════
ok('欢迎页出现', await page.getByText('未来收益规划器').isVisible())
await page.getByRole('button', { name: /美股/ }).click()
await page.waitForSelector('svg[role="img"]', { timeout: 10000 })
await settle(2500)
ok('选中市场后进入主界面并算出结果', (await bodyText()).includes('期末总值'))
ok('首屏无错误横幅', (await bannerText()) === '')

const baselineInvested = await invested()

// ══ ① 添加标的：草稿行不该把整个应用弄坏 ══════════════════════════
// 这是用户进入后最先做的一个动作。空代码的行只是草稿，不是标的。
await page.getByRole('button', { name: '＋ 添加标的' }).click()
await settle()
const blankBanner = await bannerText()
ok('新增空标的不会报错', blankBanner === '', blankBanner || '无错误')
ok('新增空标的不会清空已有结果', (await bodyText()).includes('期末总值'))

await page.getByRole('button', { name: '＋ 添加标的' }).click()
await settle()
ok('连续新增两个空标的也不报错', (await bannerText()) === '', await bannerText())

// 给其中一个填上代码，应当正常参与计算
const draftSym = page.locator('#sym-2')
await draftSym.fill('VOO')
await draftSym.blur()
await settle(3500)
await compute()
ok('给草稿行填上代码后正常计入', (await bannerText()) === '', await bannerText())

// 删掉刚加的，回到干净状态
await page.locator('.asset .icon').nth(2).click()
await settle()
await page.locator('.asset .icon').nth(2).click()
await settle()

// ══ ② 计划合计必须等于界面上各行的和 ═════════════════════════════
const t0 = await planTotal()
const i0 = await planInputs()
const sum0 = i0.reduce((a, b) => a + (Number(b) || 0), 0)
ok(
  '计划合计等于各行之和',
  t0.includes(sum0.toLocaleString('en-US')),
  `${t0} vs 各行 ${JSON.stringify(i0)}`,
)

// ══ ③ 改标的名：月投额要跟着走，钱不能悄悄少投 ════════════════════
const sym0 = page.locator('#sym-0')
const oldSymbol = await sym0.inputValue()
await sym0.fill('VOO')
await sym0.blur()
await settle(3500)
await compute()

const i1 = await planInputs()
const t1 = await planTotal()
ok(
  '改名后月投额跟着迁移到新代码',
  i1[0] === i0[0],
  `${oldSymbol}:${i0[0]} → VOO:${i1[0]}`,
)
ok(
  '改名后计划合计不变',
  t1.includes((i1.reduce((a, b) => a + (Number(b) || 0), 0)).toLocaleString('en-US')),
  `${t1} vs ${JSON.stringify(i1)}`,
)
const afterRename = await invested()
ok('改名后累计投入不变（没有静默少投）', afterRename === baselineInvested, `${baselineInvested} → ${afterRename}`)

// ══ ④ 删标的：计划里不该留下查不到价格的孤儿 ══════════════════════
await page.locator('.asset .icon').first().click()
await settle(2000)
const i2 = await planInputs()
const t2 = await planTotal()
const sum2 = i2.reduce((a, b) => a + (Number(b) || 0), 0)
ok(
  '删标的后计划合计仍等于各行之和',
  t2.includes(sum2.toLocaleString('en-US')),
  `${t2} vs ${JSON.stringify(i2)}`,
)
ok('删标的后无错误横幅', (await bannerText()) === '', await bannerText())

// ══ ⑤ 增长曲线交互 ═══════════════════════════════════════════════
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /美股/ }).click()
await page.waitForSelector('svg[role="img"]', { timeout: 10000 })
await settle(2500)

const svg = page.locator('svg[role="img"]')
const range = page.locator('.head-tools .range')
const tip = page.locator('.tip')
const zoomReset = page.locator('.head-tools button[title="回到全程视图"]')

ok('曲线已绘制', ((await svg.locator('path[stroke-width="2.5"]').getAttribute('d')) ?? '').length > 100)
ok('初始为全程', (await range.innerText()).includes('全程'), await range.innerText())

const box = await svg.boundingBox()
const cx = box.x + box.width * 0.5
const cy = box.y + box.height * 0.5

await page.mouse.move(cx, cy)
await page.mouse.wheel(0, -400)
await page.waitForTimeout(250)
ok('滚轮缩放改变了可视区间', !(await range.innerText()).includes('全程'), await range.innerText())
ok('滚轮缩放没有带动页面滚动', (await page.evaluate(() => window.scrollY)) === 0)

for (let i = 0; i < 20; i++) {
  await page.mouse.move(cx, cy)
  await page.mouse.wheel(0, -400)
  await page.waitForTimeout(60)
}
const ticks = await svg.locator('text.tick').allTextContents()
ok('深度放大后 X 轴切成按月刻度', ticks.some((t) => /第\d+月/.test(t)), ticks.slice(0, 6).join(' '))
ok('缩放后 Y 轴按可见区间重新定标', !ticks.some((t) => /M$/.test(t)), ticks.slice(0, 6).join(' '))

await page.mouse.click(box.x + box.width * 0.45, cy)
await page.waitForTimeout(200)
ok('单击弹出单月明细', await tip.isVisible())
const tipText = await tip.innerText()
ok('明细含组合总值与累计投入', /组合总值/.test(tipText) && /累计投入/.test(tipText), tipText)
const selectedLabel = tipText.split('\n')[0]

// 浮层是只读的，不该挡住画布：它盖住的地方仍然能拖动
const beforeDrag = await range.innerText()
await page.mouse.move(cx, cy)
await page.mouse.down()
await page.mouse.move(cx + 160, cy, { steps: 12 })
await page.mouse.up()
await page.waitForTimeout(250)
ok('拖动平移改变了区间', (await range.innerText()) !== beforeDrag, `${beforeDrag} → ${await range.innerText()}`)
const stillVisible = await tip.isVisible()
ok(
  '拖动不会误触改选中月份',
  !stillVisible || (await tip.innerText()).split('\n')[0] === selectedLabel,
  stillVisible ? '浮层仍在' : '浮层随视野滚出而隐藏',
)

await zoomReset.click()
await page.waitForTimeout(200)
ok('回到全程', (await range.innerText()).includes('全程'), await range.innerText())
ok('回到全程后明细关闭', !(await tip.isVisible()))

await page.locator('.head-tools button[title="放大"]').click()
await page.waitForTimeout(200)
ok('放大按钮生效', !(await range.innerText()).includes('全程'), await range.innerText())
await zoomReset.click()

// ══ ⑥ FR-005 输入标的自动抓取参数 ════════════════════════════════
// 一律把断言锚在**具体某张标的卡**上。上一版读的是「第一个 .quote-row」，
// 而第 0 行本来就抓过 SCHD，于是测试以为自己在看第 1 行，其实一直在看第 0 行
// —— 后两条断言因此长期读错对象。
const card = (i) => page.locator('.asset').nth(i)
const quoteRow = (i) => card(i).locator('.quote-row')

// 等的是「不再是搜索中」而不是「已经出结果」—— 两个来源并行，快路径先落地时
// 慢路径还在跑，界面会补一句「Nasdaq 查询中…」。那已经是可断言的状态了，
// 死等慢路径只会让这几条用例随 Nasdaq 的脾气时绿时红。
async function waitForQuote(i) {
  await page.waitForFunction(
    (n) => {
      const el = document.querySelectorAll('.asset')[n]?.querySelector('.quote-row')
      return el && !el.innerText.includes('搜索中')
    },
    i,
    { timeout: 30000 },
  )
}

// 第 1 行默认是 NVDA，先改成 SCHD 会与第 0 行重号（后端拒绝、界面也只该留一列），
// 所以这次改成一个与默认组合不冲突的真实标的。
const symInput = page.locator('#sym-1')
await symInput.fill('VOO')
await symInput.blur()
await waitForQuote(1)
const qText = await quoteRow(1).innerText()
ok('输入标的后自动抓取到参数', /数据截至/.test(qText), qText)
ok('抓取后填入了当前股价', Number(await card(1).locator('#p-1').inputValue()) > 0)
ok('展示了数据截至日期', /数据截至 \d{4}-\d{2}-\d{2}/.test(qText), qText)
// 不再说「缓存命中」这种黑话，直接报数据是从哪儿来的
ok('直接展示数据来源而不是缓存状态', /来源 (stockanalysis|Nasdaq)/.test(qText), qText)
ok('不再出现「缓存命中」字样', !qText.includes('缓存命中'), qText)
ok(
  '清单里有的代码不提示「可能不存在」',
  !(await card(1).innerText()).includes('清单里没有'),
  await card(1).innerText(),
)
ok('抓取结果只影响自己那张卡', (await quoteRow(0).innerText()) !== qText, await quoteRow(0).innerText())

// 费用率输入框在「增长参数」折叠面板里，得先展开才看得到（A23 那条）。
await card(1).locator('button.ghost.small', { hasText: '增长参数' }).click()
await page.waitForTimeout(200)
const er = card(1).locator('#er-1')
ok(
  '抓取模式费用率被锁为 0',
  (await er.inputValue()) === '0.00' && (await er.isDisabled()),
  `值=${await er.inputValue()} 禁用=${await er.isDisabled()}`,
)

await quoteRow(1).locator('.chip', { hasText: '15 年' }).click()
await page.waitForTimeout(2500)
ok('回看窗口可切到 15 年', (await quoteRow(1).locator('.chip.active').innerText()).includes('15'))

// ── 「搜索中」必须真的出现过 ───────────────────────────────────────
// 不能填个新代码就去断言：缓存命中时响应只要十几毫秒，这句提示一闪而过，
// 用例会时绿时红。所以把请求**人为拖慢**，让这个状态稳定地停在那儿。
// 拖的是路由层，不是后端 —— 测的是界面有没有这个状态，不是网络有多快。
let slowing = true
await page.route('**/api/quote/**', async (route) => {
  if (slowing) await new Promise((r) => setTimeout(r, 1500))
  await route.continue()
})
await symInput.fill('VHT')
await symInput.blur()
await page.waitForTimeout(350)
const loadingText = await quoteRow(1).innerText()
ok('输入完成后先显示搜索中', loadingText.includes('搜索中'), loadingText)
slowing = false
await waitForQuote(1)
await page.unroute('**/api/quote/**')
ok('搜索结束后换成结果', !(await quoteRow(1).innerText()).includes('搜索中'))

// 无效标的要给出说明而不是崩溃
await symInput.fill('ZZZZ')
await symInput.blur()
await waitForQuote(1)
const badText = await quoteRow(1).innerText()
ok('无效标的给出说明而不是崩溃', /抓不到|手输|查不到/.test(badText), badText)
ok('无效标的没有留下错误横幅', (await bannerText()) === '', await bannerText())
// 代码清单里没有它 —— 提示归提示，但**不能拦住用户**，手输参数的通道要开着
ok(
  '清单里没有的代码额外提示可能拼错',
  (await card(1).innerText()).includes('清单里没有'),
  await card(1).innerText(),
)

// ══ ⑦ 重号标的：不该把整块计划面板搞崩 ════════════════════════════
// 手滑把两行填成同一个代码是常事。计划面板的月投列按代码做 key，
// 不去重就会抛 each_key_duplicate，整个面板白掉。后端仍然要吵出来。
//
// 这里的「吵」发生在**点计算的时候**：改了参数不重算之后，配置级的错误
// 由后端在 /compute 上拒（`_symbols_unique`），而不是每敲一下就报一次。
// 界面在这中间不是哑的 —— 「参数已改 · 待计算」会一直亮着，用户在拿到
// 新数字之前一定会点那一下，也就一定会撞上这句话。
await symInput.fill('SCHD')
await symInput.blur()
await waitForQuote(1)
ok('重号标的后计划面板仍在', await page.locator('.amounts input').first().isVisible())
await compute()
ok('重号标的给出明确提示', /重复/.test(await bannerText()), await bannerText())
ok('重号标的没有页面级崩溃', !consoleErrors.some((e) => e.includes('each_key_duplicate')))

// ══ ⑧ 一个来源没答上话 ≠ 出错 ═══════════════════════════════════
// Nasdaq 实测要几十秒、一半请求超时。它没回来的时候，屏幕上那份
// stockanalysis 的数据**是对的** —— 为它标红等于告诉用户「你手里这个数可疑」。
// 这里把 nasdaq 那条请求直接掐断（比等它真超时快得多，也不看它脸色），
// 看界面到底说什么。
await page.route('**/api/quote/**', async (route) =>
  route.request().url().includes('source=nasdaq') ? route.abort() : route.continue(),
)
await symInput.fill('VTI')
await symInput.blur()
await waitForQuote(1)
// 顺手算一次：上一节那个重号标的把错误横幅留在了屏幕上，而这一节要断言
// 「缺一个来源不报错」——横幅不清掉就分不清红的是哪一桩。
await compute()
await page.waitForTimeout(1500) // 等慢路径那条失败也落地
const partial = await quoteRow(1).innerText()
ok('一个来源挂了，另一个的数据照常显示', /来源 stockanalysis/.test(partial), partial)
ok('缺一个来源不说「失败」「错误」「不可达」', !/失败|错误|不可达/.test(partial), partial)
ok('缺一个来源时点名是谁没答上话', /Nasdaq 来源暂时无法获取/.test(partial), partial)
// 「不标红」得真的量颜色，不能只看文字 —— 拿一个 var(--negative) 的探针比。
const noteCheck = await page.evaluate(() => {
  const probe = document.createElement('span')
  probe.style.color = 'var(--negative)'
  document.body.appendChild(probe)
  const red = getComputedStyle(probe).color
  probe.remove()
  const row = document.querySelectorAll('.asset')[1].querySelector('.quote-row')
  const note = row.querySelector('.src-missing')
  return {
    red,
    note: note ? getComputedStyle(note).color : null,
    errNodes: row.querySelectorAll('.err-text').length,
  }
})
ok(
  '备注是灰的，不是红的',
  noteCheck.note !== null && noteCheck.note !== noteCheck.red,
  `备注 ${noteCheck.note} / 红 ${noteCheck.red}`,
)
ok('缺一个来源时不出现错误提示块', noteCheck.errNodes === 0)
await page.unroute('**/api/quote/**')

// ── 数据源**自相矛盾**（bad_data）：同样是灰脚注，但话不一样 ────────
// 这既不是「连不上」也不是「你填错了代码」—— 是那个来源自己的两份响应打架：
// 历史末价 80243，而它自报的现价 51.55，相差 1556 倍。实盘上这条坏数据把
// VFLO 的期末总值从 $2.3M 顶成了 $61.7M（正好是用户截图里那个数）。
// 界面该说的是「这份报价已忽略」，而不是「网络不行，重试」—— 后者是假话，
// 会让人白折腾自己的网络。
await page.route('**/api/quote/**', async (route) => {
  if (!route.request().url().includes('source=nasdaq')) return route.continue()
  await route.fulfill({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify({
      symbol: 'JEPQ',
      available: false,
      failure: 'bad_data',
      known: true,
      reason: '数据源返回的历史价格（80,243.35）与它自己报的现价（51.55）相差过大，本次数据不采用。',
    }),
  })
})
// 用一个**这行还没抓过的**代码：抓过的代码会从上一轮的成功结果里继承
// 「来源 Nasdaq」，那时屏幕上是上次那份好数据，测的就不是这一条了。
await symInput.fill('JEPQ')
await symInput.blur()
await waitForQuote(1)
await page.waitForTimeout(1400) // 等慢路径那条也落地
const badData = await quoteRow(1).innerText()
ok('报价自相矛盾时点名说「异常，已忽略」', /Nasdaq 报价异常，已忽略/.test(badData), badData)
ok(
  '报价异常不说「无法获取」（那是另一回事）',
  !/Nasdaq 来源暂时无法获取/.test(badData),
  badData,
)
ok('报价异常仍然照常显示另一个来源的数据', /来源 stockanalysis/.test(badData), badData)
ok('报价异常不出现错误提示块', (await quoteRow(1).locator('.err-text').count()) === 0)
ok('报价异常不留下错误横幅', (await bannerText()) === '', await bannerText())
await page.unroute('**/api/quote/**')

// ══ ⑨ 配置的存 / 取 ════════════════════════════════════════════
// 列表接口给的是**对象** `{name, modified_at}`，不是一串名字。前端一度把整个
// 对象当名字用，于是 `<option value={entry}>` 把对象转成字符串 `"[object Object]"`：
// 名字显示成那串鬼东西，选中后「载入」去请求 `/api/configs/[object Object]` 拿 404，
// 保存也被后端的名字校验挡下。症状是「存了，但存不上」，而盘上的文件其实好好的。
// 这条走完整的存 → 改 → 取 → 删，自检自己清理，**不碰用户已有的配置**。
const TEST_CFG = '自检临时配置'
const cfgSel = page.locator('select[aria-label="选择配置"]')
const cfgOpts = () =>
  cfgSel.locator('option').evaluateAll((o) => o.map((x) => ({ t: x.textContent, v: x.value })))

page.once('dialog', (d) => d.accept(TEST_CFG)) // 保存时会问名称
const putDone = page
  .waitForResponse(
    (r) => /\/api\/configs\//.test(r.url()) && r.request().method() === 'PUT',
    { timeout: 15000 },
  )
  .catch(() => null)
await page.getByRole('button', { name: '保存' }).click()
await putDone
await page.waitForTimeout(500) // 紧随其后的列表刷新（GET）再一个来回

// 这里等的是「存盘这个来回结束了」，**不是「名字出现在下拉里」**。
// 后者一旦回归就只剩一句没头没脑的 10 秒超时，而我们要的是下面那句
// 「下拉里没有 [object Object]」自己报红，一眼看出坏在哪。
const savedOpts = await cfgOpts()
const listed = savedOpts.some((o) => o.t === TEST_CFG && o.v === TEST_CFG)
ok('保存后配置按**名字**出现在下拉里', listed, JSON.stringify(savedOpts))
ok(
  '下拉里没有 [object Object]',
  !savedOpts.some((o) => /\[object Object\]/.test(o.t + '|' + o.v)),
  JSON.stringify(savedOpts),
)
const savedAtText = await page.locator('.saved-at').innerText().catch(() => '')
ok(
  '选中配置后显示它存于什么时候',
  /存于 \d{4}-\d{2}-\d{2}/.test(savedAtText),
  savedAtText || '(没有这个元素)',
)

if (!listed) {
  // 存都没存上，后面的载入 / 删除无从谈起。硬跑只会抛超时，
  // 把「坏的其实是保存这一步」埋掉。
  for (const n of ['载入后占比被配置里的值覆盖回来', '载入给出了成功提示', '删掉后下拉里就没有它了'])
    skip(n, '保存没成功')
} else {
  // 把占比改掉，再载入 —— 配置里的值应该把它盖回来
  await card(0).locator('#w-0').fill('7')
  await cfgSel.selectOption(TEST_CFG)
  await page.getByRole('button', { name: '载入' }).click()
  await page.waitForTimeout(1200)
  const w0 = await card(0).locator('#w-0').inputValue()
  ok('载入后占比被配置里的值覆盖回来', w0 !== '7', `改动后载入得到 ${w0}`)
  const noticeText = await page.locator('.bar .notice').innerText().catch(() => '')
  ok('载入给出了成功提示', /已载入/.test(noticeText), noticeText || '(无提示)')

  page.once('dialog', (d) => d.accept()) // 删除时会确认
  await page.getByRole('button', { name: '删除' }).click()
  await page.waitForTimeout(900)
  const afterOpts = await cfgOpts()
  ok(
    '删掉后下拉里就没有它了',
    !afterOpts.some((o) => o.t === TEST_CFG),
    JSON.stringify(afterOpts),
  )
}

// ══ ⑩ 布局：量像素，不靠肉眼 ════════════════════════════════════
// 这里量的是踩过的坑：`.symbol` 的 `flex: 0 0 auto` 与 `.field` 的 `flex: 1`
// **权重相同**，只能靠先后顺序决胜 —— 它排在后面，于是那条规则一直是死的。
// 输入框照 7em 画成 105px，格子却被压成 60px，多出来的 45px 向右溢出去，
// 正好盖住「目标占比」那一格；点进去弹个焦点框，看着就像把旁边的东西盖了。
//
// CSS 输了权重不报错、不警告，`vite build` 照样成功，渲染自检也照样通过
// （它只测 SSR 出来的文字，不看盒子）。只有量像素才发现得了。
async function layoutSnapshot() {
  return page.evaluate(() => {
    const rows = [...document.querySelectorAll('.asset .row.main')]
    const spill = []
    const crossOverlap = []
    for (const row of rows) {
      const inputs = [...row.querySelectorAll('input')]
      for (const inp of inputs) {
        const cell = inp.closest('.field')
        const over = Math.round(
          inp.getBoundingClientRect().width - cell.getBoundingClientRect().width,
        )
        if (over > 1) spill.push(`${inp.id} 比自己的格子宽 ${over}px`)
      }
      for (let i = 0; i < inputs.length; i++) {
        for (let j = i + 1; j < inputs.length; j++) {
          const a = inputs[i].getBoundingClientRect()
          const b = inputs[j].getBoundingClientRect()
          if (Math.min(a.right, b.right) - Math.max(a.left, b.left) > 1) {
            crossOverlap.push(`${inputs[i].id} 压住了 ${inputs[j].id}`)
          }
        }
      }
    }
    return {
      spill,
      crossOverlap,
      // 代码框得放得下 5 位大写字母。scrollWidth 超出 clientWidth 就是被切掉了。
      // 这正是最初那版「SCHD 的 D 没显示全」。
      clipped: [...document.querySelectorAll('.asset .field.symbol input')]
        .filter((i) => i.scrollWidth > i.clientWidth)
        .map((i) => i.id),
      delW: Math.round(document.querySelector('.asset button.icon').getBoundingClientRect().width),
      // 统计条目各占一行就是换行了 —— 换行本身没错，错在这条信息本该一眼看完
      statLines: [...document.querySelectorAll('.asset .stats')].map(
        (s) => new Set([...s.children].map((c) => Math.round(c.getBoundingClientRect().y))).size,
      ),
    }
  })
}

for (const vp of [1440, 1100, 900]) {
  await page.setViewportSize({ width: vp, height: 1000 })
  await page.waitForTimeout(250)
  const L = await layoutSnapshot()
  const statLines = Math.max(...L.statLines)
  ok(
    `${vp}px：代码框不溢出自己的格子，也没压住旁边的输入框`,
    L.spill.length === 0 && L.crossOverlap.length === 0,
    [...L.spill, ...L.crossOverlap].join(' / ') || '无',
  )
  ok(`${vp}px：5 位代码在框里显示得下`, L.clipped.length === 0, L.clipped.join(' '))
  ok(`${vp}px：删除按钮按内容定宽，没被 flex:1 撑开`, L.delW < 60, `${L.delW}px`)
  ok(`${vp}px：初始市值/股价年增长/股息率 一行放得下`, statLines === 1, `占 ${statLines} 行`)
}
await page.setViewportSize({ width: 1440, height: 1000 })

// ══ ⑪ 计算由用户发起，不再「一动就重算」 ══════════════════════════
// 原先配置一变就重算（350ms 防抖）。于是用户填「360」，中间的 3、36 也在算，
// 期末总值啪啪啪地变三回 —— 填到一半就被打断，还看不出什么时候算完了。
// 现在：文本输入**不动**结果，一次成型的控件（勾选框、下拉、按钮）照旧立即算。

const summary = () => page.locator('.card', { has: page.locator('h2', { hasText: '期末总值' }) })
const bigNumber = async () =>
  (await summary().locator('.big').innerText().catch(() => '')).trim()
const staleBadge = async () => (await bodyText()).includes('参数已改 · 待计算')

const beforeType = await bigNumber()
// 改的是「当前股价」而不是占比：占比动了会牵动合计 100% 那条校验，
// 万一越界后端就 422，那测的就不是「会不会自动重算」了。
await card(0).locator('#p-0').fill('137')
await page.waitForTimeout(1500) // 比原来那个 350ms 防抖长得多
ok('文本框里改参数不重算，期末总值纹丝不动', (await bigNumber()) === beforeType, `${beforeType} → ${await bigNumber()}`)
ok('但要说明「参数已改 · 待计算」，不能装作没发生', await staleBadge())
ok('待计算时「计算」按钮亮起来', await summary().locator('.compute.dirty').isVisible())

await compute()
ok('点了计算，期末总值才变', (await bigNumber()) !== beforeType, `${beforeType} → ${await bigNumber()}`)
ok('算完「待计算」提示消失', !(await staleBadge()))
ok('算完按钮不再高亮', (await summary().locator('.compute.dirty').count()) === 0)

// 一次成型的控件：勾选框自己会算，不需要用户再点一下
const afterCompute = await bigNumber()
const rebalance = page.locator('.check input[type="checkbox"]').first()
ok('找得到再平衡勾选框', await rebalance.isVisible())
await rebalance.click()
await settle(2800)
ok('勾选框（一次成型）立即重算，不用再点计算', (await bigNumber()) !== afterCompute, `${afterCompute} → ${await bigNumber()}`)
ok('勾选框算完不留下「待计算」', !(await staleBadge()))

// ══ ⑫ 卡片折叠：标题留下，主体收干净 ══════════════════════════════
// 每张卡右上角一个小箭头。收起后标题必须**还在** —— 否则用户不知道
// 该点哪儿把它放回来。
const firstCard = page.locator('.card').first()
const bodyHeight = () =>
  firstCard.locator('.body-inner').evaluate((el) => el.getBoundingClientRect().height)
const expandedH = await bodyHeight()
ok('展开时卡片主体有高度', expandedH > 100, `${Math.round(expandedH)}px`)

const chevron = firstCard.locator('.chevron')
ok('每张卡右上角都有折叠箭头', (await page.locator('.card .chevron').count()) >= 11, `${await page.locator('.card .chevron').count()} 个`)
ok('箭头默认是展开态', (await chevron.getAttribute('aria-expanded')) === 'true')

await chevron.click()
await page.waitForTimeout(700) // 380ms 的动画跑完
ok('收起后主体高度归零', (await bodyHeight()) < 1, `${Math.round(await bodyHeight())}px`)
ok('收起后标题仍在（不然找不到地方展开回来）', await firstCard.locator('h2').isVisible())
ok('收起后箭头翻成收起态', (await chevron.getAttribute('aria-expanded')) === 'false')
ok('收起后里面那些输入框不再能被 Tab 选中', await firstCard.locator('.body-inner').evaluate((el) => getComputedStyle(el).visibility === 'hidden'))

// 「动效要细致」得真的量：高度是**渐变**的，不是一步跳过去的
await chevron.click()
await page.waitForTimeout(120)
const midH = await bodyHeight()
await page.waitForTimeout(700)
const backH = await bodyHeight()
ok('展开是个渐变过程，不是一步到位', midH > 1 && midH < backH - 1, `中途 ${Math.round(midH)}px → 最终 ${Math.round(backH)}px`)
ok('展开后回到原来的高度', Math.abs(backH - expandedH) < 2, `${Math.round(expandedH)} → ${Math.round(backH)}`)

// 折叠状态记在本地，刷新后还在 —— 否则每次刷新都要重新收一遍
const portfolio = page.locator('.card', { has: page.locator('h2', { hasText: '投资组合' }) })
await portfolio.locator('.chevron').click()
await page.waitForTimeout(700)
await page.reload({ waitUntil: 'networkidle' })
await page.getByRole('button', { name: /美股/ }).click()
await page.waitForSelector('svg[role="img"]', { timeout: 10000 })
await settle(2000)
ok(
  '刷新后折叠状态还在',
  (await portfolio.locator('.chevron').getAttribute('aria-expanded')) === 'false',
)
await portfolio.locator('.chevron').click()
await page.waitForTimeout(700)

// ══ ⑬ 股息增长率：测不了 ≠ 不增长 ═══════════════════════════════
// VFLO 2023 年才成立，派息记录 3.2 年，但**真正能拿去量增长的区间**只有
// 2.24 年 —— 月度派息要攒够 12 笔才开得了一个窗口，最早那个窗口只能落在
// 一年前。那 2.24 年正好是它的建仓爬坡期，年化出来 26.6%，拿去复利 30 年
// 就是拿噪音当趋势。
//
// 后端现在报 0 并带上 `dividend_growth_insufficient_history`。界面必须把
// **「测不了」和「测出来不涨」分开说** —— 两者数值一样、含义相反。混为
// 一谈，用户会以为这个标的的股息真的不增长，那是另一回事。
//
// 标志位用拦截响应来造，不等真实数据：真实标的的标志位会随数据源更新而
// 变化（VFLO 再攒一年就够 3 年了），用例就成了看别人脸色。
const quoteWith = (params) => ({
  symbol: 'TEST',
  available: true,
  params: {
    price_growth: 0.05,
    dividend_yield: 0.013,
    dividend_growth: 0,
    expense_ratio: 0.0,
    source: 'fetched',
    lookback_years: 10,
    history_years: 3.26,
    dividend_growth_span_years: 2.24,
    dividend_growth_insufficient_history: false,
    ...params,
  },
  name: 'Test Fund',
  asset_class: 'etf',
  last_price: 100,
  as_of: '2026-09-25',
  fetched_at: '2026-09-28T11:54:50+00:00',
  from_cache: true,
  source: 'stockanalysis',
  known: true,
  expense_ratio_info: null,
  lookback_years: 10,
  reason: null,
  failure: null,
})

// `symbol` 必须每次都不一样：输入框里已是同一个代码时，值没变，
// 浏览器就**不派发 change**，`onSymbolChange` 不跑，抓取根本没发生 ——
// 于是第二次读到的还是上一次的结果。这条踩过一次。
async function quoteNoteFor(symbol, params) {
  await page.route('**/api/quote/**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ ...quoteWith(params), symbol }),
    }),
  )
  await symInput.fill(symbol)
  await symInput.blur()
  await waitForQuote(1)
  // 说明在「增长参数」折叠面板里，收着的时候读不到 —— 先展开。
  // 上一节刚刷新过页面，折叠状态是收起的。
  const toggle = card(1).locator('button.ghost.small', { hasText: '增长参数' })
  if ((await toggle.innerText()).includes('▸')) {
    await toggle.click()
    await page.waitForTimeout(300)
  }
  await page.waitForTimeout(600)
  const cardText = await card(1).innerText()
  await page.unroute('**/api/quote/**')
  return cardText
}

const shortSpan = await quoteNoteFor('VFLX', {
  dividend_growth_insufficient_history: true,
})
ok('区间太短时说明「测不了」，而不是默不作声', /测不了/.test(shortSpan), shortSpan)
ok('说明里点出实测区间有多长（2.2 年）', /2\.2 年/.test(shortSpan), shortSpan)
ok('说明里说清这不是「不增长」', /不是「测出来不涨」/.test(shortSpan), shortSpan)
ok(
  '说明里给出路：可以自己填',
  /直接改这个框/.test(shortSpan),
  shortSpan,
)

// 量像素，不靠肉眼：一句新文案最容易出的两种毛病是**横向顶出卡片**和
// **染成红色**。前者只有量盒子才发现，后者只有比颜色才发现。
async function divNoteGeometry() {
  return page.evaluate(() => {
    const p = document.querySelectorAll('.asset')[1].querySelector('.div-note')
    if (!p) return null
    const card = p.closest('.card') ?? p.closest('.asset')
    const probe = document.createElement('span')
    probe.style.color = 'var(--negative)'
    document.body.appendChild(probe)
    const red = getComputedStyle(probe).color
    probe.remove()
    const line = parseFloat(getComputedStyle(p).lineHeight) || 17
    return {
      color: getComputedStyle(p).color,
      red,
      overflowsRight: Math.round(p.getBoundingClientRect().right - card.getBoundingClientRect().right),
      clipped: p.scrollWidth - p.clientWidth,
      lines: Math.round(p.getBoundingClientRect().height / line),
    }
  })
}

for (const vp of [1440, 900]) {
  await page.setViewportSize({ width: vp, height: 1000 })
  await page.waitForTimeout(250)
  const g = await divNoteGeometry()
  ok(`${vp}px：说明没顶出卡片，也没被切掉`, g !== null && g.overflowsRight <= 1 && g.clipped <= 1, JSON.stringify(g))
  // 和 .src-missing 同一个道理：它是说明，不是错误，所以**不能**用 --negative
  ok(`${vp}px：说明是灰的，不是红的`, g !== null && g.color !== g.red, g && `${g.color} / 红 ${g.red}`)
  ok(`${vp}px：说明换行后高度正常（不是 0 行也不是一行挤爆）`, g !== null && g.lines >= 2, g && `${g.lines} 行`)
}
await page.setViewportSize({ width: 1440, height: 1000 })

const longSpan = await quoteNoteFor('SCHX', {
  dividend_growth_insufficient_history: false,
})
ok('区间够长时不出现这句话', !/测不了/.test(longSpan), longSpan)

// ══ ⑭ 定投计划：持续时间按「年 + 个月」两个面额填 ══════════════════
//
// 年与月是**加数**，不是进位位 —— 唯一的真值仍是一个整数（总月数）。
// 所以「7 年 25 个月」照收（= 9 年 1 个月），当场把实际值标出来。
// 这块面板此前端到端零覆盖，顺手补齐。
const durY = (i = 0) => page.locator(`#dy-${i}`)
const durM = (i = 0) => page.locator(`#dm-${i}`)
const carryText = async () => {
  const note = page.locator('.segment .carry')
  return (await note.count()) ? (await note.first().innerText()).replace(/\s+/g, ' ') : ''
}
const planAxis = async () => (await page.locator('.axis').innerText()).replace(/\s+/g, ' ')

// head 行现在装的是「持续时间 + 定投频率」，两者都得给右上角的 ✕ 让路。
// 「第 X – Y 月」已经挪到下面的读数行，不再参与这里的挤压判断。
async function headGeometry(i = 0) {
  return page.evaluate((i) => {
    const seg = document.querySelectorAll('.segment')[i]
    if (!seg) return null
    const box = (el) => el.getBoundingClientRect()
    const dur = box(seg.querySelector('.duration'))
    const freq = box(seg.querySelector('.freq'))
    const close = box(seg.querySelector('.close'))
    const meta = box(seg.querySelector('.meta'))
    const segBox = box(seg)
    // 真正的「互压」是**两个方向上都相交**。只比 X 区间是不够的 ——
    // 上下两行的控件 X 区间本来就会重叠，那样比出来的红是假的。
    const overlap = (a, b) =>
      Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) > 0 &&
      Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top)) > 0
    const sameLine = (a, b) =>
      Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0
    return {
      durRight: Math.round(dur.right),
      freqLeft: Math.round(freq.left),
      freqRight: Math.round(freq.right),
      closeLeft: Math.round(close.left),
      durFreqOverlap: overlap(dur, freq),
      freqCloseOverlap: overlap(freq, close),
      // 「持续多久」和「多久投一次」是同一个问题的两半，得并排待着。
      // 谁把频率挪回自己一行，这条立刻红。
      durFreqSameLine: sameLine(dur, freq),
      closeTop: Math.round(close.top - segBox.top),
      closeRight: Math.round(segBox.right - close.right),
      closeOverflow: Math.round(close.right - segBox.right),
      // 读数行有没有被挤到折行（单行高 ≈ 19px，折了就翻倍）
      metaHeight: Math.round(meta.height),
      metaWidth: Math.round(meta.width),
    }
  }, i)
}

const investedValue = async () => {
  const raw = await invested()
  const mult = raw.includes('K') ? 1e3 : raw.includes('M') ? 1e6 : 1
  return parseFloat(raw.replace(/[$,]/g, '')) * mult
}

ok(
  '定投计划默认只有一个阶段',
  (await page.locator('.segment').count()) === 1,
  `${await page.locator('.segment').count()} 个`,
)

// —— 只填年：7 年 ——
await durY().fill('7')
await durM().fill('0')
await settle(300)
ok('只填 7 年 → 定投覆盖 84 个月', (await planAxis()).includes('84'), await planAxis())
ok('规范写法不出现标注', (await carryText()) === '', await carryText())

// —— 只填月：144 个月。逐字打，**不能用 fill()** ——
// fill 是一次性写入，正好绕过了「每敲一个数字就被规范化重写，
// 三个数字根本打不完」这个真问题。要测的就是逐字输入。
await durY().fill('0')
await durM().fill('')
await durM().pressSequentially('144', { delay: 40 })
await settle(300)
ok('月份框里逐字打「144」不会被打断', (await durM().inputValue()) === '144', await durM().inputValue())
const note144 = await carryText()
ok('144 个月当场标出实际为 12 年', /12 年/.test(note144) && /144 个月/.test(note144), note144)

// —— 组合：7 年 + 25 个月 ——
await durY().fill('7')
await durM().fill('')
await durM().pressSequentially('25', { delay: 40 })
await settle(300)
const note109 = await carryText()
ok(
  '7 年 25 个月当场标出实际为 9 年 1 个月',
  /9 年 1 个月/.test(note109) && /109 个月/.test(note109),
  note109,
)
ok('未进位的写法照样算进时间轴', (await planAxis()).includes('109'), await planAxis())

// —— 失焦归位：屏幕上的样子要和存盘的样子一致 ——
// 不归位的话，界面显示 7/25 而存盘是 109，重新载入会变成 9/1，
// 用户看到自己的数「自己变了」。
await durM().blur()
await settle(300)
const backY = await durY().inputValue()
const backM = await durM().inputValue()
ok('失焦后归位成 9 年 1 个月', backY === '9' && backM === '1', `实际 ${backY} 年 ${backM} 个月`)
ok('归位后标注自动消失', (await carryText()) === '', await carryText())

// —— 两个框都清零：给句人话，别让用户吃一句英文校验错 ——
await durY().fill('0')
await durM().fill('0')
await settle(300)
ok('持续时间为 0 时给出中文提示', /至少填 1 个月/.test(await carryText()), await carryText())

// —— 持续时间真的喂给了引擎 ——
// 「累计投入」只由「投了几期 × 每期多少」决定，与涨跌无关，所以期数翻倍
// 就该正好翻倍。这条同时证明了新控件没把 months 传丢、传错或传成 0。
async function investedAt(years, months) {
  await durY().fill(String(years))
  await durM().fill(String(months))
  await durM().blur()
  await compute()
  return investedValue()
}

const inv5 = await investedAt(5, 0)
const inv10 = await investedAt(10, 0)
const ratio = inv10 / inv5
ok(
  '期限翻倍，累计投入正好翻倍（证明持续时间喂到了引擎）',
  Math.abs(ratio - 2) < 0.02,
  `${inv5} → ${inv10}，比值 ${ratio.toFixed(3)}`,
)

// —— 像素：head 行里的三个东西互不侵犯，✕ 待在右上角，读数行不折行 ——
// `metaHeight <= 26` 是「单行」的判据：实测单行 19px，折一行就是 38px。
const META_ONE_LINE_MAX = 26
for (const vp of [1440, 1100, 900]) {
  await page.setViewportSize({ width: vp, height: 1000 })
  await page.waitForTimeout(250)
  const g = await headGeometry()
  ok(`${vp}px：持续时间与定投频率不互压`, g !== null && !g.durFreqOverlap, JSON.stringify(g))
  ok(`${vp}px：定投频率没钻到 ✕ 底下`, g !== null && !g.freqCloseOverlap, JSON.stringify(g))
  // 「持续多久」和「多久投一次」是同一个问题的两半 —— 频率原先被顶到
  // 另一行右侧，左边空出 124px，label 悬着单独成行，看着就是个飘着的标签。
  ok(`${vp}px：定投频率与持续时间并排`, g !== null && g.durFreqSameLine, JSON.stringify(g))
  // 这一条正是用户报的那个 bug：✕ 原先挂在 head 行行尾、距框顶 44px，
  // 纵向中心和「持续时间」输入框只差 6px，于是被读成「清空这一格」。
  // 判据就取「离右上角有多近」，位置一挪回去立刻红。
  ok(
    `${vp}px：✕ 贴在阶段框右上角`,
    g !== null && g.closeTop <= 12 && g.closeRight <= 12,
    g && `距顶 ${g.closeTop}px，距右 ${g.closeRight}px`,
  )
  ok(`${vp}px：✕ 没被顶出阶段框`, g !== null && g.closeOverflow <= 0, g && `${g.closeOverflow}px`)
  ok(
    `${vp}px：读数行没被挤到折行`,
    g !== null && g.metaHeight <= META_ONE_LINE_MAX,
    g && `高 ${g.metaHeight}px（单行约 19px），宽 ${g.metaWidth}px`,
  )
}
await page.setViewportSize({ width: 1440, height: 1000 })

// ══ ⑮ 定投计划：定投频率（默认每月） ══════════════════════════════
//
// 频率是「每 N 个月投一笔**整的**」，不是把一笔摊到中间月份上 ——
// 摊薄会把按季买入抹成连续注资，和事实不符。上面那条「期限翻倍 →
// 累计投入翻倍」的用例已经在 N=1 下守着引擎，这里守 N>1 的现金流形状。
const freqBox = (i = 0) => page.locator(`#sf-${i}`)
// 措辞断言不认符号名 —— 跑到这里时标的已经被前面几节改名过了
const firstAmountLabel = async () =>
  (await page.locator('.segment .amounts label').first().innerText()).trim()
const totalLine = async () => (await page.locator('.segment .total').first().innerText()).replace(/\s+/g, ' ')
const amountBoxes = page.locator('.segment .amounts input')

ok('定投频率默认每月', (await freqBox().inputValue()) === '1', await freqBox().inputValue())
ok('N=1 时措辞说「每月投入」', /每月投入额/.test(await firstAmountLabel()), await firstAmountLabel())

// 把所有金额框清零，只留第一格 300 —— 让下面的算术不依赖默认值，
// 也不依赖当前有哪几只标的（前面几节改过名字）
for (let k = 0; k < (await amountBoxes.count()); k++) {
  await amountBoxes.nth(k).fill('0')
  await amountBoxes.nth(k).blur()
}
await amountBoxes.first().fill('300')
await amountBoxes.first().blur()
await settle(200)

// —— 改成每 3 个月一次 ——
await freqBox().fill('3')
await freqBox().blur()
await settle(300)
// 注意别用 /月投/ 去否：**「每 3 个月投入额」本身就含「月投」两个字**
ok('改频率后措辞跟着变，说的是「每 3 个月」', /每 3 个月投入额/.test(await firstAmountLabel()), await firstAmountLabel())
ok('合计那行也说清是「每 3 个月」', /每 3 个月投入合计/.test(await totalLine()), await totalLine())

// 单笔金额不因频率而变 —— 变的是**多久投一次**，不是每笔投多少
ok('单笔金额不随频率缩放', (await amountBoxes.first().inputValue()) === '300', await amountBoxes.first().inputValue())

// —— 真正的验收：引擎收到的是断续的一笔整的 ——
// 每 3 个月一笔 300、投 5 年 0 个月 → 第 1/4/…/58 月共 20 笔 × 300 = 6,000。
// 若被摊薄成按月注资，这里会是 18,000 —— 那正是这条要挡住的错法。
await durY().fill('5')
await durM().fill('0')
await durM().blur()
await compute()
const invQuarterly = await investedValue()
ok(
  '每 3 个月投 300、投 5 年 → 累计投入 6,000（摊薄的话会是 18,000）',
  Math.abs(invQuarterly - 6000) < 1,
  `${invQuarterly}`,
)

// 同金额改回每月 → 60 笔 × 300 = 18,000，正好是 3 倍
await freqBox().fill('1')
await freqBox().blur()
await compute()
const invMonthly = await investedValue()
ok(
  '频率改回每月 → 累计投入正好 3 倍',
  Math.abs(invMonthly / invQuarterly - 3) < 0.01,
  `${invQuarterly} → ${invMonthly}`,
)

// —— 清空频率框重打，不该变成「13」——
await freqBox().fill('')
await freqBox().pressSequentially('12', { delay: 40 })
await settle(200)
ok('频率框逐字打「12」不会被打断', (await freqBox().inputValue()) === '12', await freqBox().inputValue())
await freqBox().blur()
await settle(200)

// —— 像素：频率框和「填法」并排，不能互压、不能顶出卡片 ——
async function controlsGeometry(i = 0) {
  return page.evaluate((i) => {
    const seg = document.querySelectorAll('.segment')[i]
    if (!seg) return null
    const box = (el) => el.getBoundingClientRect()
    const mode = box(seg.querySelector('.mode'))
    const freq = box(seg.querySelector('.freq'))
    const cr = box(seg.closest('.card'))

    // 标签有没有被挤到换行：拿同一段文字、同样样式、但强制不换行量一次
    // 做基准，比实际高度高就说明折了行。措辞从「月投额」变成「每 3 个月
    // 投入额」之后变长了，窄屏上是有可能折的 —— 折了不报错，只是难看。
    const lbl = seg.querySelector('.amounts label')
    const probe = lbl.cloneNode(true)
    probe.style.position = 'absolute'
    probe.style.whiteSpace = 'nowrap'
    probe.style.width = 'auto'
    document.body.appendChild(probe)
    const oneLine = probe.getBoundingClientRect().height
    probe.remove()

    return {
      modeRight: Math.round(mode.right),
      freqLeft: Math.round(freq.left),
      // 「同一行」不能比 top：`.controls` 是 align-items: flex-end，频率框
      // 头上多一行 label，两者顶边本来就差一截。要比的是垂直区间相不相交。
      sameLine: mode.bottom > freq.top && freq.bottom > mode.top,
      freqRight: Math.round(freq.right),
      cardRight: Math.round(cr.right),
      freqBottom: Math.round(freq.bottom),
      cardBottom: Math.round(cr.bottom),
      labelWrapped: lbl.getBoundingClientRect().height > oneLine + 2,
    }
  }, i)
}

for (const vp of [1440, 900]) {
  await page.setViewportSize({ width: vp, height: 1000 })
  await page.waitForTimeout(250)
  const c = await controlsGeometry()
  ok(
    `${vp}px：频率框没压住「填法」切换`,
    c !== null && (!c.sameLine || c.modeRight <= c.freqLeft),
    JSON.stringify(c),
  )
  ok(
    `${vp}px：频率框在卡片内，没顶出右边也没顶出底部`,
    c !== null && c.freqRight <= c.cardRight && c.freqBottom <= c.cardBottom,
    c && `右溢出 ${c.freqRight - c.cardRight}px 下溢出 ${c.freqBottom - c.cardBottom}px`,
  )
  ok(`${vp}px：金额框的标签没被挤到折行`, c !== null && !c.labelWrapped, JSON.stringify(c))
}
await page.setViewportSize({ width: 1440, height: 1000 })

// ══ ⑯ 输入区不留「填什么都一样」的废话 ════════════════════════════
//
// 这里曾经无条件写着「第 N 个月起不再定投 —— 之后只靠复利增长。这正是
// Coast FIRE 的形状。」—— 只要定投没覆盖满计算年限就出现，等于填什么都
// 显示同一句。真正的 Coast 结论在 FIRE 模块里按数算（FR-013），
// 这里复读一遍不带任何信息，只会占地方。
// 锚点必须是**整张卡片**。一开始写的是 `.segment` 的父节点，那是 `.segments`
// 容器 —— 而时间轴那一段是它的**兄弟**，根本不在里面，于是这两条断言
// 一直在看一个永远不含这句话的元素，恒绿。改成按「含持续时间输入框的卡片」
// 定位，才真的把它框进来。
const planCard = page.locator('.card').filter({ has: page.locator('#dy-0') })
const planCardText = (await planCard.innerText()).replace(/\s+/g, ' ')
ok('不再出现无条件的 Coast 复读', !/Coast FIRE 的形状/.test(planCardText), planCardText.slice(0, 60))
ok('也不再出现「之后只靠复利增长」', !/不再定投/.test(planCardText))

// 删掉那段话之后，盒子要跟着缩 —— 这里量的是**有没有留下空档**。
// 判据不能是「小于某个数」：那证明不了什么。要的是轴的最后一行到卡片
// 内边界的距离，**恰好等于卡片自身的下内边距** —— 多出来的那几像素就是
// 删剩的空壳。两处都要量：外壳自己有没有下内边距，以及总账对不对得上。
async function timelineWrapGeometry() {
  return page.evaluate(() => {
    const wrap = document.querySelector('.timeline-wrap')
    if (!wrap) return null
    const card = wrap.closest('.card')
    const axis = wrap.querySelector('.axis')
    return {
      wrapPadBottom: Math.round(parseFloat(getComputedStyle(wrap).paddingBottom)),
      cardPadBottom: Math.round(parseFloat(getComputedStyle(card).paddingBottom)),
      tailGap: Math.round(card.getBoundingClientRect().bottom - axis.getBoundingClientRect().bottom),
    }
  })
}

const tw = await timelineWrapGeometry()
ok('时间轴外壳自己没有残留的下内边距', tw !== null && tw.wrapPadBottom === 0, tw && `padding-bottom ${tw.wrapPadBottom}px`)
ok(
  '轴下面只剩卡片自身的内边距，没有删剩的空档',
  tw !== null && tw.tailGap === tw.cardPadBottom,
  tw && `轴后 ${tw.tailGap}px，卡片内边距 ${tw.cardPadBottom}px`,
)

// ══ ⑰ 输入框在休息态就得看得出来「这里能填」 ═══════════════════════
//
// 踩过的坑：app.css 里输入框是 `背景: --bg-sunken` + `边框: transparent`，
// 而 `.segment`（定投阶段卡）和 `.asset`（标的卡）的背景**也是** --bg-sunken。
// 实测两者都是 rgb(245,245,247)，边框 rgba(0,0,0,0) —— **差值为零**，
// 输入框在屏幕上真的不存在。用户的说法是「所有数字都融入背景里了」，
// 实际比"融入"更彻底：得先点一下才知道那里能填。
//
// 判据两条，缺一不可：
//   ① 边框一律可见 —— 这是唯一在**所有**容器上都成立的手段
//      （白卡上输入框底色和白卡同为白色，只有边框能说话）；
//   ② 坐在灰面板上的输入框，底色必须和面板**不同** —— 否则「浮起来」
//      这层意思就没了，只剩一条线撑着。
// 修复前这两条都是红的，那正是它们存在的意义。
async function inputAffordance() {
  return page.evaluate(() => {
    const parse = (c) => (c.match(/[\d.]+/g) || []).map(Number)
    const opaque = (c) => {
      const n = parse(c)
      return n.length >= 3 && (n[3] === undefined || n[3] > 0.9)
    }
    const alphaOf = (c) => {
      const n = parse(c)
      return n.length === 4 ? n[3] : 1
    }
    // --bg-sunken 是个十六进制令牌，计算样式给的是 rgb() —— 换算一次再比，
    // 否则字符串永远不等，这条断言会变成恒真的摆设。
    const sunken = getComputedStyle(document.documentElement).getPropertyValue('--bg-sunken').trim()
    const probe = document.createElement('div')
    probe.style.color = sunken
    document.body.appendChild(probe)
    const sunkenRgb = getComputedStyle(probe).color
    probe.remove()

    const hostOf = (el) => {
      let h = el.parentElement
      while (h) {
        const bg = getComputedStyle(h).backgroundColor
        if (opaque(bg)) return bg
        h = h.parentElement
      }
      return null
    }

    const noBorder = []
    const flatOnPanel = []
    let total = 0
    // 只查**要打字进去**的控件。勾选框 / 单选框走 `appearance: auto`，
    // 由浏览器画那个原生方框（SettingsInput 的 `.check input` 就是靠
    // accent-color 染色的），它自带的chrome 已经说明了可点，不需要边框 ——
    // 把它算进来是拿错了尺子，会得到一条永远修不好的假红。
    const FIELDS = "input:not([type='checkbox']):not([type='radio']), select"
    for (const el of document.querySelectorAll(FIELDS)) {
      total++
      const cs = getComputedStyle(el)
      const name = el.id || el.getAttribute('aria-label') || el.className || el.tagName
      if (parseFloat(cs.borderTopWidth) < 1 || alphaOf(cs.borderTopColor) === 0) noBorder.push(name)
      if (hostOf(el) === sunkenRgb && cs.backgroundColor === sunkenRgb) flatOnPanel.push(name)
    }
    return { total, sunkenRgb, noBorder, flatOnPanel }
  })
}

const aff = await inputAffordance()
// 先确认扫到了东西 —— 选择器写错时循环为空，下面两条会**恒绿**，
// 这正是本项目栽过跟头的那类假绿。
ok(
  '扫到了全页的输入框（下面两条不是空转）',
  aff.total >= 20,
  `${aff.total} 个，灰面板色 ${aff.sunkenRgb}`,
)
ok(
  '没有边框透明的输入框 —— 不用点也知道能填',
  aff.noBorder.length === 0,
  aff.noBorder.join(' | ') || `全 ${aff.total} 个都可见`,
)
ok(
  '坐在灰面板上的输入框底色与面板不同（真的浮起来）',
  aff.flatOnPanel.length === 0,
  aff.flatOnPanel.join(' | ') || '无',
)

// 全页不许出现横向滚动 —— 全局改了输入框边框宽度，窄容器有可能被撑出去。
async function pageOverflowX() {
  return page.evaluate(() => ({
    x: Math.round(document.documentElement.scrollWidth - document.documentElement.clientWidth),
    widest: [...document.querySelectorAll('input, select')]
      .map((el) => Math.round(el.getBoundingClientRect().right))
      .reduce((a, b) => Math.max(a, b), 0),
    viewport: document.documentElement.clientWidth,
  }))
}

for (const vp of [1440, 1100, 900]) {
  await page.setViewportSize({ width: vp, height: 1000 })
  await page.waitForTimeout(250)
  const o = await pageOverflowX()
  ok(`${vp}px：全页没有横向溢出`, o.x <= 0, `溢出 ${o.x}px（最右的输入框到 ${o.widest}，视口 ${o.viewport}）`)
}
await page.setViewportSize({ width: 1440, height: 1000 })


// ══ 收尾 ═════════════════════════════════════════════════════════
ok('全程无控制台报错', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

await browser.close()
process.exit(report() ? 1 : 0)

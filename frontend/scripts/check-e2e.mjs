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

// ══ 收尾 ═════════════════════════════════════════════════════════
ok('全程无控制台报错', consoleErrors.length === 0, consoleErrors.slice(0, 3).join(' | '))

await browser.close()
process.exit(report() ? 1 : 0)

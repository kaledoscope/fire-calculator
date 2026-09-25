/**
 * 配置的一致性维护。
 *
 * 这里只做**结构整理**，不做任何数值计算（SRS §2.1）：把界面上的中间态
 * 收拢成一份自洽的、能被后端接受的配置。
 */

const keyOf = (symbol) => (symbol || '').trim().toUpperCase()

/**
 * 去掉还没成形的输入，返回一份可提交的配置。
 *
 * 为什么需要它：用户点了「＋ 添加标的」之后，那一行在填代码之前是
 * **草稿**，不是一个标的 —— 后端要求 `symbol` 至少一个字符，这是对的，
 * 没有代码的标的根本无从定价。但界面上一出现空行就整个报错，等于用户
 * 的第一步动作就把应用弄坏了。
 *
 * 所以在提交的边界上把草稿滤掉：界面照常显示，计算与保存则当它不存在。
 * 顺带清掉定投计划里指向已不存在标的的月投额 —— 那种条目会被引擎
 * **静默跳过**（`expand_schedule` 里查不到价格就不投），钱不知去向，
 * 还不如在源头就不让它出现。
 */
export function sanitizeConfig(config) {
  const assets = (config.assets ?? []).filter((a) => keyOf(a.symbol))
  const symbols = new Set(assets.map((a) => keyOf(a.symbol)))

  const segments = (config.plan?.segments ?? []).map((seg) => {
    if (!seg.monthly) return seg
    const monthly = {}
    for (const [symbol, amount] of Object.entries(seg.monthly)) {
      if (symbols.has(keyOf(symbol))) monthly[symbol] = amount
    }
    return { ...seg, monthly }
  })

  return {
    ...config,
    assets,
    plan: { ...config.plan, segments },
  }
}

/**
 * 标的改名时，把定投计划里挂在旧代码下的月投额挪到新代码下。
 *
 * **不这么做会静默少投钱。** 计划是按标的代码存的，把 SCHD 改成 VOO
 * （换了只基金，但计划里那一格还是要投的），那笔月投额就留在了 `SCHD`
 * 这个已经不存在的键上：界面那一行读新代码读不到值、显示 0，引擎查不到
 * 价格也跳过不投。结果就是用户按自己填的计划投了钱，算出来的却少一大块，
 * 而且看不出是改名弄丢的。
 */
export function renamePlanSymbol(config, from, to) {
  const oldKey = keyOf(from)
  const newKey = keyOf(to)
  if (!oldKey || !newKey || oldKey === newKey) return

  for (const seg of config.plan?.segments ?? []) {
    if (!seg.monthly || !(oldKey in seg.monthly)) continue
    const amount = seg.monthly[oldKey]
    delete seg.monthly[oldKey]
    // 新代码已经有金额就保留它 —— 用户已经填好的数不该被覆盖
    if (!(newKey in seg.monthly)) seg.monthly[newKey] = amount
  }
}

/**
 * 标的被删除时，一并清掉定投计划里属于它的月投额。
 *
 * 与 `sanitizeConfig` 里那段是同一个理由：留着就是一笔查不到价格、
 * 会被引擎静默跳过的钱；而且「计划合计」还会把它算进去，与界面显示的
 * 各行对不上。
 */
export function dropPlanSymbol(config, symbol) {
  const key = keyOf(symbol)
  if (!key) return

  for (const seg of config.plan?.segments ?? []) {
    if (seg.monthly && key in seg.monthly) delete seg.monthly[key]
  }
}

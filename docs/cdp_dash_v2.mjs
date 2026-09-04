/* v2 仪表盘验证: 量能卡 + 时间轴 + 板块展开 */
const CDP = 'http://127.0.0.1:9226'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const list = await (await fetch(`${CDP}/json/list`)).json()
const page = list.find((t) => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0; const pend = new Map(); const errors = []
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text || '')
  else if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push(m.params.entry.text)
})
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params }))
})
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true })
  return r && r.result && r.result.value
}
await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable')
await send('Page.navigate', { url: 'http://127.0.0.1:8720/#/' })
// wait overview loaded
for (let i = 0; i < 30; i++) {
  await sleep(2000)
  const t = (await ev('document.body.innerText')) || ''
  if (t.includes('量能') && t.includes('时间轴')) break
}
await sleep(2000)
console.log('errors=', errors.length, errors.slice(0, 2))
const checks = await ev(`(() => {
  const t = document.body.innerText
  return {
    hasVolCard: t.includes('量能') && (t.includes('放量') || t.includes('缩量') || t.includes('持平')),
    volSub: (t.match(/上证指数分时[^\n]*/) || [''])[0],
    hasTimeline: t.includes('情绪周期时间轴'),
    segLabels: (t.match(/(主跌|试错|主升|高位震荡)/g) || []).slice(0, 10),
    hasSecVolCol: t.includes('量能(较昨同时段)'),
    rows: document.querySelectorAll('.dash-sec-row').length,
  }
})()`)
console.log('checks=', JSON.stringify(checks, null, 1))
// 点击第一个板块行展开
const clicked = await ev(`(() => { const el = document.querySelector('.dash-sec-row'); if (el) { el.click(); return true } return false })()`)
await sleep(4000)
const exp = await ev(`(() => {
  const detail = document.querySelector('.sec-detail')
  if (!detail) return null
  const t = detail.innerText
  return {
    hasHead: t.includes('今日涨停'),
    roleChips: (t.match(/(板块龙头|首板领涨|首板跟风|中位跟风|同高卡位|板块高标)/g) || []).slice(0, 10),
    itemN: detail.querySelectorAll('.zt-item').length,
  }
})()`)
console.log('expand detail=', JSON.stringify(exp))
ws.close(); process.exit(0)

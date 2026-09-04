/* CDP 冒烟(实盘): 逐路由渲染读取文本并捕获异常 */
const CDP = 'http://127.0.0.1:9224'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

const list = await (await fetch(`${CDP}/json/list`)).json()
const page = list.find((t) => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })

let id = 0
const pend = new Map()
let errors = []
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text || '')
  else if (m.method === 'Log.entryAdded' && m.params.entry.level === 'error') errors.push(m.params.entry.text)
})
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params }))
})
const evalText = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true })
  return r && r.result && r.result.value
}
await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable')
await sleep(5000)

const routes = [
  { label: 'dashboard', hash: '#/' },
  { label: 'leaders', hash: '#/leaders' },
  { label: 'pools', hash: '#/pools' },
  { label: 'signals', hash: '#/signals' },
  { label: 'stock', hash: '#/stock?code=600519' },
  { label: 'backtest', hash: '#/backtest' },
]
for (const r of routes) {
  await send('Page.navigate', { url: `http://127.0.0.1:8720${r.hash}` })
  await sleep(9000)
  const text = await evalText('document.querySelector("#root") ? document.querySelector("#root").innerText.slice(0, 2600) : "(no root)"')
  console.log(`\n===== ${r.label} ===== errors=${errors.length}`)
  if (errors.length) { console.log('ERR>', errors.slice(0, 3).join(' | ').slice(0, 300)); errors = [] }
  console.log('TEXT>', String(text || '').replace(/\n+/g, ' | ').slice(0, 900))
}
ws.close(); process.exit(0)

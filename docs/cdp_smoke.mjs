/* CDP 冒烟：连上 headless Edge，切到各 hash 路由，读 #root 文本并收集异常 */
const CDP = 'http://127.0.0.1:9223'

async function getTarget(url) {
  const list = await (await fetch(`${CDP}/json/list`)).json()
  const page = list.find((t) => t.type === 'page')
  return page.webSocketDebuggerUrl
}

function cdp(ws) {
  let id = 0
  const pend = new Map()
  const events = []
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
    else if (m.method === 'Runtime.exceptionThrown' || m.method === 'Log.entryAdded') events.push(m)
  })
  const send = (method, params = {}) => new Promise((res) => {
    const i = ++id
    pend.set(i, (m) => res(m.result))
    ws.send(JSON.stringify({ id: i, method, params }))
  })
  return { send, events }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function evalText(conn, expression) {
  const r = await conn.send('Runtime.evaluate', { expression, returnByValue: true })
  return r && r.result && r.result.value
}

const routes = [
  { label: 'dashboard', hash: '#/' },
  { label: 'leaders', hash: '#/leaders' },
  { label: 'pools', hash: '#/pools' },
  { label: 'signals', hash: '#/signals' },
  { label: 'stock', hash: '#/stock?code=300908' },
  { label: 'backtest', hash: '#/backtest' },
]

const wsUrl = await getTarget()
const ws = new WebSocket(wsUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
const conn = cdp(ws)
await conn.send('Runtime.enable')
await conn.send('Log.enable')
await conn.send('Page.enable')

// 等首屏
await sleep(6000)

for (const r of routes) {
  await conn.send('Page.navigate', { url: `http://127.0.0.1:8720${r.hash}` })
  await sleep(7000) // 让每页数据+图表加载
  const text = await evalText(conn, 'document.querySelector("#root") ? document.querySelector("#root").innerText.slice(0, 3000) : "(no root)"')
  const errors = conn.events.filter((e) => e.method === 'Runtime.exceptionThrown' || (e.method === 'Log.entryAdded' && e.params.entry.level === 'error'))
  console.log(`\n===== ${r.label} (${r.hash}) =====`)
  console.log(`exceptions/errors: ${errors.length}`)
  if (errors.length) {
    for (const e of errors.slice(0, 3)) {
      const d = e.method === 'Runtime.exceptionThrown' ? (e.params.exceptionDetails?.text || '') : (e.params.entry.text || '')
      console.log('  ERR>', String(d).slice(0, 200))
    }
    conn.events.length = 0
  }
  console.log('TEXT>', String(text || '').replace(/\n+/g, ' | ').slice(0, 700))
}

ws.close()
process.exit(0)

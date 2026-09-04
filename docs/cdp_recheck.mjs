/* CDP 复检 leaders/stock 长加载页: 轮询等待文本出现 */
const CDP = 'http://127.0.0.1:9224'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const list = await (await fetch(`${CDP}/json/list`)).json()
const page = list.find((t) => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0; const pend = new Map(); let errors = []
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  else if (m.method === 'Runtime.exceptionThrown') errors.push(m.params.exceptionDetails?.text || '')
})
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params }))
})
const evalText = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true })
  return r && r.result && r.result.value
}
await send('Runtime.enable'); await send('Page.enable')

async function waitText(label, url, needles, timeoutMs = 40000) {
  await send('Page.navigate', { url })
  const t0 = Date.now()
  let text = ''
  while (Date.now() - t0 < timeoutMs) {
    await sleep(1500)
    text = (await evalText('document.querySelector("#root") ? document.querySelector("#root").innerText : ""')) || ''
    if (needles.some((n) => text.includes(n))) break
  }
  console.log(`[${label}] loaded=${needles.some((n) => text.includes(n))} errors=${errors.length} wait=${((Date.now() - t0) / 1000).toFixed(0)}s`)
  if (errors.length) { console.log('ERR>', errors.slice(0, 2).join(' | ').slice(0, 300)); errors = [] }
  console.log('TEXT>', text.replace(/\n+/g, ' | ').slice(0, 750))
}

await waitText('leaders', 'http://127.0.0.1:8720/#/leaders', ['六维评分', '板块龙头表', '市场总龙'])
await waitText('stock600519', 'http://127.0.0.1:8720/#/stock?code=600519', ['贵州茅台', '日K', '龙头战法'])
ws.close(); process.exit(0)

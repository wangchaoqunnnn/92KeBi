/* 最终验证: 相对地址资源加载 + 页面运行 */
const CDP = 'http://127.0.0.1:9225'
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
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true })
  return r && r.result && r.result.value
}
await send('Runtime.enable'); await send('Log.enable'); await send('Page.enable')
await sleep(8000)
for (const [label, hash] of [['dashboard', '#/'], ['stock', '#/stock?code=600519'], ['backtest', '#/backtest']]) {
  await send('Page.navigate', { url: `http://127.0.0.1:8720/${hash}` })
  await sleep(11000)
  const text = (await ev('document.querySelector("#root")?.innerText || ""')) || ''
  const scriptEl = await ev('document.querySelector("script[type=module]")?.getAttribute("src")')
  console.log(`[${label}] err=${errors.length} scriptSrc=${scriptEl} loaded=${text.length > 200}`)
  if (errors.length) { console.log('  ERR>', errors.slice(-2).join(' | ').slice(0, 240)); errors.length = 0 }
  console.log('  TEXT>', text.replace(/\n+/g, ' | ').slice(0, 420))
}
ws.close(); process.exit(0)

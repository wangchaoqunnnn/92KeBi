/* ops 页面渲染验证 */
const CDP = 'http://127.0.0.1:9229'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const list = await (await fetch(`${CDP}/json/list`)).json()
const page = list.find((t) => t.type === 'page')
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
let id = 0; const pend = new Map(); const errs = []
ws.addEventListener('message', (ev) => {
  const m = JSON.parse(ev.data)
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
  else if (m.method === 'Runtime.exceptionThrown') {
    const u = m.params.exceptionDetails?.exception?.description || ''
    if (!u.includes('sider') && !u.includes('chrome-extension')) errs.push(String(u).slice(0, 220))
  }
})
const send = (method, params = {}) => new Promise((res) => {
  const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params }))
})
const ev = async (expr) => {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true })
  return r.result && (r.result.value !== undefined ? r.result.value : 'EXC')
}
await send('Runtime.enable'); await send('Page.enable')
await sleep(12000)
const b = (await ev('document.body.innerText')) || ''
console.log('has title:', b.includes('打板操作台'))
console.log('sections:', ['买入池', '卖出池', '观察池', '最新提示'].map((s) => s + '=' + b.includes(s)).join(' '))
console.log('buy rows:', await ev('document.querySelectorAll("table").length'))
console.log('has buy names:', ['新 希 望', '平潭发展', '敦煌种业'].filter((n) => b.includes(n)).join(',') || 'none')
console.log('nav has 打板台:', b.includes('打板台'))
console.log('errs:', errs.length ? errs : 'none')
ws.close(); process.exit(0)

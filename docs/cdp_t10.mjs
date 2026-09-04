const CDP = 'http://127.0.0.1:9235'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function open(url) {
  const t = await (await fetch(`${CDP}/json/new?about:blank`, { method: 'PUT' })).json()
  const ws = new WebSocket(t.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0; const pend = new Map(); const errs = []
  ws.addEventListener('message', (ev) => { const m = JSON.parse(ev.data); if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) } else if (m.method === 'Runtime.exceptionThrown') errs.push((m.params.exceptionDetails?.exception?.description || '').slice(0,160)) })
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })) })
  const ev2 = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }); return r.result?.value }
  await send('Runtime.enable'); await send('Page.enable'); await send('Page.navigate', { url })
  return { ws, ev2, errs }
}
let c = await open('http://127.0.0.1:8720/#/')
for (let i = 0; i < 25; i++) { await sleep(2000); if (((await c.ev2('document.body.innerText')) || '').includes('涨幅前 10')) break }
const r1 = await c.ev2(`(() => ({
  head1: document.body.innerText.includes('板块强弱榜 · 涨幅前10 / 跌幅后10'),
  topN: [...document.querySelectorAll('.dash-sec-tbl tbody')].map(tb => tb.querySelectorAll('.dash-sec-row').length),
}))()`)
console.log('dash groups:', JSON.stringify(r1), 'errs', c.errs.length ? c.errs.slice(0,2) : 'none')
c.ws.close()
c = await open('http://127.0.0.1:8720/#/stock?code=600519')
for (let i = 0; i < 20; i++) { await sleep(2000); if (((await c.ev2('document.body.innerText')) || '').includes('近1年(滚动365日)全部')) break }
const b2 = (await c.ev2('document.body.innerText')) || ''
console.log('stock block 1y tag:', b2.includes('近1年(滚动365日)全部'), '| row count text:', (b2.match(/共 (\d+) 个交易日记录/) || [])[1] || '?')
c.ws.close(); process.exit(0)

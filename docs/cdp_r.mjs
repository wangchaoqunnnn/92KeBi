/* 验证: 个股资金卡 / 仪表盘绝对量能 / 打板台时段标记 */
const CDP = 'http://127.0.0.1:9232'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
async function open(url) {
  const t = await (await fetch(`${CDP}/json/new?${encodeURIComponent(url)}`, { method: 'PUT' })).json()
  const ws = new WebSocket(t.webSocketDebuggerUrl)
  await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej })
  let id = 0; const pend = new Map(); const errs = []
  ws.addEventListener('message', (ev) => {
    const m = JSON.parse(ev.data)
    if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id) }
    else if (m.method === 'Runtime.exceptionThrown') errs.push((m.params.exceptionDetails?.exception?.description || '').slice(0, 200))
  })
  const send = (method, params = {}) => new Promise((res) => { const i = ++id; pend.set(i, (m) => res(m.result)); ws.send(JSON.stringify({ id: i, method, params })) })
  const ev2 = async (expr) => { const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true }); return r.result?.value }
  await send('Runtime.enable'); await send('Page.enable')
  await sleep(9000)
  const body = (await ev2('document.body.innerText')) || ''
  const out = { url: await ev2('location.href'), errs: errs.length }
  return { ws, body, out, ev2 }
}

// 个股 600519 资金卡
let c = await open('http://127.0.0.1:8720/#/stock?code=600519')
console.log('STOCK errs', c.out.errs)
console.log('资金卡标题:', c.body.includes('当日资金 · 量能 & 净流入'))
console.log('放量文本:', (c.body.match(/全日折算为近5日均的 [^→]* → [^ \n]*/) || [])[0] || 'none')
console.log('暗盘:', c.body.includes('暗盘净流入'))
console.log('主力:', c.body.includes('主力净流入'))
c.ws.close()

// 仪表盘 量能(绝对)
c = await open('http://127.0.0.1:8720/#/')
console.log('DASH 今/昨同期文本:', (c.body.match(/今 \d+亿[^·]*昨同期 \d+亿/) || [])[0] || 'none')
const line = c.body.split('\n').find((l) => l.includes('量能 · 较昨日同时段'))
console.log('DASH vol card around:', c.body.slice(Math.max(0, c.body.indexOf('量能 · 较昨日同时段')), c.body.indexOf('量能 · 较昨日同时段') + 200).replace(/\n+/g, ' | ').slice(0, 220))
c.ws.close()

// 打板台 时段
c = await open('http://127.0.0.1:8720/#/ops')
console.log('OPS 非交易时段标记:', c.body.includes('非交易时段(买卖暂停'))
c.ws.close()
process.exit(0)

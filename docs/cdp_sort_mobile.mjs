/* 验证: 时间轴图表 + 表头排序 + 移动端溢出 */
const CDP = 'http://127.0.0.1:9227'
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
    if (!u.includes('sider.ai')) errs.push(String(u).slice(0, 160))
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
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
await send('Page.navigate', { url: 'http://127.0.0.1:8720/#/' })
await sleep(15000)
const d = await ev(`(() => {
  const b = document.body.innerText
  return {
    timelineTitle: b.includes('情绪周期时间轴'),
    canvasN: document.querySelectorAll('#root canvas').length,
    sortHeads: document.querySelectorAll('.dash-sec-tbl .sortable').length,
    firstSectorBefore: document.querySelector('.dash-sec-row td')?.innerText || 'none',
    overflow: document.documentElement.scrollWidth - window.innerWidth,
  }
})()`)
console.log('DASH(mobile390):', JSON.stringify(d), 'errs', errs.length ? errs : 'none')
// 点击“今涨停”表头排序
await ev(`(() => { const th = [...document.querySelectorAll('.dash-sec-tbl th')].find(x => x.innerText.includes('今涨停')); if (th) th.click(); return true })()`)
await sleep(1200)
const after = await ev(`(() => { const t = [...document.querySelectorAll('.dash-sec-row')].map(r => r.querySelector('td:nth-child(3)')?.innerText).slice(0, 5); return t })()`)
console.log('sorted by 今涨停 first5:', JSON.stringify(after))
for (const [label, hash] of [['leaders', '#/leaders'], ['signals', '#/signals'], ['pools', '#/pools']]) {
  await send('Page.navigate', { url: `http://127.0.0.1:8720/${hash}` })
  await sleep(9000)
  const r = await ev(`(() => {
    const sortables = [...document.querySelectorAll('table thead th.sortable')].map(t => t.innerText.replace(/\\s/g,' ').slice(0,24))
    return { sortables, bodyErr: !!document.querySelector('.error-box') }
  })()`)
  console.log(label, 'sortables:', JSON.stringify(r))
}
ws.close(); process.exit(0)

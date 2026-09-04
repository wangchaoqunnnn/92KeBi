/* 最终验证: 时间轴图/表头排序/移动端 */
const CDP = 'http://127.0.0.1:9228'
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
    if (!u.includes('sider.ai') && !u.includes('chrome-extension')) errs.push(String(u).slice(0, 220))
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
await send('Page.navigate', { url: 'http://127.0.0.1:8720/#/' })
for (let i = 0; i < 20; i++) {
  await sleep(1500)
  const b = (await ev('document.body.innerText')) || ''
  if (b.includes('量能') && b.includes('情绪周期时间轴')) break
}
const d = await ev(`(() => {
  const b = document.body.innerText
  return {
    timeline: b.includes('情绪周期时间轴'),
    canvasN: document.querySelectorAll('#root canvas').length,
    sortTh: document.querySelectorAll('.dash-sec-tbl .sortable').length,
    volCard: b.includes('量能 · 较昨日同时段'),
    firstSec: document.querySelector('.dash-sec-row')?.innerText?.split('\\n')[0] || 'none',
    err: !!document.querySelector('.error-box'),
  }
})()`)
console.log('DASH:', JSON.stringify(d))
// 点“今涨停”排序
const before = await ev(`(() => [...document.querySelectorAll('.dash-sec-row')].slice(0,3).map(r => r.querySelector('td:nth-child(3)')?.innerText))()`)
await ev(`(() => { const th=[...document.querySelectorAll('.dash-sec-tbl th')].find(t=>t.innerText.includes('今涨停')); if(th) th.click(); return true })()`)
await sleep(1000)
const afterSort = await ev(`(() => [...document.querySelectorAll('.dash-sec-row')].slice(0,5).map(r => r.querySelector('td:nth-child(3)')?.innerText))()`)
console.log('sort 今涨停 before:', JSON.stringify(before), 'after:', JSON.stringify(afterSort))
// 展开第一个板块
await ev(`(() => { const el=document.querySelector('.dash-sec-row'); if(el) el.click(); return true })()`)
await sleep(2500)
const exp = await ev(`(() => { const t=document.querySelector('.sec-detail')?.innerText||''; return { open: t.includes('今日涨停'), roles:(t.match(/(板块龙头|首板|跟风|高标)/g)||[]).slice(0,6) } })()`)
console.log('expand:', JSON.stringify(exp))
// 移动端视口溢出
await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 2, mobile: true })
await sleep(1500)
const mo = await ev(`({ docOverflow: document.documentElement.scrollWidth - window.innerWidth, bodyOverflow: document.body.scrollWidth - window.innerWidth })`)
console.log('mobile390 overflow px:', JSON.stringify(mo))
// 其余页面表头
for (const [lbl, h] of [['leaders', '#/leaders'], ['signals', '#/signals'], ['backtest', '#/backtest']]) {
  await send('Page.navigate', { url: `http://127.0.0.1:8720/${h}` })
  await sleep(8000)
  const s = await ev(`(() => [...document.querySelectorAll('table thead th.sortable')].map(t => t.innerText.replace(/\\s/g,' ').slice(0,18)))()`)
  console.log(lbl, 'sortable heads:', JSON.stringify(s))
}
console.log('page errs:', errs.length ? errs : 'none')
ws.close(); process.exit(0)

// 格式化与常量工具

export const PHASE_META = {
  main_decline: { label: '主跌阶段', color: '#ef4444', short: '主跌', order: 0 },
  probe: { label: '低位震荡/试错期', color: '#f59e0b', short: '试错', order: 1 },
  main_ascend: { label: '主升阶段', color: '#22c55e', short: '主升', order: 2 },
  high_oscillate: { label: '高位震荡', color: '#eab308', short: '高位震荡', order: 3 },
}
export const PHASE_KEYS = ['main_decline', 'probe', 'main_ascend', 'high_oscillate']

export const UP = '#f5444b'   // A股红涨
export const DOWN = '#0ecb81' // A股绿跌
export const FLAT = '#9aa8bf'

export function fmt(x, nd = 2) {
  if (x === null || x === undefined || Number.isNaN(Number(x))) return '—'
  return Number(x).toFixed(nd)
}

export function fmtPct(x, withSign = true) {
  const n = Number(x)
  if (!Number.isFinite(n)) return '—'
  const s = withSign && n > 0 ? '+' : ''
  return `${s}${n.toFixed(2)}%`
}

export function clsPct(x) {
  const n = Number(x)
  if (!Number.isFinite(n) || n === 0) return 'flat'
  return n > 0 ? 'up' : 'down'
}

export function fmtNum(x, nd = 0) {
  const n = Number(x)
  if (!Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 10000) return `${(n / 10000).toFixed(2)}万`
  if (Math.abs(n) >= 1000) return `${(n / 1000).toFixed(1)}千`
  return n.toFixed(nd)
}

export function fmtAmountYi(x) {
  const n = Number(x)
  if (!Number.isFinite(n)) return '—'
  return `${n.toFixed(1)}亿`
}

export function fmtDate(iso) {
  if (!iso) return '—'
  return String(iso).slice(0, 10)
}

export function dateStr(d = new Date()) {
  return d.toISOString().slice(0, 10)
}

export function recentDates(backDays, end = new Date()) {
  const out = []
  for (let i = backDays - 1; i >= 0; i--) {
    const d = new Date(end)
    d.setDate(d.getDate() - i)
    while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() - 1)
    out.push(d.toISOString().slice(0, 10))
  }
  return out
}

export const SIGNAL_DIR_META = {
  buy: { label: '买入', color: '#f5444b', cls: 'sig-buy' },
  sell: { label: '卖出', color: '#0ecb81', cls: 'sig-sell' },
  watch: { label: '观察', color: '#eab308', cls: 'sig-watch' },
}
export const STRENGTH_META = {
  强: { label: '强', cls: 'st-strong' },
  中: { label: '中', cls: 'st-mid' },
  警示: { label: '警示', cls: 'st-warn' },
}

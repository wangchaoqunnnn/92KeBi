// 统一的 API 访问层。全部使用相对地址(无主机/无根前缀)：
// BASE='.' → './api/...'，兼容部署在站点根路径或任意子路径(反向代理同前缀场景)。
// 开发模式下由 Vite 代理转发到后端 8720。
const BASE = '.'

async function req(path, opts = {}) {
  const url = path.startsWith('/') ? `.${path}` : `${BASE}/${path}`
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  })
  if (!res.ok) {
    let detail = ''
    try {
      const j = await res.json()
      detail = j.detail ? (typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail)) : ''
    } catch { /* ignore */ }
    throw new Error(`API ${res.status}: ${path} ${detail}`.trim())
  }
  return res.json()
}

export const api = {
  meta: () => req('/api/meta'),
  live: () => req('/api/market/live'),
  sectorZt: (sector) => req(`/api/market/sector-zt?sector=${encodeURIComponent(sector)}`),

  opsOverview: () => req('/api/ops/overview'),
  opsFlush: () => req('/api/ops/flush', { method: 'POST', body: '{}' }),
  opsIgnore: (pool, code) => req(`/api/ops/ignore?pool=${pool}&code=${code}`, { method: 'POST', body: '{}' }),
  opsManualSell: (code) => req(`/api/ops/manual-sell?code=${code}`, { method: 'POST', body: '{}' }),
  opsManualWatch: (code) => req(`/api/ops/manual-watch?code=${code}`, { method: 'POST', body: '{}' }),
  opsDelete: (itemId) => req(`/api/ops/delete?item_id=${itemId}`, { method: 'POST', body: '{}' }),

  overview: () => req('/api/dashboard/overview'),
  sectors: () => req('/api/dashboard/sectors'),
  history: (days = 40) => req(`/api/dashboard/history?days=${days}`),

  leaders: () => req('/api/leaders'),
  leadersHistory: (days = 40) => req(`/api/leaders/history?days=${days}`),

  pools: () => req('/api/pools'),
  signals: () => req('/api/signals'),

  search: (q) => req(`/api/search?q=${encodeURIComponent(q)}`),
  stock: (code) => req(`/api/stocks/${code}`),
  stockBlockTrades: (code, limit = 0) => req(`/api/stocks/${code}/block-trades?limit=${limit}`),

  backtestMeta: () => req('/api/backtest/meta'),
  runBacktest: (payload) => req('/api/backtest/run', { method: 'POST', body: JSON.stringify(payload) }),

  adminStatus: () => req('/api/admin/status'),
  adminRefresh: () => req('/api/admin/refresh', { method: 'POST', body: '{}' }),
  adminAdvance: (days = 1) => req('/api/admin/advance', { method: 'POST', body: JSON.stringify({ days }) }),
  probeSina: () => req('/api/admin/probe-sina', { method: 'POST', body: '{}' }),
}

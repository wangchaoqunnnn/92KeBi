import React, { useEffect, useMemo, useState, useCallback } from 'react'
import { api } from './api'
import { PHASE_META } from './format'
import DashboardPage from './pages/DashboardPage'
import LeadersPage from './pages/LeadersPage'
import PoolsPage from './pages/PoolsPage'
import SignalsPage from './pages/SignalsPage'
import StockPage from './pages/StockPage'
import BacktestPage from './pages/BacktestPage'
import { Loading } from './components/ui'

const ROUTES = [
  { path: '/', label: '仪表盘', el: DashboardPage },
  { path: '/leaders', label: '龙头榜', el: LeadersPage },
  { path: '/pools', label: '股票池', el: PoolsPage },
  { path: '/signals', label: '信号看板', el: SignalsPage },
  { path: '/stock', label: '个股分析', el: StockPage },
  { path: '/backtest', label: '回测中心', el: BacktestPage },
]

function parseHash() {
  const raw = window.location.hash.replace(/^#/, '') || '/'
  const [path, query = ''] = raw.split('?')
  const params = new URLSearchParams(query)
  return { path: path || '/', params }
}

export default function App() {
  const [route, setRoute] = useState(parseHash)
  const [meta, setMeta] = useState(null)
  const [q, setQ] = useState('')
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)

  useEffect(() => {
    const onHash = () => setRoute(parseHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  useEffect(() => {
    api.meta().then(setMeta).catch(() => {})
  }, [])

  const nav = useCallback((to) => {
    window.location.hash = to
  }, [])

  // 全局搜索（跳个股页）
  useEffect(() => {
    if (!q || q.length < 1) { setResults([]); return }
    setSearching(true)
    const t = setTimeout(() => {
      api.search(q)
        .then((r) => setResults(r.items || []))
        .catch(() => setResults([]))
        .finally(() => setSearching(false))
    }, 250)
    return () => clearTimeout(t)
  }, [q])

  const goStock = (code) => {
    setResults([])
    setQ('')
    nav(`/stock?code=${code}`)
  }

  const matched = useMemo(() => {
    const p = route.path
    if (p.startsWith('/stock')) return ROUTES.find((r) => r.path === '/stock')
    return ROUTES.find((r) => r.path === p) || ROUTES[0]
  }, [route.path])

  const Page = matched.el
  const pageProps = { route: route.path, params: route.params, nav, goStock }
  const phaseBadge = meta?.last_date

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <button className="brand" onClick={() => nav('/')}>
            <span className="brand-mark">龙</span>
            <span className="brand-text">
              <b>情绪周期决策台</b>
              <small>92K System · 龙头/补涨/切换</small>
            </span>
          </button>
          <nav className="nav">
            {ROUTES.map((r) => {
              const isActive = r.path === '/' ? route.path === '/' : route.path === r.path || route.path.startsWith(`${r.path}/`)
              return (
                <button
                  key={r.path}
                  className={`nav-item ${isActive ? 'active' : ''}`}
                  onClick={() => nav(r.path === '/' ? '/' : r.path)}
                >
                  {r.label}
                </button>
              )
            })}
          </nav>
          <div className="top-right">
            <div className="searchbox">
              <input
                placeholder="搜代码/名称，如 幻视影视"
                value={q}
                onChange={(e) => setQ(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && results[0] && goStock(results[0].code)}
              />
              {searching && <span className="search-spin spin" />}
              {results.length > 0 && (
                <ul className="search-drop">
                  {results.slice(0, 8).map((r) => (
                    <li key={r.code} onClick={() => goStock(r.code)}>
                      <b>{r.name}</b>
                      <span className="muted">{r.code} · {r.sector}</span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="env-chip" title="当前数据源说明">
              {meta?.mock_mode ? '模拟数据' : meta?.mode?.label || '实盘行情'}
              {phaseBadge ? <span className="env-date">{phaseBadge}</span> : null}
            </div>
          </div>
        </div>
      </header>

      <main className="main">
        <Page key={matched.path} {...pageProps} />
      </main>

      <footer className="footer risk-note">
        <p>
          ⚠️ 重要提示：本系统为 <b>学习研究用决策辅助工具</b>，
          {meta?.mock_mode
            ? <>行情为模拟数据（股票名称与代码均虚构）</>
            : <>行情为新浪公开接口实时数据（可能存在延迟或误差）</>}
          ，不构成任何投资建议。最终交易决策须自行研判，据此操作风险自负。
        </p>
        <p className="muted small">92 策略体系方法论：龙头(主升) / 补涨(高位震荡) / 切换(试错期) / 空仓(主跌) · 能量化则量化，不能量化者提供辅助信息</p>
      </footer>
    </div>
  )
}

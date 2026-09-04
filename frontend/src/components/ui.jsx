import React from 'react'

/* 基础 UI 原语：Card / Stat / Tag / Meter / Section / Empty / Loading / PctText / ConditionList */

export function Card({ title, extra, children, className = '', pad = true, style }) {
  return (
    <section className={`card ${className}`} style={style}>
      {(title || extra) && (
        <header className="card-head">
          {title && <h3 className="card-title">{title}</h3>}
          {extra && <div className="card-extra">{extra}</div>}
        </header>
      )}
      <div className={pad ? 'card-body' : ''}>{children}</div>
    </section>
  )
}

export function Stat({ label, value, sub, tone, big }) {
  return (
    <div className={`stat ${tone ? `stat-${tone}` : ''}`}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${big ? 'stat-big' : ''}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export function Tag({ children, cls = '', tone }) {
  const c = `tag ${tone ? `tag-${tone}` : ''} ${cls}`.trim()
  return <span className={c}>{children}</span>
}

export function Meter({ value, max = 100, color, height = 6 }) {
  const p = Math.max(0, Math.min(100, (value / max) * 100))
  return (
    <div className="meter" style={{ height }}>
      <div className="meter-fill" style={{ width: `${p}%`, background: color }} />
    </div>
  )
}

export function Section({ title, children, sub, style }) {
  return (
    <div className="section" style={style}>
      <h4 className="section-title">{title}</h4>
      {sub && <div className="section-sub">{sub}</div>}
      {children}
    </div>
  )
}

export function Empty({ text = '暂无数据' }) {
  return <div className="empty">{text}</div>
}

export function Loading({ text = '加载中…' }) {
  return (
    <div className="loading">
      <span className="spin" />
      <span>{text}</span>
    </div>
  )
}

export function ErrorBox({ error, onRetry }) {
  return (
    <div className="error-box">
      <div>⚠️ {String(error?.message || error)}</div>
      {onRetry && (
        <button className="btn btn-ghost btn-sm" onClick={onRetry}>
          重试
        </button>
      )}
    </div>
  )
}

/** 涨跌着色文本 */
export function PctText({ value, suffix = '%', nd = 2 }) {
  const n = Number(value)
  if (!Number.isFinite(n)) return <span className="flat">—</span>
  const cls = n > 0 ? 'up' : n < 0 ? 'down' : 'flat'
  const sign = n > 0 ? '+' : ''
  return <span className={cls}>{sign}{n.toFixed(nd)}{suffix}</span>
}

/** 勾选/未勾 条件列表 */
export function ConditionList({ items, dense = false }) {
  if (!items || !items.length) return <div className="cond-empty">—</div>
  return (
    <ul className={`cond-list ${dense ? 'cond-dense' : ''}`}>
      {items.map((it) => (
        <li key={it.id} className={it.ok ? 'ok' : 'no'}>
          <span className="cond-id">{it.id}</span>
          <span className="cond-name">{it.name}</span>
          <span className="cond-note">{it.note}</span>
          {typeof it.score === 'number' && <span className="cond-score">{it.score.toFixed(0)}</span>}
        </li>
      ))}
    </ul>
  )
}

import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Card, Tag, Empty, Loading, ErrorBox, PctText, useTableSort, sortRows, SortTh } from '../components/ui'
import { fmt } from '../format'

/* ================= 买卖信号看板 U-05 ================= */

const STRAT_FALLBACK_CN = { leader: '龙头战法', buyang: '补涨战法', qiehuan: '切换战法', generic: '通用' }
const STRAT_ORDER = ['leader', 'buyang', 'qiehuan']
const DIR_ORDER = [
  ['all', '全部'],
  ['buy', '买入'],
  ['sell', '卖出'],
  ['watch', '观察'],
]
const ST_CLS = { 强: 'tag-strong', 中: 'tag-gray', 警示: 'tag-amber' }
const DISCIPLINE = [
  '单笔仓位 ≤ 25%，不满仓梭哈',
  '止损 -5% 无条件执行，破位即走',
  '断板即撤：涨停打开 / 次日不连板，第一时间离场',
  '信号由规则引擎自动生成，仅供参考，务必人工确认盘面与题材后再决策',
]

const PAGE_CSS = `
.sig-cnt { font-variant-numeric: tabular-nums; margin-left: 2px; opacity: .9; }
.sig-seg { display: inline-flex; gap: 6px; }
.sig-seg .btn { background: rgba(255,255,255,.03); border-color: var(--border-soft); color: var(--muted); }
.sig-seg .btn:hover { color: var(--text); }
.sig-seg .btn.on { background: rgba(76,141,255,.16); border-color: rgba(76,141,255,.55); color: #fff; }
.sig-tooltip { font-size: 12px; color: var(--muted); line-height: 1.6; }
.sig-reason-cell { max-width: 420px; min-width: 240px; white-space: normal; line-height: 1.65; color: var(--muted); }
.sig-name-cell b { color: #e9effc; font-size: 13.5px; }
.sig-name-cell .sig-date { font-size: 11px; color: var(--muted2); }
.stock-cell .link { font-weight: 600; font-size: 13px; }
.stock-cell .s-code { font-size: 11px; color: var(--muted2); margin-top: 2px; }
.num-cell { font-variant-numeric: tabular-nums; }
.sig-dsc-list { list-style: none; margin: 4px 0 0; padding: 0; display: grid; gap: 6px; }
.sig-dsc-list li {
  display: flex; gap: 8px; align-items: baseline;
  font-size: 12.5px; color: #c7d3e8; line-height: 1.6;
}
.sig-dsc-list li::before { content: '▍'; color: var(--gold); flex: none; }
`

export default function SignalsPage({ route, params, nav, goStock }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [dir, setDir] = useState('all')
  const [strat, setStrat] = useState('all')

  const load = () => {
    api.signals().then(setData).catch(setErr)
  }
  useEffect(() => { load() }, [])

  const counts = useMemo(() => {
    const c = { buy: 0, sell: 0, watch: 0 }
    ;(data?.items || []).forEach((it) => {
      if (c[it.dir] != null) c[it.dir] += 1
    })
    return c
  }, [data])

  const strategies = useMemo(() => {
    const found = {}
    ;(data?.items || []).forEach((it) => {
      if (it.strategy && found[it.strategy] == null)
        found[it.strategy] = it.strategy_cn || STRAT_FALLBACK_CN[it.strategy] || it.strategy
    })
    const keys = [
      ...STRAT_ORDER.filter((k) => found[k] != null || STRAT_FALLBACK_CN[k] != null),
      ...Object.keys(found).filter((k) => !STRAT_ORDER.includes(k)),
    ]
    return keys.map((k) => ({ key: k, cn: found[k] || STRAT_FALLBACK_CN[k] || k }))
  }, [data])

  const filtered = useMemo(() => {
    if (!data?.items) return []
    return data.items.filter(
      (it) => (dir === 'all' || it.dir === dir) && (strat === 'all' || it.strategy === strat)
    )
  }, [data, dir, strat])
  const sigSort = useTableSort(null)
  const shown = sortRows(filtered, sigSort.key, sigSort.dir)

  if (err) return <ErrorBox error={err} onRetry={load} />
  if (!data) return <Loading />

  const total = (data.items || []).length

  return (
    <div className="page">
      <style>{PAGE_CSS}</style>

      <div className="page-head">
        <div>
          <h2 className="page-title">买卖信号看板</h2>
          <div className="page-sub">U-05 · 规则引擎触发信号（买入 / 卖出 / 观察），仅供参考</div>
        </div>
        <div className="toolbar">
          <Tag tone="buy">买入 {data.count?.buy ?? counts.buy}</Tag>
          <Tag tone="sell">卖出 {data.count?.sell ?? counts.sell}</Tag>
          <Tag tone="watch">观察 {data.count?.watch ?? counts.watch}</Tag>
          <Tag cls="tag-gray">更新 {data.date || '—'}</Tag>
        </div>
      </div>

      {/* 筛选工具条 */}
      <div className="card">
        <div className="card-body" style={{ padding: '10px 16px' }}>
          <div className="toolbar" style={{ justifyContent: 'space-between' }}>
            <div className="sig-seg">
              {DIR_ORDER.map(([k, label]) => (
                <button
                  key={k}
                  type="button"
                  className={`btn btn-sm ${dir === k ? 'on' : ''}`}
                  onClick={() => setDir(k)}
                >
                  {label}
                  {k !== 'all' && <span className="sig-cnt">{counts[k]}</span>}
                </button>
              ))}
            </div>
            <div className="toolbar">
              <span className="muted2 small">策略</span>
              <select className="input" value={strat} onChange={(e) => setStrat(e.target.value)}>
                <option value="all">全部策略（{total}）</option>
                {strategies.map((s) => (
                  <option key={s.key} value={s.key}>
                    {s.cn}
                  </option>
                ))}
              </select>
            </div>
          </div>
          {data.note && <div className="sig-tooltip" style={{ marginTop: 8 }}>{data.note}</div>}
        </div>
      </div>

      {/* 信号列表 */}
      <Card title="触发信号" extra={<Tag cls="tag-gray">{filtered.length} 条</Tag>}>
        {total === 0 ? (
          <Empty text="当前无任何触发信号 —— 平静运行中，无买卖动作" />
        ) : filtered.length === 0 ? (
          <Empty text="该筛选条件下暂无信号，试试放宽筛选" />
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <SortTh label="信号" sortKey="signal" sort={sigSort} />
                  <SortTh label="标的" sortKey="name" sort={sigSort} />
                  <SortTh label="策略" sortKey="strategy_cn" sort={sigSort} />
                  <SortTh label="方向" sortKey="dir" sort={sigSort} />
                  <SortTh label="强度" sortKey="strength" sort={sigSort} />
                  <SortTh label="现价" sortKey="price" sort={sigSort} />
                  <SortTh label="当日涨幅" sortKey="pct_today" sort={sigSort} />
                  <th>触发理由</th>
                </tr>
              </thead>
              <tbody>
                {shown.map((it, i) => {
                  return (
                    <tr key={`${it.code}-${it.signal}-${it.date}-${i}`}>
                      <td className="sig-name-cell">
                        <b>{it.signal || '—'}</b>
                        {it.date && <div className="sig-date">{it.date}</div>}
                      </td>
                      <td className="stock-cell">
                        <button
                          type="button"
                          className="link"
                          onClick={() => goStock(it.code)}
                        >
                          {it.name || '—'}
                        </button>
                        <div className="s-code">
                          {it.code}
                          {it.sector ? ` · ${it.sector}` : ''}
                        </div>
                      </td>
                      <td>
                        <Tag cls="tag-gray">{it.strategy_cn || it.strategy || '—'}</Tag>
                      </td>
                      <td>
                        {it.dir === 'buy' || it.dir === 'sell' || it.dir === 'watch' ? (
                          <Tag tone={it.dir}>{it.dir_cn || it.dir}</Tag>
                        ) : (
                          <Tag cls="tag-gray">{it.dir_cn || it.dir || '—'}</Tag>
                        )}
                      </td>
                      <td>
                        {it.strength ? (
                          <Tag cls={ST_CLS[it.strength] || 'tag-gray'}>{it.strength}</Tag>
                        ) : (
                          <span className="muted2">—</span>
                        )}
                      </td>
                      <td className="num-cell">{it.price != null ? fmt(it.price) : '—'}</td>
                      <td className="num-cell">
                        {it.pct_today != null ? <PctText value={it.pct_today} /> : <span className="muted2">—</span>}
                      </td>
                      <td className="sig-reason-cell">{it.reason || '—'}</td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* 纪律说明卡 */}
      <Card title="交易纪律（严格执行，不因信号而动摇）">
        <ul className="sig-dsc-list">
          {DISCIPLINE.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
      </Card>
    </div>
  )
}

import { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Card, Tag, Empty, Loading, ErrorBox, PctText, useTableSort, sortRows, SortTh } from '../components/ui'
import { fmt, fmtPct } from '../format'

const TYPE_META = {
  buy: { label: '买点', tone: 'buy' },
  sell: { label: '卖点', tone: 'sell' },
  watch: { label: '观察', tone: 'watch' },
}

export default function OpsPage({ route, params, nav, goStock }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const [code, setCode] = useState('')

  const load = useCallback(() => {
    api.opsOverview().then(setData).catch(setErr)
  }, [])
  useEffect(() => {
    load()
    const t = setInterval(load, 8000)
    return () => clearInterval(t)
  }, [load])

  const bSort = useTableSort('entry_time')
  const sSort = useTableSort('exit_time')
  const wSort = useTableSort('score')
  const buys = sortRows((data?.buy) || [], bSort.key, bSort.dir)
  const sells = sortRows((data?.sell) || [], sSort.key, sSort.dir)
  const watch = sortRows((data?.watch) || [], wSort.key, wSort.dir)
  const stats = data?.stats || {}
  const win = data?.window || null
  const tradeOpen = !!win && !!win.open

  const act = async (fn, okMsg) => {
    setBusy(true)
    try {
      await fn()
      load()
      if (okMsg && window.confirm) window.alert(okMsg)
    } catch (e) {
      alert(`操作失败：${e.message || e}`)
    } finally {
      setBusy(false)
    }
  }
  const manualSell = (c) =>
    act(() => api.opsManualSell(c).then((r) => { if (!r.ok) throw new Error(r.error || '无持仓') }), `已按现价了结 ${c}`)
  const delSell = (row) => {
    if (!window.confirm(`确认删除卖出池记录：${row.name}（${row.code}）？删除后不可恢复。`)) return
    act(() => api.opsDelete(row.id).then((r) => { if (!r.ok) throw new Error(r.error || '删除失败') }), `已删除 ${row.name} 的卖出记录`)
  }
  const ignore = (pool, c) =>
    act(() => api.opsIgnore(pool, c), `已移除 ${pool === 'buy' ? '买入池' : '观察池'}：${c}`)
  const addWatch = () => {
    const c = code.trim()
    if (!/^\d{6}$/.test(c)) { alert('请输入 6 位股票代码'); return }
    act(() => api.opsManualWatch(c).then((r) => { if (!r.ok) throw new Error(r.error || '加入失败') }), `已加入观察池：${c}`)
    setCode('')
  }
  const [openR, setOpenR] = useState({})
  const toggleReason = (id) => setOpenR((m) => ({ ...m, [id]: !m[id] }))

  const timeShort = (t) => (t || '').slice(11, 19)
  const dateShort = (t) => (t || '').slice(0, 10)

  return (
    <div className="page">
      <style>{`
        .ops-stats{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:10px; }
        .ops-feed{ display:flex; flex-direction:column; gap:6px; max-height:230px; overflow:auto; padding-right:4px; }
        .ops-feed-item{ display:flex; flex-wrap:wrap; align-items:center; gap:6px 10px; padding:6px 10px;
          border-radius:9px; background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.06); font-size:12.5px; }
        .ops-feed-item .ts{ color:#5f7598; font-family:var(--mono); font-size:11.5px; }
        .ops-feed-item .rs{ color:#7f94b5; flex:1 1 200px; }
        .ops-note{ color:#5f7598; font-size:12px; margin-top:6px; }
        .ops-act{ display:inline-flex; gap:6px; }
        .ops-act .btn{ padding:2px 8px; font-size:12px; }
        .ops-watch-add{ display:flex; gap:6px; align-items:center; }
        .ops-watch-add input{ width:130px; }
        .ops-reason-cell{ min-width:220px; max-width:460px; }
        .ops-reason{ margin:0; line-height:1.6; font-size:12.5px; color:#b9c6dd; word-break:break-word;
          white-space:normal; display:-webkit-box; -webkit-box-orient:vertical; -webkit-line-clamp:3; overflow:hidden; cursor:pointer; }
        .ops-reason-full{ margin:0; line-height:1.7; font-size:12.5px; color:#dde7fb; word-break:break-word;
          white-space:normal; overflow:visible; }
        .ops-long{ white-space:normal; word-break:break-word; line-height:1.65; font-size:12.5px; color:#c6d3ea;
          min-width:190px; max-width:400px; }
        .tbl td.ops-long{ white-space:normal !important; word-break:break-word !important; vertical-align:top; }
        /* 打板台全部表格: 单元格强制换行, 杜绝理由被 nowrap 截断 */
        .ops-tbl th{ white-space:nowrap; }
        .ops-tbl td{ white-space:normal !important; word-break:break-word !important; overflow-wrap:anywhere; vertical-align:middle; }
        .ops-tbl td.num, .ops-tbl .num{ white-space:nowrap; }
        .ops-reason:hover{ color:#e9effc; }
        .ops-more{ display:inline-block; margin-left:4px; font-size:11.5px; color:var(--accent2); cursor:pointer; user-select:none; }
        .ops-more:hover{ text-decoration:underline; }
      `}</style>

      <div className="page-head">
        <div>
          <h1 className="page-title">打板操作台</h1>
          <div className="page-sub">92 模式自动化盯盘：龙头/补涨买点提示 → 买入池 → 卖点提示结算 → 卖出池盈亏复盘 · 观察池记录</div>
        </div>
        <div className="toolbar">
          <Tag cls="tag-gray">数据 {data?.date || '—'}</Tag>
          <Tag tone={tradeOpen ? 'buy' : 'gray'} cls={tradeOpen ? '' : 'tag-watch'}>
            {tradeOpen ? `交易时段 ${win.start}–${win.end}` : `非交易时段(买卖暂停 ${win?.start}–${win?.end})`}
          </Tag>
          <button className="btn btn-sm" disabled={busy} onClick={() => act(api.opsFlush, '')}>立即扫描</button>
        </div>
      </div>

      {err && !data ? <ErrorBox error={err} onRetry={load} /> : !data ? <Loading /> : (
        <>
          {/* 提示流 */}
          <Card title="最新提示 · 买卖点及时推送" extra={<span className="muted">每 8 秒自动扫描 · 提示时间/价格/理由</span>}>
            {(!data.prompts || data.prompts.length === 0) ? (
              <Empty text="暂无提示。出现龙头/补涨/切换买点或持仓卖点时会自动推送到这里" />
            ) : (
              <div className="ops-feed">
                {data.prompts.map((p, i) => {
                  const mt = TYPE_META[p.type] || TYPE_META.watch
                  return (
                    <div className="ops-feed-item" key={i}>
                      <Tag tone={mt.tone}>{mt.label}</Tag>
                      <b><span className="link" onClick={() => goStock(p.code)}>{p.name}</span> {p.code}</b>
                      {p.signal && <span className="muted small">{p.signal}</span>}
                      {p.price != null && <span className="num">{fmt(p.price)}</span>}
                      <span className="ts">{dateShort(p.ts)} {timeShort(p.ts)}</span>
                      <span className="rs" title={p.reason}>{p.reason}</span>
                    </div>
                  )
                })}
              </div>
            )}
            <div className="ops-note">规则：买卖操作仅于交易日 09:25–14:59 执行（买点=买入信号触发；卖点=止损-5%/断板/加速一致/不及预期等），其余时段自动暂停；观察池随时记录。</div>
          </Card>

          {/* 统计 */}
          <div className="ops-stats">
            <Card><div className="stat-label">持仓中（买入池）</div><div className="stat-value">{stats.buy_open ?? '—'}</div><div className="stat-sub">买点触发自动入池</div></Card>
            <Card><div className="stat-label">观察中</div><div className="stat-value">{stats.watch ?? '—'}</div><div className="stat-sub">符合模式待确认</div></Card>
            <Card><div className="stat-label">已结算（卖出池）</div><div className="stat-value">{stats.sold ?? '—'}</div><div className="stat-sub">含盈亏与理由复盘</div></Card>
            <Card><div className="stat-label">胜率</div><div className="stat-value">{stats.win_rate_pct != null ? `${fmt(stats.win_rate_pct, 1)}%` : '—'}</div><div className="stat-sub">盈利笔 / 已结算笔</div></Card>
            <Card><div className="stat-label">平均盈 / 亏</div><div className="stat-value up">{stats.avg_win_pct != null ? `+${fmt(stats.avg_win_pct)}%` : '—'}</div><div className="stat-sub down">{stats.avg_loss_pct != null ? `${fmt(stats.avg_loss_pct)}%` : ''} · 平均持有 {stats.avg_hold_days ?? '—'} 天</div></Card>
          </div>

          {/* 买入池 */}
          <Card
            title={`买入池 · 持仓 ${buys.length}`}
            extra={<span className="muted">出现卖点会自动提示并结算到卖出池</span>}
          >
            {buys.length === 0 ? <Empty text="买入池为空：出现买点提示后自动加入" /> : (
              <div className="table-wrap">
                <table className="tbl ops-tbl">
                  <thead><tr>
                    <SortTh label="名称" sortKey="name" sort={bSort} />
                    <SortTh label="买点时间" sortKey="entry_time" sort={bSort} />
                    <SortTh label="买点信号" sortKey="signal" sort={bSort} />
                    <SortTh label="买入价" sortKey="entry_price" sort={bSort} />
                    <SortTh label="现价" sortKey="last_price" sort={bSort} />
                    <SortTh label="浮盈" sortKey="live_pct" sort={bSort} />
                    <th>买入理由</th>
                    <th>操作</th>
                  </tr></thead>
                  <tbody>
                    {buys.map((r) => (
                      <tr key={r.id}>
                        <td><b><span className="link" onClick={() => goStock(r.code)}>{r.name}</span></b><div className="muted2 small">{r.code} · {r.sector}</div></td>
                        <td className="num">{dateShort(r.entry_time)}<div className="muted2 small">{timeShort(r.entry_time)}</div></td>
                        <td><Tag cls="tag-gray">{r.signal || r.strategy || '—'}</Tag></td>
                        <td className="num">{r.entry_price != null ? fmt(r.entry_price) : '—'}</td>
                        <td className="num">{r.last_price != null ? fmt(r.last_price) : '—'}</td>
                        <td>{r.live_pct != null ? <PctText value={r.live_pct} /> : '—'}</td>
                        <td className="ops-long" title={r.reason}>{r.reason}</td>
                        <td>
                          <div className="ops-act">
                            <button className="btn btn-sm" onClick={() => goStock(r.code)}>查看</button>
                            <button className="btn btn-sm btn-danger" onClick={() => manualSell(r.code)} title={tradeOpen ? '按当前价了结并结算' : '仅 09:25–14:59 交易时段可买卖'} disabled={!tradeOpen}>
                              {tradeOpen ? '了结' : '暂停'}
                            </button>
                            <button className="btn btn-sm btn-ghost" onClick={() => ignore('buy', r.code)}>忽略</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* 卖出池 */}
          <Card title={`卖出池 · 已结算 ${sells.length}`} extra={<span className="muted">自动记录买入/卖出时间价格、盈亏与买卖理由</span>}>
            {sells.length === 0 ? <Empty text="卖出池为空：持仓触发卖点后自动结算到这里" /> : (
              <div className="table-wrap">
                <table className="tbl ops-tbl">
                  <thead><tr>
                    <SortTh label="名称" sortKey="name" sort={sSort} />
                    <SortTh label="买入时间" sortKey="entry_time" sort={sSort} />
                    <SortTh label="买入价" sortKey="entry_price" sort={sSort} />
                    <SortTh label="卖出时间" sortKey="exit_time" sort={sSort} />
                    <SortTh label="卖出价" sortKey="exit_price" sort={sSort} />
                    <SortTh label="盈利%" sortKey="pnl_pct" sort={sSort} />
                    <SortTh label="持有" sortKey="hold_days" sort={sSort} />
                    <th>当初买入（买点/理由）</th>
                    <th>卖出理由</th>
                    <th>操作</th>
                  </tr></thead>
                  <tbody>
                    {sells.map((r) => (
                      <tr key={r.id}>
                        <td><b><span className="link" onClick={() => goStock(r.code)}>{r.name}</span></b><div className="muted2 small">{r.code}</div></td>
                        <td className="num">{dateShort(r.entry_time)}<div className="muted2 small">{timeShort(r.entry_time)}</div></td>
                        <td className="num" style={{ fontWeight: 600 }}>{r.entry_price != null ? fmt(r.entry_price) : '—'}</td>
                        <td className="num">{dateShort(r.exit_time)}<div className="muted2 small">{timeShort(r.exit_time)}</div></td>
                        <td className="num" style={{ fontWeight: 600 }}>{r.exit_price != null ? fmt(r.exit_price) : '—'}</td>
                        <td><PctText value={r.pnl_pct} /></td>
                        <td className="num">{r.hold_days != null ? `${r.hold_days}天` : '—'}</td>
                        <td className="ops-long" title={r.reason}>
                          <span className="muted2 small">{r.signal ? `${r.signal} · ` : ''}</span>{r.reason}
                        </td>
                        <td className="ops-long" title={r.exit_reason}>{r.exit_reason}</td>
                        <td>
                          <div className="ops-act">
                            <button className="btn btn-sm btn-danger" title="删除该条卖出记录（管理用）" onClick={() => delSell(r)}>删除</button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>

          {/* 观察池 */}
          <Card
            title={`观察池 · ${watch.length}`}
            extra={
              <div className="ops-watch-add">
                <input className="input" placeholder="输入6位代码" value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ''))} />
                <button className="btn btn-sm" onClick={addWatch}>手动加入观察</button>
              </div>
            }
          >
            {watch.length === 0 ? <Empty text="观察池为空：符合模式的龙头/补涨/切换候选会自动记录（含日期与理由）" /> : (
              <div className="table-wrap">
                <table className="tbl ops-tbl">
                  <thead><tr>
                    <SortTh label="名称" sortKey="name" sort={wSort} />
                    <SortTh label="评分" sortKey="score" sort={wSort} />
                    <SortTh label="最近记录" sortKey="updated_at" sort={wSort} />
                    <th>记录日期</th>
                    <th>观察理由</th>
                    <th>操作</th>
                  </tr></thead>
                  <tbody>
                    {watch.map((r) => {
                      const reason = r.reason || '—'
                      const isOpen = !!openR[r.id]
                      const long = reason.length > 30
                      return (
                        <tr key={r.id}>
                          <td><b><span className="link" onClick={() => goStock(r.code)}>{r.name}</span></b><div className="muted2 small">{r.code} · {r.sector}</div></td>
                          <td className="num" style={{ fontWeight: 700, color: (r.score ?? -1) >= 70 ? 'var(--gold)' : (r.score ?? -1) >= 55 ? '#7aa9ff' : undefined }}>
                            {r.score != null ? fmt(r.score, 1) : '—'}
                          </td>
                          <td className="num">{dateShort(r.updated_at)} {timeShort(r.updated_at)}</td>
                          <td className="num">{r.last_date || r.entry_date || '—'}</td>
                          <td className="ops-reason-cell">
                            {isOpen ? (
                              <>
                                <div className="ops-reason-full">{reason}</div>
                                <span className="ops-more" onClick={(e) => { e.stopPropagation(); toggleReason(r.id) }}>收起 ▲</span>
                              </>
                            ) : (
                              <>
                                <div
                                  className="ops-reason"
                                  title={reason}
                                  onClick={(e) => { e.stopPropagation(); toggleReason(r.id) }}
                                >
                                  {reason}
                                </div>
                                {long && (
                                  <span className="ops-more" onClick={(e) => { e.stopPropagation(); toggleReason(r.id) }}>
                                    展开 ▼
                                  </span>
                                )}
                              </>
                            )}
                          </td>
                          <td>
                            <div className="ops-act">
                              <button className="btn btn-sm" onClick={() => goStock(r.code)}>查看</button>
                              <button className="btn btn-sm btn-ghost" onClick={() => ignore('watch', r.code)}>移除</button>
                            </div>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
          <div className="muted small" style={{ color: '#5f7598' }}>{data.disclaimer}</div>
        </>
      )}
    </div>
  )
}

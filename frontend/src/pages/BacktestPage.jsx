import React, { useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Card, Tag, Empty, Loading, ErrorBox, PctText } from '../components/ui'
import EChart, { TOOLTIP, axisCommon } from '../components/EChart'
import { fmt, fmtPct, clsPct, fmtDate } from '../format'

/* ================= 回测中心 U-06 ================= */

const MODE_CN = { auto: '全周期轮动', leader: '龙头战法', buyang: '补涨战法', qiehuan: '切换战法' }
const UP_RED = '#f5444b' // A股红涨（策略净值线）

/* 本地日期加减（避免 UTC 时区偏移） */
function shiftDate(iso, days) {
  if (!iso) return ''
  const [y, mo, dd] = iso.slice(0, 10).split('-').map(Number)
  if (!y || !mo || !dd) return iso.slice(0, 10)
  const dt = new Date(y, mo - 1, dd)
  dt.setDate(dt.getDate() + days)
  const p = (n) => String(n).padStart(2, '0')
  return `${dt.getFullYear()}-${p(dt.getMonth() + 1)}-${p(dt.getDate())}`
}
function clampDate(s, min, max) {
  let out = s
  if (min && out < min) out = min
  if (max && out > max) out = max
  return out
}
function defaultStart(range) {
  if (!range || range.length < 2 || !range[1]) return '2025-01-02'
  return clampDate(shiftDate(range[1], -420), range[0], range[1])
}

/* 数字 → 千分位带符号现金 */
function cash(x) {
  const n = Number(x)
  if (!Number.isFinite(n)) return '—'
  const sign = n > 0 ? '+' : ''
  return `${sign}${n.toLocaleString('zh-CN', { maximumFractionDigits: 2 })}`
}

/* 两序列按日期对齐绘制 */
function alignByDate(dates, rows) {
  const map = {}
  ;(rows || []).forEach((r) => { map[r.date] = Number(r.value) })
  return dates.map((dt) => (map[dt] != null ? map[dt] : null))
}

const PAGE_CSS = `
.bt-toolbar { display:flex; align-items:flex-end; gap:14px; flex-wrap:wrap; }
.bt-field { display:flex; flex-direction:column; gap:5px; }
.bt-field > label { font-size: 11.5px; color: var(--muted); }
.bt-field .input { min-width: 190px; }
.bt-note { font-size: 12px; color: var(--muted2); line-height: 1.7; margin-top: 10px; }
.bt-stats { display:grid; gap:10px; grid-template-columns: repeat(auto-fill, minmax(158px, 1fr)); }
.bt-stat .stat-label { font-size: 11.5px; }
.bt-stat .stat-value { font-size: 21px; }
.bt-stat .stat-value.big { font-size: 26px; }
.bt-stat .stat-sub { font-size: 11px; }
.bt-run-head { display:flex; align-items:center; justify-content:space-between; gap:10px; flex-wrap:wrap; }
.bt-strat-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; }
.bt-strat-list li {
  display:flex; align-items:center; gap:10px; padding: 9px 2px;
  border-bottom: 1px solid rgba(34,48,74,.4); font-size: 13px;
}
.bt-strat-list li:last-child { border-bottom: 0; }
.bt-strat-list .grow { flex:1; }
.bt-tbl-scroll { max-height: 470px; overflow: auto; }
.bt-tbl-scroll thead th { position: sticky; top: 0; z-index: 2; background: #0e1726; }
.bt-tbl-scroll td.wrap-cell { white-space: normal; min-width: 200px; max-width: 380px; color: var(--muted); line-height: 1.6; }
.bt-tbl-scroll td { font-variant-numeric: tabular-nums; }
.bt-empty-card .empty { padding: 26px 0; }
.bt-foot { text-align:center; }
`

/* 绩效统计块（tone: up 红 / down 绿，与 A股涨跌习惯一致） */
function Metric({ label, value, sub, tone, big }) {
  const toneCls = tone && tone !== 'flat' ? ` stat-${tone}` : ''
  return (
    <div className={`bt-stat stat${toneCls}`}>
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${big ? 'big' : ''}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  )
}

export default function BacktestPage({ route, params, nav, goStock }) {
  const [meta, setMeta] = useState(null)
  const [metaErr, setMetaErr] = useState(null)
  const [mode, setMode] = useState('auto')
  const [start, setStart] = useState('2025-01-02')
  const [capital, setCapital] = useState(100000)
  const [running, setRunning] = useState(false)
  const [result, setResult] = useState(null)
  const [runErr, setRunErr] = useState(null)

  // 加载配置（mode / 日期范围 / 说明）
  useEffect(() => {
    api
      .backtestMeta()
      .then((m) => {
        setMeta(m)
        if (m && Array.isArray(m.modes) && m.modes.length && !m.modes.some((x) => x.key === mode)) {
          setMode(m.modes[0].key)
        }
        setStart((prev) => {
          const dflt = defaultStart(m && m.range)
          // 仅当用户尚未手动改过（仍是初始占位值）时应用默认
          return prev === '2025-01-02' ? dflt : prev
        })
      })
      .catch(setMetaErr)
  }, [])

  const range = meta && Array.isArray(meta.range) ? meta.range : null
  const endDate = range && range[1] ? range[1] : ''
  const modes = meta && Array.isArray(meta.modes) ? meta.modes : []

  const run = () => {
    setRunning(true)
    setRunErr(null)
    const payload = {
      mode,
      start: start || '',
      end: endDate,
      capital: Number(capital) || 100000,
    }
    api
      .runBacktest(payload)
      .then((res) => {
        setResult(res)
        setRunning(false)
      })
      .catch((e) => {
        setRunErr(e)
        setRunning(false)
      })
  }

  const stats = result && result.stats ? result.stats : null

  /* 净值/回撤图 option */
  const eqOption = useMemo(() => {
    if (!result) return null
    const equity = result.equity || []
    const benchmark = result.benchmark || []
    if (!equity.length) return null
    const dates = equity.map((e) => e.date)
    const eqVals = equity.map((e) => Number(e.value))
    const benchVals =
      benchmark.length === equity.length
        ? benchmark.map((b) => Number(b.value))
        : alignByDate(dates, benchmark)
    return {
      animation: false,
      color: [UP_RED, '#6b83a8'],
      legend: { data: ['策略净值', '基准(全市场)'], top: 2, right: 8, itemWidth: 16, itemHeight: 8, textStyle: { color: '#8fa3c0', fontSize: 11 } },
      tooltip: { ...TOOLTIP, trigger: 'axis' },
      grid: { left: 58, right: 16, top: 34, bottom: 30 },
      xAxis: { type: 'category', data: dates, ...axisCommon(true) },
      yAxis: { ...axisCommon(false), scale: true },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series: [
        {
          name: '策略净值', type: 'line', data: eqVals, showSymbol: false, smooth: false,
          lineStyle: { width: 2, color: UP_RED },
          areaStyle: { color: 'rgba(245,68,75,.06)' },
        },
        {
          name: '基准(全市场)', type: 'line', data: benchVals, showSymbol: false, smooth: false,
          lineStyle: { width: 1.4, color: '#6b83a8', type: 'dashed' },
        },
      ],
    }
  }, [result])

  const ddOption = useMemo(() => {
    if (!result) return null
    const dd = result.drawdown || []
    if (!dd.length) return null
    const dates = dd.map((r) => r.date)
    const vals = dd.map((r) => Number(r.value))
    const minV = Math.min(...vals, 0)
    return {
      animation: false,
      tooltip: {
        ...TOOLTIP,
        trigger: 'axis',
        valueFormatter: (v) => `${fmt(v)}%`,
      },
      grid: { left: 58, right: 16, top: 24, bottom: 30 },
      xAxis: { type: 'category', data: dates, ...axisCommon(true) },
      yAxis: { ...axisCommon(false), axisLabel: { color: '#8fa3c0', fontSize: 11, formatter: (v) => `${v}%` } },
      dataZoom: [{ type: 'inside', start: 0, end: 100 }],
      series: [
        {
          name: '回撤', type: 'line', data: vals, showSymbol: false,
          lineStyle: { width: 1.6, color: '#0ecb81' },
          areaStyle: { color: 'rgba(14,203,129,.22)' },
          markLine: stats
            ? {
                symbol: 'none',
                silent: true,
                data: [{ yAxis: Number(stats.max_drawdown_pct) || minV }],
                lineStyle: { color: '#f2c14e', type: 'dashed', width: 1 },
                label: {
                  formatter: `最大回撤 ${fmt(stats.max_drawdown_pct)}%`,
                  color: '#f2c14e', fontSize: 11, position: 'insideEndTop',
                },
              }
            : undefined,
        },
      ],
    }
  }, [result, stats])

  const stratRows = useMemo(() => {
    if (!stats || !stats.by_strategy) return []
    return Object.entries(stats.by_strategy).map(([name, v]) => ({
      name,
      n: v && v.n != null ? v.n : 0,
      win: v && v.win != null ? v.win : null,
      pnl: v && v.pnl_sum != null ? Number(v.pnl_sum) : null,
    }))
  }, [result])

  if (metaErr) {
    return (
      <div className="page">
        <style>{PAGE_CSS}</style>
        <ErrorBox error={metaErr} onRetry={() => { setMetaErr(null); api.backtestMeta().then(setMeta).catch(setMetaErr) }} />
      </div>
    )
  }
  if (!meta) return <Loading text="正在加载回测配置 …" />

  const netProfit = stats ? Number(stats.gross_profit || 0) - Number(stats.gross_loss || 0) : null
  const retTone = stats ? clsPct(stats.total_ret_pct) : ''
  const annTone = stats ? clsPct(stats.annual_ret_pct) : ''
  const ddTone = stats ? clsPct(stats.max_drawdown_pct) : ''
  const netTone = netProfit != null ? clsPct(netProfit) : ''

  return (
    <div className="page">
      <style>{PAGE_CSS}</style>

      <div className="page-head">
        <div>
          <h2 className="page-title">回测中心</h2>
          <div className="page-sub">U-06 · 在历史行情上回测 92K 策略（仅供研究，不代表真实收益）</div>
        </div>
        {range && <Tag cls="tag-gray">数据区间 {fmtDate(range[0])} ~ {fmtDate(range[1])}</Tag>}
      </div>

      {/* 参数表单 */}
      <Card title="回测参数">
        <div className="card-body">
          <div className="bt-toolbar">
            <div className="bt-field">
              <label>策略模式</label>
              <select className="input" value={mode} onChange={(e) => setMode(e.target.value)}>
                {modes.map((md) => (
                  <option key={md.key} value={md.key}>
                    {md.cn || MODE_CN[md.key] || md.key}
                    {md.phases ? `（${md.phases}）` : ''}
                  </option>
                ))}
              </select>
            </div>
            <div className="bt-field">
              <label>起始日期</label>
              <input
                type="date"
                className="input"
                value={start}
                min={range ? range[0] : undefined}
                max={endDate || undefined}
                onChange={(e) => setStart(e.target.value)}
              />
            </div>
            <div className="bt-field">
              <label>初始资金（元）</label>
              <input
                type="number"
                className="input"
                style={{ width: 150 }}
                min={10000}
                step={10000}
                value={capital}
                onChange={(e) => setCapital(e.target.value)}
              />
            </div>
            <button
              type="button"
              className="btn btn-primary"
              style={{ marginLeft: 'auto' }}
              disabled={running}
              onClick={run}
            >
              {running ? '回测中…' : '开始回测'}
            </button>
          </div>
          {meta.note && <div className="bt-note">规则说明：{meta.note}</div>}
        </div>
      </Card>

      {/* 运行错误（如 400 参数非法） */}
      {runErr && (
        <Card title="回测失败">
          <ErrorBox error={runErr} onRetry={run} />
        </Card>
      )}

      {/* 结果区 */}
      {running && (
        <Card>
          <div className="card-body">
            <Loading text="回测计算中，请稍候…" />
          </div>
        </Card>
      )}

      {result && stats && (
        <>
          <div className="bt-run-head">
            <div>
              <h3 style={{ margin: 0 }}>回测结果</h3>
              <div className="muted small" style={{ marginTop: 2 }}>
                {MODE_CN[stats.mode] || stats.mode} · {fmtDate(stats.start)} ~ {fmtDate(stats.end)} ·{' '}
                {stats.days} 个交易日
              </div>
            </div>
            <div className="toolbar">
              <Tag cls="tag-gray">初始资金 ¥{fmt(stats.init_capital, 0)}</Tag>
              <Tag cls="tag-gray">期末净值 ¥{fmt(stats.final_equity, 2)}</Tag>
            </div>
          </div>

          {/* a) 统计卡 */}
          <Card>
            <div className="bt-stats">
              <Metric label="总收益率" value={fmtPct(stats.total_ret_pct)} tone={retTone || undefined} />
              <Metric label="年化收益率" value={fmtPct(stats.annual_ret_pct)} tone={annTone || undefined} sub="按自然日年化" />
              <Metric label="最大回撤" value={fmtPct(stats.max_drawdown_pct)} tone={ddTone || undefined} />
              <Metric label="胜率" value={stats.win_rate_pct != null ? `${fmt(stats.win_rate_pct, 1)}%` : '—'} sub={`盈 ${fmt(stats.avg_win_pct, 2)}% / 亏 ${fmt(stats.avg_loss_pct, 2)}%`} />
              <Metric label="盈亏比" value={stats.profit_factor != null ? fmt(stats.profit_factor, 2) : '—'} sub="毛盈利 / 毛亏损" />
              <Metric label="交易次数" value={stats.trade_count != null ? stats.trade_count : '—'} />
              <Metric label="期末净值" big value={`¥${fmt(stats.final_equity, 2)}`} sub={`初始 ¥${fmt(stats.init_capital, 0)}`} />
              <Metric
                label="净利润"
                tone={netTone || undefined}
                value={netProfit != null ? cash(netProfit) : '—'}
                sub={`盈利 ${cash(stats.gross_profit)} / 亏损 ${cash(stats.gross_loss)}`}
              />
            </div>
          </Card>

          {/* b) 净值 + 回撤图 */}
          <div className="grid2">
            <Card title="净值曲线（起点=100）">
              {eqOption ? <EChart option={eqOption} height={250} /> : <Empty text="无净值序列" />}
            </Card>
            <Card title="回撤曲线（%）">
              {ddOption ? <EChart option={ddOption} height={250} /> : <Empty text="无回撤序列" />}
            </Card>
          </div>

          {/* c) 分策略表现 */}
          {stratRows.length > 0 && (
            <Card title="分策略表现" extra={<Tag cls="tag-gray">按策略战法聚合</Tag>}>
              <ul className="bt-strat-list">
                {stratRows.map((s) => (
                  <li key={s.name}>
                    <Tag cls="tag-gray">{s.name}</Tag>
                    <span className="grow muted2">
                      交易 {s.n} 笔 · 胜率 {s.win != null ? `${fmt(s.win, 1)}%` : '—'}
                    </span>
                    <span className={`num ${clsPct(s.pnl)}`}>{s.pnl != null ? cash(s.pnl) : '—'}</span>
                  </li>
                ))}
              </ul>
            </Card>
          )}

          {/* d) 交易明细 */}
          <Card
            title="交易明细"
            extra={
              <Tag cls="tag-gray">
                {(result.trades || []).length} 笔 · 收盘成交（含单边 0.15% 费用）
              </Tag>
            }
          >
            {(result.trades || []).length === 0 ? (
              <Empty text="本次区间内没有产生交易" />
            ) : (
              <div className="table-wrap">
                <div className="bt-tbl-scroll">
                  <table className="tbl">
                    <thead>
                      <tr>
                        <th>代码</th>
                        <th>名称</th>
                        <th>策略</th>
                        <th>买入日</th>
                        <th>卖出日</th>
                        <th>买入价</th>
                        <th>卖出价</th>
                        <th>收益率</th>
                        <th>盈亏额</th>
                        <th>持有</th>
                        <th>离场原因</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(result.trades || []).map((t, i) => (
                        <tr key={`${t.code}-${t.entry_date}-${i}`}>
                          <td className="muted2">{t.code}</td>
                          <td>
                            <button type="button" className="link" onClick={() => goStock(t.code)}>
                              {t.name}
                            </button>
                          </td>
                          <td><Tag cls="tag-gray">{t.strategy_cn || t.strategy || '—'}</Tag></td>
                          <td>{t.entry_date || '—'}</td>
                          <td>{t.exit_date || '—'}</td>
                          <td>{t.entry_px != null ? fmt(t.entry_px) : '—'}</td>
                          <td>{t.exit_px != null ? fmt(t.exit_px) : '—'}</td>
                          <td><PctText value={t.pnl_pct} /></td>
                          <td className={t.pnl_cash != null ? clsPct(t.pnl_cash) : ''}>
                            {t.pnl_cash != null ? cash(t.pnl_cash) : '—'}
                          </td>
                          <td>{t.hold_days != null ? `${t.hold_days}天` : '—'}</td>
                          <td className="wrap-cell">{t.reason || '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </Card>
        </>
      )}

      {/* 无结果提示（从未运行且无错误时） */}
      {!result && !runErr && !running && (
        <Card className="bt-empty-card">
          <Empty text="设置上方参数后点击「开始回测」—— 将基于历史行情回放策略信号与交易" />
        </Card>
      )}

      <p className="bt-foot muted small" style={{ margin: 0 }}>
        ⚠️ 特别提示：回测为信号与交易计算管线的历史回放，仅供研究，不代表未来收益；
        结果受模型简化、微观约束(一字无法买入/停牌/滑点等)影响，切勿据此实盘操作。
      </p>
    </div>
  )
}

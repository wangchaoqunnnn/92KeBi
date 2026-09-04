import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { api } from '../api'
import { Card, Tag, Meter, Empty, Loading, ErrorBox, PctText, ConditionList } from '../components/ui'
import EChart, { TOOLTIP, axisCommon } from '../components/EChart'
import {
  PHASE_META, UP, DOWN, fmt, fmtPct, fmtAmountYi, fmtNum,
} from '../format'

/* ================= 个股分析 U-07 ================= */

const ROLE_META = {
  市场总龙: { cls: 'role-dragon', text: '市场总龙' },
  主线板块: { cls: 'role-main', text: '主线板块' },
  普通: { cls: 'role-normal', text: '普通' },
}
const STRAT_CN = { leader: '龙头战法', buyang: '补涨战法', qiehuan: '切换战法' }
const ST_CLS = { 强: 'tag-strong', 中: 'tag-gray', 警示: 'tag-amber' }

function fitTag(score) {
  const s = Number(score)
  if (!Number.isFinite(s)) return { text: '未评估', cls: 'tag-gray' }
  if (s >= 75) return { text: '高适配', cls: 'tag-ok' }
  if (s >= 60) return { text: '适配', cls: 'tag-amber' }
  if (s >= 40) return { text: '一般', cls: '' }
  return { text: '不适配', cls: 'tag-gray' }
}

function sentMeta(s) {
  const n = Number(s)
  if (!Number.isFinite(n) || n === 0) return { text: '中性', tone: '' }
  if (n > 0) return { text: '利好', tone: 'buy' }
  return { text: '利空', tone: 'sell' }
}

function Kpi({ label, children, sub, wide }) {
  return (
    <div className={`kpi ${wide ? 'kpi-wide' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-val">{children}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  )
}

/* K线 ECharts option */
function buildKOption(kline) {
  const dates = kline.map((b) => b.date)
  const closes = kline.map((b) => Number(b.close))
  const ma = (n) => {
    const out = []
    let sum = 0
    for (let i = 0; i < closes.length; i++) {
      sum += closes[i]
      if (i >= n) sum -= closes[i - n]
      out.push(i >= n - 1 ? +(sum / n).toFixed(3) : '-')
    }
    return out
  }
  const volColors = kline.map((b) => {
    if (b.limit_up) return 'rgba(245,68,75,.95)'
    return Number(b.close) >= Number(b.open) ? 'rgba(245,68,75,.38)' : 'rgba(14,203,129,.38)'
  })
  const xCat = { type: 'category', data: dates, boundaryGap: true, ...axisCommon(true) }
  const xValue = { ...axisCommon(false), scale: true }

  return {
    animation: false,
    legend: {
      data: ['MA5', 'MA10', 'MA20'],
      top: 2, right: 8, itemWidth: 14, itemHeight: 8,
      textStyle: { color: '#8fa3c0', fontSize: 11 },
    },
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'cross', link: [{ xAxisIndex: 'all' }] },
      formatter(ps) {
        const bar = ps && ps[0] && kline[ps[0].dataIndex]
        if (!bar) return ''
        const upBar = Number(bar.close) >= Number(bar.open)
        const pct = Number(bar.pct)
        const pctColor = pct > 0 ? UP : pct < 0 ? DOWN : '#9aa8bf'
        let s = `<div style="font-weight:700;margin-bottom:4px">${bar.date}</div>`
        s += `<div>开 ${fmt(bar.open)}　高 ${fmt(bar.high)}　低 ${fmt(bar.low)}　收 <span style="color:${upBar ? UP : DOWN};font-weight:700">${fmt(bar.close)}</span></div>`
        s += `<div>涨跌 <span style="color:${pctColor};font-weight:600">${fmtPct(pct)}</span>　换手 ${fmt(bar.turnover)}%</div>`
        s += `<div>额 ${fmtAmountYi(bar.amount)}　量 ${fmtNum(bar.volume)}　连板 ${bar.streak || 0}${bar.limit_up ? ' · 今日涨停' : ''}</div>`
        return s
      },
    },
    grid: [
      { left: 58, right: 14, top: 30, height: '56%' },
      { left: 58, right: 14, top: '72%', height: '17%' },
    ],
    xAxis: [
      { ...xCat, gridIndex: 0, axisLabel: { show: false }, axisPointer: { show: false } },
      { ...xCat, gridIndex: 1 },
    ],
    yAxis: [
      { ...xValue, gridIndex: 0 },
      { ...xValue, gridIndex: 1, splitNumber: 2 },
    ],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 }],
    series: [
      {
        name: 'K线', type: 'candlestick', xAxisIndex: 0, yAxisIndex: 0,
        data: kline.map((b) => [Number(b.open), Number(b.close), Number(b.low), Number(b.high)]),
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
      { name: 'MA5', type: 'line', data: ma(5), smooth: true, showSymbol: false, lineStyle: { width: 1.1, color: '#f2c14e' }, itemStyle: { color: '#f2c14e' } },
      { name: 'MA10', type: 'line', data: ma(10), smooth: true, showSymbol: false, lineStyle: { width: 1.1, color: '#4c8dff' }, itemStyle: { color: '#4c8dff' } },
      { name: 'MA20', type: 'line', data: ma(20), smooth: true, showSymbol: false, lineStyle: { width: 1.1, color: '#b07bff' }, itemStyle: { color: '#b07bff' } },
      {
        name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1,
        data: kline.map((b) => Number(b.volume) || 0),
        itemStyle: { color: (p) => volColors[p.dataIndex] || 'rgba(143,163,192,.4)' },
      },
    ],
  }
}

const PAGE_CSS = `
.stock-top { display:flex; align-items:flex-start; justify-content:space-between; gap:14px; flex-wrap:wrap; }
.stock-name { font-size: 26px; margin: 0; font-weight: 800; letter-spacing: .5px; }
.stock-sub { color: var(--muted); font-size: 12.5px; margin-top: 4px; }
.stock-head-right { display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }
.role-dragon { background: rgba(245,68,75,.16); color:#ff6b70; border-color: rgba(245,68,75,.55); }
.role-main { background: rgba(242,193,78,.13); color:#f2c14e; border-color: rgba(242,193,78,.5); }
.role-normal { background: rgba(255,255,255,.05); color: var(--muted); border-color: rgba(255,255,255,.1); }
.kpis { display:flex; gap:10px; flex-wrap:wrap; margin-top:14px; }
.kpi {
  min-width: 92px; background: rgba(255,255,255,.03);
  border: 1px solid var(--border-soft); border-radius: 10px; padding: 8px 12px;
}
.kpi-wide { min-width: 190px; flex: 1; }
.kpi-label { font-size: 11px; color: var(--muted); }
.kpi-val { font-size: 19px; font-weight: 700; font-variant-numeric: tabular-nums; margin: 2px 0 1px; }
.kpi-val .up, .kpi-val .down, .kpi-val .flat { font-size: 21px; font-weight: 800; }
.kpi-sub { font-size: 11px; color: var(--muted2); }
.kpi-val .kpi-suffix { font-size: 12px; color: var(--muted2); font-weight: 400; margin-left: 5px; }
.kpi-heat { display:flex; align-items:center; gap:10px; }
.kpi-heat .meter { flex: 1; }
.col-stack { display:flex; flex-direction:column; gap:14px; min-width:0; }
.stock-sigs { display:flex; flex-direction:column; }
.stock-sig {
  display:grid; grid-template-columns: auto 1fr auto; gap: 8px 12px; align-items:baseline;
  padding: 9px 2px; border-bottom: 1px solid rgba(34,48,74,.45);
}
.stock-sig:last-child { border-bottom: 0; }
.stock-sig .sig-reason { grid-column: 2 / -1; color: var(--muted); font-size: 12px; line-height: 1.6; }
.stock-sig b { font-size: 13.5px; }
.stock-sig .sig-date { color: var(--muted2); font-size: 11.5px; display:flex; align-items:center; gap:6px; }
.strat-head { display:flex; align-items:center; gap: 10px; margin-bottom: 8px; }
.strat-score { font-size: 28px; font-weight: 800; color: #f2c14e; font-variant-numeric: tabular-nums; line-height: 1; }
.strat-score-sub { font-size: 11px; color: var(--muted2); }
.strat-head .bar { flex: 1; min-width: 40px; }
.kw-row { display:flex; gap:6px; flex-wrap:wrap; margin: 10px 0; }
.heat-line { display:flex; align-items:center; gap:10px; font-size:12.5px; color: var(--muted); margin: 8px 0; }
.heat-line b { color: var(--text); font-variant-numeric: tabular-nums; }
.heat-line > .meter { width: 130px; }
.sector-line { display:flex; gap:14px; flex-wrap:wrap; font-size:12.5px; color: var(--muted); margin-top: 8px; }
.news-list { list-style:none; margin:0; padding:0; display:flex; flex-direction:column; }
.news-list li { padding: 9px 2px; border-bottom: 1px solid rgba(34,48,74,.4); }
.news-list li:last-child { border-bottom: 0; }
.news-title { font-size: 13px; color: #dbe5f7; line-height: 1.55; }
.news-meta { display:flex; align-items:center; gap:8px; margin-top: 5px; font-size: 11.5px; color: var(--muted2); }
.tag-gold { background: rgba(242,193,78,.12); color: #f2c14e; border-color: rgba(242,193,78,.5); }
.guide-box { max-width: 660px; margin: 8vh auto 0; text-align:center; }
.guide-box .k { font-size: 40px; }
`

export default function StockPage({ route, params, nav, goStock }) {
  const rawCode = params ? params.get('code') || '' : ''
  const code = useMemo(() => {
    if (!rawCode) return ''
    try {
      return /%[0-9a-fA-F]{2}/.test(rawCode) ? decodeURIComponent(rawCode) : rawCode
    } catch {
      return rawCode
    }
  }, [rawCode])

  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  const load = useCallback(() => {
    if (!code) return
    setErr(null)
    setData(null)
    api.stock(code).then(setData).catch(setErr)
  }, [code])

  useEffect(() => {
    load()
  }, [load])

  // 历史大宗交易(独立加载)
  const [bt, setBt] = useState(null)
  const [btErr, setBtErr] = useState(null)
  useEffect(() => {
    if (!code) return
    let on = true
    setBt(null); setBtErr(null)
    api.stockBlockTrades(code)
      .then((d) => { if (on) setBt(d) })
      .catch((e) => { if (on) setBtErr(e) })
    return () => { on = false }
  }, [code])

  // —— 所有 Hook 必须位于条件返回之前 ——
  const kOption = useMemo(() => {
    const kl = data && data.kline && data.kline.length ? data.kline : null
    return kl ? buildKOption(kl) : null
  }, [data])

  const condsEntries = useMemo(() => {
    const c = (data && data.conds) || {}
    return Object.keys(STRAT_CN)
      .filter((k) => c[k] && Array.isArray(c[k].items))
      .map((k) => [k, STRAT_CN[k], c[k]])
  }, [data])

  const keywords = useMemo(() => {
    const arr = data && Array.isArray(data.sector_keywords) ? [...data.sector_keywords] : []
    const tags = data && data.meta ? data.meta.tags : null
    if (tags) {
      String(tags)
        .split(/[\/,，、]/)
        .map((s) => s.trim())
        .filter(Boolean)
        .forEach((s) => { if (!arr.includes(s)) arr.push(s) })
    }
    return arr
  }, [data])

  // —— 无代码：引导卡 ——
  if (!code) {
    return (
      <div className="page">
        <style>{PAGE_CSS}</style>
        <div className="page-head">
          <div>
            <h2 className="page-title">个股分析</h2>
            <div className="page-sub">U-07 · 龙头 / 补涨 / 切换 三维策略体检 + 日K</div>
          </div>
        </div>
        <div className="card guide-box">
          <div className="card-body">
            <div className="k">🔍</div>
            <h3 style={{ margin: '8px 0' }}>先选择一只股票</h3>
            <p className="muted">
              在顶部搜索框输入<strong>代码或名称</strong>（如「幻视影视」），
              回车或点击下拉结果即可进入本页。
            </p>
            <p className="muted small">
              可查看：日K/成交量、触发信号、龙头/补涨/切换三策略条件打分、题材逻辑与新闻情绪。
            </p>
          </div>
        </div>
      </div>
    )
  }

  if (err) {
    return (
      <div className="page">
        <style>{PAGE_CSS}</style>
        <ErrorBox error={err} onRetry={load} />
      </div>
    )
  }
  if (!data) return <Loading text={`正在加载 ${code} …`} />

  /* —— 数据整理 —— */
  const d = data
  const m = d.meta || {}
  const feat = d.feat || {}
  const kline = d.kline && d.kline.length ? d.kline : null
  const todayBar = kline ? kline[kline.length - 1] : null
  const isToday = !!(todayBar && d.date && todayBar.date === d.date)
  const featToday = feat.today || (d.conds && d.conds.feat && d.conds.feat.today) || null
  const todayPct =
    featToday && featToday.pct != null
      ? Number(featToday.pct)
      : isToday && todayBar && todayBar.pct != null
        ? Number(todayBar.pct)
        : null
  const todayTurn =
    featToday && featToday.turnover != null
      ? Number(featToday.turnover)
      : isToday && todayBar && todayBar.turnover != null
        ? Number(todayBar.turnover)
        : null
  const price = feat.close != null ? feat.close : todayBar ? todayBar.close : null
  const role = ROLE_META[d.role] || { cls: 'role-normal', text: d.role || '普通' }
  const ph = d.phase && PHASE_META[d.phase] ? PHASE_META[d.phase] : null
  const phText = ph ? ph.label : d.phase || '—'
  const phColor = ph ? ph.color : '#8fa3c0'
  const heatColor = d.sector_heat >= 70 ? '#f2c14e' : d.sector_heat >= 40 ? '#4c8dff' : '#8fa3c0'
  const sigs = d.signals || []
  const news = (d.news || []).slice(0, 10)
  const nBuy = sigs.filter((s) => s.dir === 'buy').length
  const nSell = sigs.filter((s) => s.dir === 'sell').length
  const nWatch = sigs.filter((s) => s.dir === 'watch').length

  return (
    <div className="page">
      <style>{PAGE_CSS}</style>

      {/* 1) 概览卡 */}
      <Card>
        <div className="stock-top">
          <div>
            <h2 className="stock-name">{m.name || '—'}</h2>
            <div className="stock-sub">
              {m.code} · {m.sector || '—'} · {m.market || '—'}
            </div>
          </div>
          <div className="stock-head-right">
            <span className={`tag ${role.cls}`}>{role.text}</span>
            <span
              className="tag"
              style={{ color: phColor, borderColor: `${phColor}88`, background: `${phColor}1f` }}
            >
              阶段：{phText}
            </span>
            <Tag cls="tag-gray">{d.date || '—'}</Tag>
          </div>
        </div>

        <div className="kpis">
          <Kpi label="现价">
            <span className="num">{price != null ? fmt(price) : '—'}</span>
          </Kpi>
          <Kpi label="今日涨跌" sub={todayPct == null ? '今日暂无成交' : undefined}>
            {todayPct != null ? <PctText value={todayPct} /> : <span className="muted2">—</span>}
          </Kpi>
          <Kpi label="连板高度" sub="20日内最高连板">
            <span className="num">{feat.streak_max20 != null ? `${fmt(feat.streak_max20, 0)} 板` : '—'}</span>
            {todayBar && todayBar.limit_up ? <Tag tone="buy">今日涨停</Tag> : null}
          </Kpi>
          <Kpi label="换手率" sub={todayTurn != null ? `今日 ${fmt(todayTurn)}%` : '（无今日成交）'}>
            <span className="num">{feat.turnover_avg5 != null ? fmt(feat.turnover_avg5) : '—'}</span>
            <span className="kpi-suffix">5日均%</span>
          </Kpi>
          <Kpi label="流通市值">
            <span className="num">
              {feat.float_cap != null
                ? fmtAmountYi(feat.float_cap)
                : m.float_cap != null
                  ? fmtAmountYi(m.float_cap)
                  : '—'}
            </span>
          </Kpi>
          <Kpi label="市盈率 PE">
            <span className="num">{m.pe != null ? fmt(m.pe, 1) : '—'}</span>
          </Kpi>
          <Kpi label="板块热度" wide>
            <div className="kpi-heat">
              <b className="num">{d.sector_heat != null ? Math.round(d.sector_heat) : '—'}</b>
              <Meter value={d.sector_heat || 0} color={heatColor} />
            </div>
          </Kpi>
        </div>
      </Card>

      {/* 1.5) 当日资金卡: 量能/暗盘(主动净买)/主力净流入 */}
      <Card
        title="当日资金 · 量能 & 净流入"
        extra={<Tag cls="tag-gray">绝对额口径(亿元)· 主动净买=外盘−内盘</Tag>}
      >
        {!d.flow ? (
          <Empty text="实盘模式提供该数据（新浪资金流/腾讯盘口）；当前为模拟或无数据。" />
        ) : (
          <div className="kpis">
            <Kpi label="当日量能" wide>
              {(() => {
                const v = d.flow.volume
                if (!v) return <span className="flat">—</span>
                const cls = v.state === '放量' ? 'up' : v.state === '缩量' ? 'down' : 'flat'
                return (
                  <span>
                    <span className={`num ${cls}`}>{fmt(v.today_yi)}亿</span>
                    <span className="muted2 small" style={{ marginLeft: 8 }}>折算全日 {fmt(v.whole_day_yi)}亿 · 近5日均 {fmt(v.avg5_yi)}亿</span>
                  </span>
                )
              })()}
              <div className="kpi-sub">
                {(() => {
                  const v = d.flow.volume
                  return v ? `全日折算为近5日均的 ${fmt(v.times, 2)} 倍 → ${v.state}` : '—'
                })()}
              </div>
            </Kpi>
            <Kpi label="暗盘净流入 · 主动净买(外盘−内盘)">
              {(() => {
                const a = d.flow.active_net
                if (!a) return <span className="flat">—</span>
                const cls = a.diff_yi > 0 ? 'up' : 'down'
                return <span className={cls}>{a.diff_yi > 0 ? '+' : ''}{fmt(a.diff_yi, 3)}亿</span>
              })()}
              <div className="kpi-sub">
                {(() => {
                  const a = d.flow.active_net
                  return a ? `外盘 ${fmt((a.outer || 0) / 1e4, 0)}万手 / 内盘 ${fmt((a.inner || 0) / 1e4, 0)}万手 · ${a.source}` : '盘口数据获取中…'
                })()}
              </div>
            </Kpi>
            <Kpi label="主力净流入(最近交易日)">
              {(() => {
                const m2 = d.flow.main_net
                if (!m2) return <span className="flat">—</span>
                const cls = m2.net_yi > 0 ? 'up' : m2.net_yi < 0 ? 'down' : 'flat'
                return <span className={cls}>{m2.net_yi > 0 ? '+' : ''}{fmt(m2.net_yi)}亿</span>
              })()}
              <div className="kpi-sub">
                {(() => {
                  const m2 = d.flow.main_net
                  if (!m2) return '—'
                  return `数据日 ${m2.date} · 超大单 ${m2.super_yi != null ? (m2.super_yi > 0 ? '+' : '') + fmt(m2.super_yi) + '亿' : '—'} · ${m2.source}`
                })()}
              </div>
            </Kpi>
          </div>
        )}
      </Card>

      {/* 1.6) 历史大宗交易统计(近1年) */}
      <Card
        title="历史大宗交易统计"
        extra={<Tag cls="tag-gray">近1年(滚动365日)全部 · 按日聚合 · 东财数据中心</Tag>}
      >
        {btErr ? (
          <div className="error-box" style={{ padding: 14 }}>{String(btErr?.message || btErr)}</div>
        ) : !bt ? (
          <div className="loading" style={{ padding: 14 }}><span className="spin" />加载大宗交易…</div>
        ) : (!bt.rows || bt.rows.length === 0) ? (
          <Empty text={bt.note || '近期无大宗交易记录'} />
        ) : (
          <>
            <div className="kpis">
              <Kpi label={`统计区间 ${bt.stats?.date_from || '—'} ~ ${bt.stats?.date_to || '—'}`}>
                <span className="num">{bt.stats?.n ?? '—'} 个交易日</span>
                <div className="kpi-sub">近 {bt.rows.length} 条记录(按日)</div>
              </Kpi>
              <Kpi label="大宗成交总额">
                <span className="num">{bt.stats?.total_amt_yi != null ? `${fmt(bt.stats.total_amt_yi, 2)}亿` : '—'}</span>
                <div className="kpi-sub">最大单日 {bt.stats?.max_amount_yi != null ? `${fmt(bt.stats.max_amount_yi, 2)}亿` : '—'}</div>
              </Kpi>
              <Kpi label="平均折/溢价">
                <span className={bt.stats?.avg_premium_pct != null ? (bt.stats.avg_premium_pct <= 0 ? 'down' : 'up') : 'flat'}>
                  {bt.stats?.avg_premium_pct != null ? `${fmt(bt.stats.avg_premium_pct, 2)}%` : '—'}
                </span>
                <div className="kpi-sub">负值为折价成交</div>
              </Kpi>
              <Kpi label="折价成交占比">
                <span className="num">{bt.stats?.discount_ratio != null ? `${fmt(bt.stats.discount_ratio, 1)}%` : '—'}</span>
                <div className="kpi-sub">折价 {bt.stats?.discount_n ?? '—'} 个交易日</div>
              </Kpi>
            </div>
            <div className="table-wrap" style={{ marginTop: 8, maxHeight: 360, overflow: 'auto' }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>日期</th><th>笔数</th><th>成交量(万股)</th><th>成交均价</th>
                    <th>收盘</th><th>折/溢价率</th><th>成交额(亿元)</th><th>成交后5日%</th>
                  </tr>
                </thead>
                <tbody>
                  {bt.rows.map((r, i) => (
                    <tr key={`${r.date}-${i}`}>
                      <td className="num">{r.date}</td>
                      <td className="num">{r.deal_num ?? '—'}</td>
                      <td className="num">{r.volume_wan != null ? fmt(r.volume_wan, 1) : '—'}</td>
                      <td className="num">{r.avg_price != null ? fmt(r.avg_price, 2) : '—'}</td>
                      <td className="num">{r.close != null ? fmt(r.close, 2) : '—'}</td>
                      <td>
                        {r.premium_pct != null ? (
                          <span className={r.premium_pct <= 0 ? 'down' : 'up'}>
                            {r.premium_pct > 0 ? '+' : ''}{fmt(r.premium_pct, 2)}%
                          </span>
                        ) : '—'}
                      </td>
                      <td className="num">{r.amount_wan != null ? fmt(r.amount_wan / 1e4, 3) : '—'}</td>
                      <td>{r.chg_after5 != null ? <PctText value={r.chg_after5} /> : '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="muted2 small" style={{ marginTop: 6 }}>
              统计口径：近1年（滚动365日）内该股全部大宗交易（共 {bt.rows.length} 个交易日记录，按日聚合）；大宗在收盘后披露；负折/溢价表示相对当日收盘价的成交折让。
            </div>
          </>
        )}
      </Card>

      {/* 2) 左侧主要 + 右侧窄列 */}
      <div className="grid-28">
        <div className="col-stack">
          {/* K线卡 */}
          <Card title="日K · 近300根" extra={<Tag cls="tag-gray">红涨绿跌 · 副图成交量</Tag>}>
            {kOption ? <EChart option={kOption} height={340} /> : <Empty text="暂无K线数据" />}
          </Card>

          {/* 信号卡 */}
          <Card
            title="触发信号"
            extra={
              <Tag cls="tag-gray">
                买 {nBuy} · 卖 {nSell} · 察 {nWatch}
              </Tag>
            }
          >
            {sigs.length === 0 ? (
              <Empty text="当前无触发信号" />
            ) : (
              <div className="stock-sigs">
                {sigs.map((s, i) => (
                  <div className="stock-sig" key={`${s.signal}-${s.date}-${i}`}>
                    {s.dir === 'buy' || s.dir === 'sell' || s.dir === 'watch' ? (
                      <Tag tone={s.dir}>{s.dir_cn || s.dir}</Tag>
                    ) : (
                      <Tag cls="tag-gray">{s.dir_cn || s.dir || '—'}</Tag>
                    )}
                    <b>{s.signal || '—'}</b>
                    <span className="sig-date">
                      {s.strength ? <Tag cls={ST_CLS[s.strength] || 'tag-gray'}>{s.strength}</Tag> : null}
                      <span className="muted2">{s.strategy_cn || ''} · {s.date || ''}</span>
                    </span>
                    <div className="sig-reason">{s.reason || '—'}</div>
                  </div>
                ))}
              </div>
            )}
          </Card>

          {/* 三套策略条件卡 */}
          {condsEntries.length > 0 ? (
            <div className="grid3">
              {condsEntries.map(([key, cn, c]) => {
                const fit = fitTag(c.score)
                return (
                  <Card key={key} title={cn} extra={<Tag cls={fit.cls}>{fit.text}</Tag>}>
                    <div className="strat-head">
                      <span className="strat-score">{c.score != null ? fmt(c.score, 1) : '—'}</span>
                      <span className="strat-score-sub">满分100</span>
                      <div className="bar">
                        <Meter value={c.score || 0} color={c.score >= 60 ? '#f2c14e' : '#5f7598'} />
                      </div>
                    </div>
                    <ConditionList items={c.items} dense />
                  </Card>
                )
              })}
            </div>
          ) : (
            <Card title="策略条件打分">
              <Empty text="暂无策略条件数据" />
            </Card>
          )}
        </div>

        {/* 右侧：题材 + 新闻 */}
        <div className="col-stack">
          <Card title="题材逻辑">
            <div className="muted small">所属板块：{m.sector || '—'}</div>
            {keywords.length > 0 && (
              <div className="kw-row">
                {keywords.slice(0, 12).map((w) => (
                  <Tag key={w} cls="tag-gray">{w}</Tag>
                ))}
              </div>
            )}
            <div>
              {d.sector_policy ? (
                <span className="tag tag-gold">国家级政策 / 硬逻辑</span>
              ) : (
                <Tag cls="tag-gray">主题催化型</Tag>
              )}
            </div>
            <div className="heat-line">
              <span>板块热度</span>
              <b className="num">{d.sector_heat != null ? Math.round(d.sector_heat) : '—'}</b>
              <Meter value={d.sector_heat || 0} color={heatColor} height={5} />
            </div>
            <div className="sector-line">
              <span>
                板块今日{' '}
                {feat.sector_avg_pct != null ? <PctText value={feat.sector_avg_pct} /> : '—'}
              </span>
              <span>5日涨停 {feat.sector_zt_5d ?? '—'}</span>
              <span>今日涨停 {feat.sector_zt_today ?? '—'}</span>
            </div>
          </Card>

          <Card
            title="相关新闻"
            extra={<Tag cls="tag-gray">个股 + 板块 · {news.length} 条</Tag>}
          >
            {news.length === 0 ? (
              <Empty text="暂无相关新闻" />
            ) : (
              <ul className="news-list">
                {news.map((n) => {
                  const sm = sentMeta(n.sentiment)
                  return (
                    <li key={n.id != null ? n.id : `${n.date}-${n.title}`}>
                      <div className="news-title">{n.title}</div>
                      <div className="news-meta">
                        <span>{n.date}</span>
                        <span>·</span>
                        <span>{n.source}</span>
                        {n.kind ? <span>· {n.kind}</span> : null}
                        {sm.tone ? <Tag tone={sm.tone}>{sm.text}</Tag> : <Tag cls="tag-gray">{sm.text}</Tag>}
                      </div>
                    </li>
                  )
                })}
              </ul>
            )}
          </Card>
        </div>
      </div>

      <p className="muted small" style={{ margin: 0 }}>
        ⚠️ 本页评分与信号由规则引擎自动计算，仅供学习研究，不构成投资建议；
        实操前请结合真实盘面、新闻与风控纪律人工复核。
      </p>
    </div>
  )
}

import { useState, useEffect, useMemo, useCallback, useRef } from 'react'
import { api } from '../api'
import { Card, Stat, Tag, Meter, Empty, Loading, ErrorBox, PctText, useTableSort, sortRows, SortTh } from '../components/ui'
import EChart, { TOOLTIP, axisCommon, AXIS_TEXT, GRID_LINE, SPLIT_LINE } from '../components/EChart'
import { PHASE_META, PHASE_KEYS, UP, DOWN, fmt, fmtPct, fmtAmountYi } from '../format'

const TXT = '#cdd9f0'
const YELLOW = '#f2c14e'
const BLUE = '#4c8dff'

/* 实时异动小表：独立轮询，避免带动整页重绘 */
function LiveTicker({ goStock }) {
  const [rows, setRows] = useState(null)
  const [err, setErr] = useState(null)
  const [ts, setTs] = useState(null)

  const load = useCallback(() => {
    api.live()
      .then((d) => {
        setRows(d && d.rows ? d.rows : [])
        setTs(d ? d.tick_ts || d.date : null)
        setErr(null)
      })
      .catch((e) => setErr(e))
  }, [])

  useEffect(() => {
    load()
    const t = setInterval(load, 6000)
    return () => clearInterval(t)
  }, [load])

  return (
    <div className="card dash-live">
      <div className="card-head">
        <h3 className="card-title">实时异动 · 涨幅榜 <span className="dash-live-tag">盘中</span></h3>
        <div className="card-extra">
          {ts && <span className="muted">更新于 {ts} · 每 8~10 秒自动刷新</span>}
          {err && <span className="dash-live-err">连接失败，自动重试中</span>}
        </div>
      </div>
      <div className="card-body">
        {!rows ? (
          <div className="dash-live-loading"><Loading text="实时数据加载中…" /></div>
        ) : rows.length === 0 ? (
          <div className="dash-live-empty">暂无实时异动数据（行情快照为空）</div>
        ) : (
          <div className="dash-live-scroll">
            {rows.slice(0, 12).map((r, i) => (
              <div
                key={r.code || i}
                className="dash-live-item"
                title={`${r.name} ${r.code} · 现价 ${fmt(r.price)} · 高 ${fmt(r.high)} / 低 ${fmt(r.low)} · 昨收 ${fmt(r.pre_close)}`}
                onClick={() => goStock(r.code)}
              >
                <span className="dash-live-rank">{i + 1}</span>
                <b>{r.name}</b>
                <span className="num muted">{fmt(r.price)}</span>
                <PctText value={r.pct} />
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

/* ---------- 情绪温度计 gauge ---------- */
function gaugeOption(conf, color) {
  const v = Math.round((Number(conf) || 0) * 100)
  return {
    series: [
      {
        type: 'gauge',
        center: ['50%', '70%'],
        radius: '105%',
        startAngle: 180,
        endAngle: 0,
        min: 0,
        max: 100,
        progress: { show: true, width: 13, roundCap: true, itemStyle: { color } },
        axisLine: { lineStyle: { width: 13, color: [[1, 'rgba(255,255,255,0.08)']] } },
        axisTick: { show: false },
        splitLine: { show: false },
        axisLabel: {
          distance: 20,
          color: AXIS_TEXT,
          fontSize: 10,
          formatter: (val) => (val === 0 || val === 50 || val === 100 ? `${val}` : ''),
        },
        pointer: { show: true, length: '58%', width: 4, itemStyle: { color } },
        anchor: { show: false },
        detail: {
          valueAnimation: true,
          offsetCenter: [0, '52%'],
          formatter: '{value}%',
          color: '#e9effc',
          fontSize: 24,
          fontWeight: 700,
        },
        data: [{ value: v }],
      },
    ],
  }
}

/* ---------- 情绪面图 ---------- */
function emotionOption(hist) {
  const dates = hist.map((r) => r.date.slice(5))
  return {
    color: [UP, DOWN, YELLOW],
    tooltip: { ...TOOLTIP, trigger: 'axis' },
    legend: { data: ['涨停', '跌停', '最高连板'], top: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: TXT } },
    grid: { left: 6, right: 8, top: 36, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      ...axisCommon(true),
      axisLabel: { color: AXIS_TEXT, fontSize: 11, interval: 'auto', hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value', name: '家数', minInterval: 1,
        ...axisCommon(false),
        nameTextStyle: { color: AXIS_TEXT, fontSize: 10 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 },
      },
      {
        type: 'value', name: '连板', minInterval: 1,
        ...axisCommon(false),
        splitLine: { show: false },
        nameTextStyle: { color: AXIS_TEXT, fontSize: 10 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 },
      },
    ],
    series: [
      {
        name: '涨停', type: 'bar', barMaxWidth: 8,
        data: hist.map((r) => r.zt),
        itemStyle: { color: UP, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: '跌停', type: 'bar', barMaxWidth: 8,
        data: hist.map((r) => r.dt),
        itemStyle: { color: DOWN, borderRadius: [3, 3, 0, 0] },
      },
      {
        name: '最高连板', type: 'line', yAxisIndex: 1,
        data: hist.map((r) => r.max_streak),
        symbol: 'circle', symbolSize: 5, smooth: false,
        lineStyle: { color: YELLOW, width: 2 },
        itemStyle: { color: YELLOW },
      },
    ],
  }
}

/* ---------- 赚钱效应图 ---------- */
function profitOption(hist) {
  const dates = hist.map((r) => r.date.slice(5))
  return {
    color: [YELLOW, BLUE],
    tooltip: { ...TOOLTIP, trigger: 'axis' },
    legend: { data: ['昨涨停今表现%', '炸板率%'], top: 0, itemWidth: 12, itemHeight: 8, textStyle: { color: TXT } },
    grid: { left: 6, right: 8, top: 36, bottom: 4, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      ...axisCommon(true),
      axisLabel: { color: AXIS_TEXT, fontSize: 11, interval: 'auto', hideOverlap: true },
    },
    yAxis: [
      {
        type: 'value', name: '均表现%',
        ...axisCommon(false),
        nameTextStyle: { color: AXIS_TEXT, fontSize: 10 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 },
      },
      {
        type: 'value', name: '炸板率%',
        ...axisCommon(false),
        splitLine: { show: false },
        nameTextStyle: { color: AXIS_TEXT, fontSize: 10 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 },
      },
    ],
    series: [
      {
        name: '昨涨停今表现%', type: 'line',
        data: hist.map((r) => (r.premium === null || r.premium === undefined ? undefined : Number(r.premium))),
        connectNulls: false,
        symbol: 'circle', symbolSize: 4, smooth: false,
        lineStyle: { color: YELLOW, width: 2 },
        itemStyle: { color: YELLOW },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: 'rgba(143,163,192,0.45)', type: 'dashed' },
          label: { show: false },
          data: [{ yAxis: 0 }],
        },
      },
      {
        name: '炸板率%', type: 'line', yAxisIndex: 1,
        data: hist.map((r) => r.explosion),
        symbol: 'circle', symbolSize: 4, smooth: false,
        lineStyle: { color: BLUE, width: 2 },
        itemStyle: { color: BLUE },
      },
    ],
  }
}

/* ================================================================
 * 情绪周期时间轴: 把连续同阶段交易日合并为“阶段段”, 横向渐变条 + 起止标注
 */
function mergePhaseBlocks(hist) {
  const out = []
  for (const r of hist) {
    const last = out[out.length - 1]
    if (last && last.phase === r.phase) {
      last.to = r.date
      last.n += 1
      last.zt += r.zt || 0
      last.dt += r.dt || 0
    } else {
      out.push({ phase: r.phase, from: r.date, to: r.date, n: 1, zt: r.zt || 0, dt: r.dt || 0 })
    }
  }
  const total = out.reduce((s, b) => s + b.n, 0) || 1
  out.forEach((b) => { b.w = Math.round((b.n / total) * 1000) / 10 })
  return out
}

/* ================================================================
 * 情绪阶段时间轴(同“情绪面”图表做法): x=交易日, y=阶段档位阶梯线,
 * 每日期货色散点着色, 今日竖线标记; 悬停查看当日阶段/涨跌停统计
 */
const PHASE_LEVEL = { main_decline: 0, probe: 1, high_oscillate: 2, main_ascend: 3 }
const PHASE_LEVEL_CN = ['主跌阶段', '低位震荡/试错期', '高位震荡', '主升阶段']

function phaseLadderOption(hist) {
  const dates = hist.map((r) => r.date)
  const levels = hist.map((r) => PHASE_LEVEL[r.phase] != null ? PHASE_LEVEL[r.phase] : 0)
  const colorBy = hist.map((r) => (PHASE_META[r.phase] || { color: BLUE }).color)
  return {
    color: ['#cdd9f0'],
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { color: '#0d1421', backgroundColor: AXIS_TEXT } },
      formatter: (ps) => {
        if (!ps || !ps.length) return ''
        const i = ps[0].dataIndex
        const r = hist[i]
        if (!r) return ''
        const m = PHASE_META[r.phase] || { label: r.phase, color: BLUE }
        return `<div style="font-weight:700;margin-bottom:3px;">${r.date}</div>
          <div>阶段 <b style="color:${m.color}">${m.label}</b></div>
          <div style="margin-top:3px;">涨停 ${r.zt} / 跌停 ${r.dt} · 最高 ${r.max_streak} 板 · 炸板率 ${r.explosion ?? '—'}%</div>`
      },
    },
    legend: { show: false },
    grid: { left: 8, right: 14, top: 14, bottom: 8, containLabel: true },
    xAxis: {
      type: 'category', data: dates,
      ...axisCommon(true),
      axisLabel: { color: AXIS_TEXT, fontSize: 10, interval: 'auto', hideOverlap: true },
    },
    yAxis: {
      type: 'category', data: PHASE_LEVEL_CN,
      ...axisCommon(false),
      splitLine: { show: false },
      axisLabel: { color: AXIS_TEXT, fontSize: 11 },
    },
    series: [
      {
        name: '情绪阶段',
        type: 'line',
        step: 'middle',
        data: levels,
        symbol: 'circle',
        symbolSize: 5,
        lineStyle: { color: '#9fb2d4', width: 2 },
        itemStyle: { color: '#cdd9f0' },
        markLine: {
          silent: true, symbol: 'none',
          lineStyle: { color: 'rgba(242,193,78,.85)', width: 2 },
          label: { show: true, position: 'insideEndTop', color: '#f2c14e', fontSize: 10, formatter: '今日' },
          data: [{ xAxis: dates[dates.length - 1] }],
        },
      },
      {
        name: '阶段着色',
        type: 'scatter',
        data: levels.map((v, i) => ({ value: v, itemStyle: { color: colorBy[i], opacity: 0.95 } })),
        symbolSize: 9,
        tooltip: { show: false },
      },
    ],
  }
}

function PhaseTimeline({ hist }) {
  const blocks = useMemo(() => mergePhaseBlocks(hist || []), [hist])
  if (!blocks.length) return null
  const today = hist[hist.length - 1] && hist[hist.length - 1].date
  return (
    <div className="dash-tl2">
      <div className="dash-tl2-track">
        {blocks.map((b, i) => {
          const m = PHASE_META[b.phase] || { label: b.phase, color: BLUE, short: b.phase }
          const last = blocks.length - 1 === i
          return (
            <div
              key={i}
              className={`dash-tl2-seg ${last ? 'now' : ''}`}
              style={{ flexGrow: b.w, background: `linear-gradient(90deg, ${m.color}cc, ${m.color}66)` }}
              title={`${b.phase === 'main_ascend' ? '主升' : b.phase === 'main_decline' ? '主跌' : b.phase === 'probe' ? '试错' : '高位震荡'} · ${b.from} ~ ${b.to} (${b.n}日, 平均涨停${(b.zt / b.n).toFixed(1)}/跌停${(b.dt / b.n).toFixed(1)})`}
            >
              <span className="dash-tl2-label">{m.short || m.label}</span>
            </div>
          )
        })}
      </div>
      <div className="dash-tl2-line">
        {blocks.map((b, i) => (
          <span key={i} className="dash-tl2-pt">
            {blocks.length > 6 ? (i === 0 || i === blocks.length - 1 ? b.from : '') : b.from}
          </span>
        ))}
        <span className="dash-tl2-now">← 今日 {today}</span>
      </div>
      <div className="dash-tl2-seq">
        {blocks.map((b, i) => {
          const m = PHASE_META[b.phase] || { label: b.phase }
          return (
            <span key={i} className="dash-tl2-seqitem">
              <i style={{ background: m.color }} />
              <b>{m.label}</b> {b.from.replace(/\d{4}-/, '')} ~ {b.to.replace(/\d{4}-/, '')}（{b.n}日）
            </span>
          )
        })}
      </div>
      <div className="dash-tl2-legend">
        {PHASE_KEYS.map((k) => (
          <span key={k}><i style={{ background: PHASE_META[k].color }} />{PHASE_META[k].label}</span>
        ))}
      </div>
    </div>
  )
}

/* 板块涨停股的分层标签配色 */
function ztRoleTone(role) {
  if (role === '板块龙头') return 'buy'
  if (role && role.includes('卡位')) return 'watch'
  if (role && role.includes('跟风')) return 'watch'
  if (role === '首板领涨') return 'buy'
  return 'gray'
}

function VolCell({ s }) {
  const today = s && s.amount != null ? Number(s.amount) : null
  const prev = s && s.vol_prev_yi != null ? Number(s.vol_prev_yi) : null
  if (today === null || prev === null) {
    return <span className="muted2" title="分时档案自今日开始积累，次日(明日)起可对比昨日同时段">—*</span>
  }
  const d = today - prev
  const cls = d > 0 ? 'up' : d < 0 ? 'down' : 'flat'
  return (
    <span className={`num ${cls}`} style={{ fontWeight: 600 }} title={`今日 ${fmt(today, 1)}亿 · 昨同期 ${fmt(prev, 1)}亿`}>
      {d >= 0 ? '多' : '少'}{Math.abs(d).toFixed(1)}亿
    </span>
  )
}

/* 板块行：点击展开当日涨停股分层(龙头/补涨/跟风) + 量能列 */
function SectorRow({ s, rank, open, busy, err, detail, onToggle, goStock }) {
  const roleCls = (role) => {
    if (!role) return 'role-gray'
    if (role.includes('龙头')) return 'role-dragon'
    if (role.includes('卡位') || role.includes('跟风')) return 'role-follow'
    if (role.includes('首板')) return 'role-first'
    return 'role-gray'
  }
  return (
    <>
      <tr className={`dash-sec-row ${open ? 'open' : ''}`} onClick={onToggle}>
        <td className="num muted2" style={{ width: 44 }}>{rank != null ? rank : '—'}</td>
        <td>
          <span style={{ fontWeight: 600 }}>{s.sector}</span>
          {s.is_dragon_sector && <Tag cls="tag-main">主线</Tag>}
        </td>
        <td><PctText value={s.avg_pct} /></td>
        <td className="num" style={{ color: (s.zt_today || 0) > 0 ? UP : undefined, fontWeight: (s.zt_today || 0) > 0 ? 700 : 400 }}>
          {s.zt_today ?? '—'}
        </td>
        <td>{s.avg5_pct != null ? <PctText value={s.avg5_pct} /> : <span className="muted2">—</span>}</td>
        <td>
          {s.streak_dir === 'up' && s.streak_n > 0 ? (
            <span className="up num" style={{ fontWeight: 700 }}>连涨{s.streak_n}日</span>
          ) : s.streak_dir === 'down' && s.streak_n > 0 ? (
            <span className="down num" style={{ fontWeight: 700 }}>连跌{s.streak_n}日</span>
          ) : (
            <span className="muted2">—</span>
          )}
        </td>
        <td className="num">{fmtAmountYi(s.amount)}</td>
        <td><VolCell s={s} /></td>
        <td><span className="caret" style={{ transform: open ? 'rotate(180deg)' : 'none' }}>▾</span></td>
      </tr>
      {open && (
        <tr className="sec-detail-row">
          <td colSpan={9}>
            {busy ? (
              <div className="loading" style={{ padding: 14 }}><span className="spin" />加载涨停明细…</div>
            ) : err ? (
              <div className="error-box" style={{ padding: 14 }}>{err}</div>
            ) : !detail ? null : (
              <div className="sec-detail">
                {detail.anchors && detail.anchors.length > 0 && detail.anchors.map((a, i) => (
                  <div className="sec-anchor" key={i}>
                    <span>🏁 板块高标</span>
                    <b><span className="link" onClick={() => goStock(a.code)}>{a.name}</span> {a.code}</b>
                    <span>{a.max_streak}板 · 最近 {a.last_date}</span>
                    {a.zt_today ? <span className="up">今日涨停续板</span> : <span className="muted">今日未封板（断板/歇整，倒下=板块退潮）</span>}
                  </div>
                ))}
                {!detail.items || detail.items.length === 0 ? (
                  <Empty text={`${s.sector} 今日无涨停`} />
                ) : (
                  <>
                    <div className="sec-zt-head">今日涨停 {detail.items.length} 只 · 按高度排序
                      {detail.leader ? <> · 今日最高板：<b className="up">{detail.leader.name}（{detail.leader.streak}板）</b></> : null}
                    </div>
                    {detail.items.map((it) => (
                      <div className="zt-item" key={it.code}>
                        <span className={`role-chip ${roleCls(it.role)}`}>{it.role}</span>
                        <span className="nm"><span className="link" onClick={() => goStock(it.code)}>{it.name}</span> {it.code}</span>
                        <span className="num">{it.streak > 0 ? `${it.streak}板` : '—'}</span>
                        <span className="num">{fmtAmountYi(it.amount_yi)}</span>
                        <PctText value={it.pct} />
                        <span className="note" title={it.note}>{it.note}</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  )
}

/* ================================================================ */
/* 板块异动看板 option: 上证5分钟(收盘线 + 分时量能柱) + 指数异动时段板块标记 */
const MV_UP = '#ff5b6b'      // A股涨=红
const MV_DOWN = '#22c993'    // 跌=绿
const MV_IDX = '#ffd666'
const MV_BAR = 'rgba(91,140,255,.55)'

function _ts(d, t) {
  return new Date(`${d} ${t}:00`).getTime()
}
function sectorMoveOption(d) {
  const buckets = d.buckets || []
  const moves = d.moves || []
  const day = d.date
  const idxData = buckets.map((b) => [_ts(day, b.t), b.close])
  const barData = buckets.map((b) => [_ts(day, b.t), b.yi])
  const upMoves = moves.filter((m) => m.dir === 'up').map((m) => ({
    value: [_ts(day, m.t), m.close], m,
  }))
  const downMoves = moves.filter((m) => m.dir !== 'up').map((m) => ({
    value: [_ts(day, m.t), m.close], m,
  }))
  const minT = _ts(day, '09:15')
  const maxT = _ts(day, '15:00')
  const fmtMoves = (sectors) => (sectors || [])
    .map((s) => `${s.sector} ${s.pct > 0 ? '+' : ''}${fmt(s.pct, 2)}% · ${s.delta_yi}亿`)
    .join('\n')
  return {
    backgroundColor: 'transparent',
    color: [MV_BAR, MV_IDX, MV_UP, MV_DOWN],
    tooltip: {
      ...TOOLTIP, trigger: 'item', confine: true,
      formatter: (p) => {
        const t = new Date(p.value[0] || p.data[0])
        const hh = `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
        if (p.seriesType === 'line') return `<b>${hh}</b><br/>上证指数 ${fmt(p.value[1])}`
        if (p.seriesType === 'bar') return `<b>${hh}</b><br/>上证5分钟成交额 ${fmt(p.value[1], 1)} 亿`
        const mm = p.data.m || {}
        const head = `<b>${hh}</b> 指数${mm.move_pct >= 0 ? '+' : ''}${fmt(mm.move_pct, 2)}%` +
          (mm.dir === 'up' ? ' <span style="color:#ff5b6b">▲ 带动上涨</span>' : ' <span style="color:#22c993">▼ 拖累下跌</span>')
        const secs = (mm.sectors || []).map((s) =>
          `<span style="color:${s.pct > 0 ? '#ff8a92' : '#3edca9'}">${s.sector} ${s.pct > 0 ? '+' : ''}${fmt(s.pct, 2)}%</span> 区间增量 ${s.delta_yi}亿`).join('<br/>')
        return `${head}<br/>${secs || '—'}`
      },
    },
    legend: {
      top: 0, right: 8, textStyle: { color: AXIS_TEXT, fontSize: 11 },
      data: ['上证指数', '带动上涨板块', '拖累下跌板块'],
    },
    grid: { left: 52, right: 52, top: 30, bottom: 42 },
    xAxis: {
      type: 'time', min: minT, max: maxT,
      axisLabel: {
        color: AXIS_TEXT, fontSize: 11,
        formatter: (v) => {
          const t = new Date(v)
          return `${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`
        },
      },
      axisLine: { lineStyle: { color: GRID_LINE } },
      axisTick: { show: false },
      splitLine: { show: false },
    },
    yAxis: [
      { type: 'value', name: '异动统计·成交额(亿)', nameTextStyle: { color: AXIS_TEXT, fontSize: 11 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 }, splitLine: SPLIT_LINE },
      { type: 'value', name: '上证指数', nameTextStyle: { color: AXIS_TEXT, fontSize: 11 },
        axisLabel: { color: AXIS_TEXT, fontSize: 11 }, splitLine: { show: false },
        scale: true, min: 'dataMin' },
    ],
    dataZoom: [
      { type: 'inside', start: 0, end: 100 },
      { type: 'slider', height: 14, bottom: 8, start: 0, end: 100,
        borderColor: 'transparent', backgroundColor: 'rgba(255,255,255,.05)',
        fillerColor: 'rgba(91,140,255,.25)', handleStyle: { color: '#5b8cff' },
        textStyle: { color: AXIS_TEXT, fontSize: 10 } },
    ],
    series: [
      { name: '异动统计', type: 'bar', yAxisIndex: 0, data: barData, barWidth: '62%',
        itemStyle: { color: MV_BAR, borderRadius: [2, 2, 0, 0] }, emphasis: { disabled: true } },
      { name: '上证指数', type: 'line', yAxisIndex: 1, data: idxData, showSymbol: false,
        smooth: 0.15, lineStyle: { width: 2, color: MV_IDX },
        itemStyle: { color: MV_IDX },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1,
          colorStops: [{ offset: 0, color: 'rgba(255,214,102,.22)' }, { offset: 1, color: 'rgba(255,214,102,0)' }] } } },
      { name: '带动上涨板块', type: 'scatter', yAxisIndex: 1, data: upMoves,
        symbol: 'triangle', symbolRotate: 0, symbolSize: 9,
        itemStyle: { color: MV_UP, borderColor: '#ffd7db', borderWidth: 1 },
        label: { show: true, position: 'top', color: '#ff8a92', fontSize: 10,
          formatter: (p) => (p.data.m?.sectors?.[0]?.sector || '').slice(0, 4) },
        z: 10 },
      { name: '拖累下跌板块', type: 'scatter', yAxisIndex: 1, data: downMoves,
        symbol: 'triangle', symbolRotate: 180, symbolSize: 9,
        itemStyle: { color: MV_DOWN, borderColor: '#bdf3dc', borderWidth: 1 },
        label: { show: true, position: 'bottom', color: '#3edca9', fontSize: 10,
          formatter: (p) => (p.data.m?.sectors?.[0]?.sector || '').slice(0, 4) },
        z: 10 },
    ],
    // 竞价/午休区域标注(预留)
  }
}

/* ================================================================ */
export default function DashboardPage({ route, params, nav, goStock }) {
  const [ov, setOv] = useState(null)
  const [err, setErr] = useState(null)

  const loadOv = useCallback(() => {
    api.overview()
      .then((d) => { setOv(d); setErr(null) })
      .catch((e) => setErr(e))
  }, [])

  useEffect(() => {
    loadOv()
    const t = setInterval(loadOv, 10000)
    return () => clearInterval(t)
  }, [loadOv])

  /* 全板块强弱榜(可展开涨停股明细) */
  const [secRows, setSecRows] = useState([])
  const [ztOpen, setZtOpen] = useState(null)
  const [ztMap, setZtMap] = useState({})
  const [ztBusy, setZtBusy] = useState(false)
  const [ztErr, setZtErr] = useState(null)
  useEffect(() => {
    let on = true
    api.sectors()
      .then((d) => { if (on) setSecRows(d.rows || []) })
      .catch(() => { /* 保留已有数据 */ })
    return () => { on = false }
  }, [ov && ov.date])
  const toggleZt = (sector) => {
    if (ztOpen === sector) { setZtOpen(null); return }
    setZtOpen(sector)
    if (ztMap[sector]) return
    setZtBusy(true); setZtErr(null)
    api.sectorZt(sector)
      .then((d) => setZtMap((m) => ({ ...m, [sector]: d })))
      .catch((e) => setZtErr(String(e?.message || e)))
      .finally(() => setZtBusy(false))
  }

  /* 图表 option：必须无条件调用 hooks（放在提前 return 之前） */
  const phase = (ov && ov.phase) || {}
  const pmeta = (phase.phase && PHASE_META[phase.phase]) || { label: phase.phase_cn || '—', color: '#4c8dff', short: '—' }
  const pc = pmeta.color
  const hist = (ov && ov.stats_history) || []
  const gaugeOpt = useMemo(() => gaugeOption(phase.conf, pc), [phase.conf, pc])
  const emoOpt = useMemo(() => (hist.length ? emotionOption(hist) : null), [hist])
  const proOpt = useMemo(() => (hist.length ? profitOption(hist) : null), [hist])
  const plOpt = useMemo(() => (hist.length ? phaseLadderOption(hist) : null), [hist])
  const secSort = useTableSort('avg_pct')

  /* 板块异动看板(上证5分钟) */
  const [mv, setMv] = useState(null)
  const [mvDate, setMvDate] = useState('')
  const mvDateRef = useRef('')
  const loadMv = useCallback((d) => {
    api.sectorMove(d)
      .then((r) => {
        if (!r || !r.buckets) { setMv(null); return }
        setMv(r)
        if (!d && r.date) { setMvDate(r.date); mvDateRef.current = r.date }
      })
      .catch(() => { /* 盘中数据未就绪时静默 */ })
  }, [])
  useEffect(() => {
    loadMv('')
    const t = setInterval(() => loadMv(mvDateRef.current), 45000)
    return () => clearInterval(t)
  }, [loadMv])
  const pickMvDate = (v) => {
    setMvDate(v); mvDateRef.current = v
    if (v) loadMv(v)
  }
  const mvOpt = useMemo(() => (mv && mv.buckets && mv.buckets.length ? sectorMoveOption(mv) : null),
    [mv])

  if (err && !ov) {
    return (
      <div className="page">
        <ErrorBox error={err} onRetry={loadOv} />
      </div>
    )
  }
  if (!ov) return <div className="page"><Loading /></div>

  const date = ov.date
  const stats = ov.stats || {}
  const plan = ov.plan || {}
  const rules = plan.rules || []
  const allocations = plan.allocations || []
  const secTop = (ov.sectors && ov.sectors.top) || []
  const secBottom = ((ov.sectors && ov.sectors.bottom) || []).slice(0, 3)
  const dragon = ov.leaders && ov.leaders.dragon ? ov.leaders.dragon : null
  const leadersCount = ov.leaders ? ov.leaders.count : 0
  const pools = ov.pools || {}
  const sigs = ov.signals || {}

  const position = (phase.position_range_pct && phase.position_range_pct.length === 2)
    ? `${phase.position_range_pct[0]}%–${phase.position_range_pct[1]}%`
    : (plan.cap_label && plan.cap_label.length === 2 ? `${plan.cap_label[0]}%–${plan.cap_label[1]}%` : '—')

  const ladder = stats.ladder || {}
  const ladderTxt = Object.keys(ladder).length
    ? Object.keys(ladder).sort((a, b) => Number(a) - Number(b)).map((k) => `${k}板×${ladder[k]}`).join(' · ')
    : ''
  const vol = stats.volume || null
  const volDiff = vol && vol.today_yi != null && vol.prev_yi != null ? Number(vol.today_yi) - Number(vol.prev_yi) : null
  const volCls = volDiff == null ? 'flat' : volDiff > 0 ? 'up' : volDiff < 0 ? 'down' : 'flat'
  const secAll = secRows.length ? secRows : [...secTop, ...secBottom]
  /* 板块强弱榜: 仅统计 涨幅前10 + 跌幅后10(按涨跌幅取两端); 组内可点表头切换排序 */
  const secCount = secAll.length
  const sortTop10 = sortRows(sortRows(secAll, 'avg_pct', 'desc').slice(0, 10), secSort.key, secSort.dir)
  const sortBottom10 = sortRows(sortRows(secAll, 'avg_pct', 'asc').slice(0, 10), secSort.key, secSort.dir)

  return (
    <div className="page">
      <style>{`
        /* ===== Dashboard 专属 ===== */
        .dash-hero{ position:relative; overflow:hidden; padding:18px 20px 16px; }
        .dash-hero:before{ content:''; position:absolute; left:0; top:0; bottom:0; width:4px; }
        .dash-hero-inner{ display:grid; grid-template-columns:1.6fr 1.1fr 1fr; gap:20px; align-items:center; }
        @media (max-width:1100px){ .dash-hero-inner{ grid-template-columns:1fr; } }
        .dash-h-name{ display:flex; align-items:center; gap:10px; font-size:30px; font-weight:800; letter-spacing:1px; color:#fff; }
        .dash-h-name i{ width:16px; height:16px; border-radius:50%; display:inline-block; box-shadow:0 0 12px currentColor; }
        .dash-h-sub{ display:flex; flex-wrap:wrap; gap:6px; margin-top:10px; }
        .dash-h-desc{ color:#8fa3c0; font-size:12.5px; margin-top:10px; line-height:1.7; max-width:640px; }
        .dash-h-side{ border-left:1px solid rgba(255,255,255,.07); padding-left:16px; }
        @media (max-width:1100px){ .dash-h-side{ border-left:0; padding-left:0; border-top:1px solid rgba(255,255,255,.07); padding-top:12px; } }
        .dash-cap{ color:#8fa3c0; font-size:12px; letter-spacing:1px; }
        .dash-position{ font-size:34px; font-weight:800; color:var(--gold); font-variant-numeric:tabular-nums; line-height:1.2; margin:2px 0; }
        .dash-mode{ color:#cdd9f0; font-size:13px; }
        .dash-alloc{ display:flex; flex-wrap:wrap; gap:6px; margin:10px 0 4px; }
        .dash-alloc-chip{ display:inline-flex; align-items:center; gap:6px; background:rgba(76,141,255,.1); border:1px solid rgba(76,141,255,.35);
          border-radius:8px; padding:4px 9px; font-size:12px; color:#dbe5f7; cursor:pointer; }
        .dash-alloc-chip b{ color:#fff; }
        .dash-alloc-chip .pct{ color:var(--gold); font-variant-numeric:tabular-nums; }
        .dash-alloc-chip .sig{ color:#8fa3c0; font-size:11px; }
        .dash-alloc-chip:hover{ border-color:var(--accent); }
        .dash-hero-btns{ display:flex; flex-wrap:wrap; gap:8px; margin-top:12px; }
        .dash-hero-btns .btn{ background:rgba(76,141,255,.14); border:1px solid rgba(76,141,255,.4); color:#cfe0ff; }
        .dash-hero-btns .btn:hover{ background:rgba(76,141,255,.25); }
        .dash-tiny{ color:#5f7598; font-size:11px; margin-top:8px; }
        .dash-gauge{ text-align:center; }
        .dash-gauge-cap{ color:#8fa3c0; font-size:12px; margin-top:-2px; }

        /* 实时异动 */
        .dash-live{ padding:0; }
        .dash-live .card-body{ padding-top:6px; }
        .dash-live-tag{ font-size:11px; color:var(--amber); border:1px solid rgba(242,193,78,.35); border-radius:99px; padding:0 7px; font-weight:400; }
        .dash-live-err{ color:#ff8a8e; }
        .dash-live-loading{ padding:2px 0; }
        .dash-live-empty{ color:#5f7598; font-size:12.5px; padding:4px 0 8px; }
        .dash-live-scroll{ display:flex; gap:8px; overflow-x:auto; padding:4px 0 8px; }
        .dash-live-item{ display:inline-flex; align-items:center; gap:8px; white-space:nowrap; flex:0 0 auto;
          background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07); border-radius:9px; padding:6px 12px; cursor:pointer; transition:all .15s; }
        .dash-live-item:hover{ border-color:rgba(76,141,255,.55); background:rgba(76,141,255,.08); }
        .dash-live-rank{ color:#5f7598; font-size:11px; font-variant-numeric:tabular-nums; }
        .dash-live-item b{ color:#e9effc; font-size:13px; }
        .dash-live-item .num{ font-size:12px; }

        /* 情绪阶段时间轴 */
        .dash-tl{ display:flex; width:100%; margin:10px 0 6px; border-radius:7px; overflow:hidden; }
        .dash-tl b{ flex:1 1 0; min-width:0; height:24px; display:block; cursor:default; }
        .dash-tl-meta{ display:flex; flex-wrap:wrap; gap:4px 16px; color:#8fa3c0; font-size:11.5px; }
        .dash-tl-meta i{ width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:5px; vertical-align:-1px; }
        .dash-tl-dates{ display:flex; justify-content:space-between; color:#5f7598; font-size:11px; margin-bottom:2px; }

        /* 板块强弱表 */
        .tag-main{ color:var(--gold)!important; border-color:rgba(242,193,78,.5)!important; background:rgba(242,193,78,.1)!important; }
        .dash-sep td{ color:#cdd9f0!important; font-weight:600; background:rgba(255,255,255,.04); letter-spacing:1px; padding:6px 10px; }
        .dash-sep td span{ color:#8fa3c0; font-weight:400; margin-left:8px; font-size:11px; }

        /* 右侧总龙/池卡 */
        .dash-dragon-top{ display:flex; align-items:flex-start; justify-content:space-between; gap:10px; }
        .dash-dragon-name{ font-size:22px; font-weight:800; color:#fff; }
        .dash-score-hero{ text-align:right; }
        .dash-score-hero b{ font-size:34px; color:var(--gold); font-variant-numeric:tabular-nums; line-height:1; }
        .dash-score-hero div{ color:#5f7598; font-size:11px; margin-top:2px; }
        .dash-chips{ display:flex; flex-wrap:wrap; gap:6px; margin:12px 0 4px; }
        .dash-chip{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius:8px; padding:3px 10px; font-size:12px; color:#cdd9f0; }
        .dash-chip b{ color:#fff; font-variant-numeric:tabular-nums; margin-right:3px; }
        .dash-chip small{ color:#5f7598; }
        .dash-cta{ display:flex; align-items:center; justify-content:space-between; gap:10px; margin-top:12px; padding-top:10px; border-top:1px solid rgba(255,255,255,.06); flex-wrap:wrap; }

        /* 池状态 */
        .dash-pools{ display:grid; grid-template-columns:1fr 1fr; gap:10px; margin-bottom:12px; }
        .dash-pool-box{ background:rgba(255,255,255,.03); border:1px solid rgba(255,255,255,.07); border-radius:10px; padding:10px 14px; }
        .dash-pool-box .lbl{ color:#8fa3c0; font-size:12px; }
        .dash-pool-box .val{ font-size:26px; font-weight:800; font-variant-numeric:tabular-nums; }
        .dash-sig-row{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:12px; }

        /* 风控纪律条 */
        .dash-rules{ display:flex; flex-wrap:wrap; gap:8px; }
        .dash-rule{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius:99px;
          padding:3px 13px; font-size:12.5px; color:#cdd9f0; cursor:default; }
        .dash-rule b{ color:var(--amber); margin-right:6px; font-weight:600; }
        .dash-disc{ color:#5f7598; font-size:11px; margin-top:12px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .dash-metric-sub{ }

        /* 情绪周期时间轴(阶段段) */
        .dash-tl2{ margin-top:6px; }
        .dash-tl2-track{ display:flex; width:100%; height:34px; border-radius:8px; overflow:hidden;
          border:1px solid rgba(255,255,255,.10); box-shadow: inset 0 0 10px rgba(0,0,0,.25); }
        .dash-tl2-seg{ display:flex; align-items:center; justify-content:center; min-width:0; cursor:default; position:relative; }
        .dash-tl2-seg:hover{ filter:brightness(1.35); }
        .dash-tl2-seg.now{ outline:2px solid #fff; outline-offset:-2px; }
        .dash-tl2-label{ font-size:12px; font-weight:700; color:#fff; text-shadow:0 1px 2px rgba(0,0,0,.6); white-space:nowrap; overflow:hidden; }
        .dash-tl2-now{ color:var(--gold); font-weight:600; }
        .dash-tl2-seq{ display:flex; flex-wrap:wrap; gap:6px 18px; margin-top:8px; color:#8fa3c0; font-size:11.5px; }
        .dash-tl2-seqitem{ display:inline-flex; align-items:center; gap:5px; }
        .dash-tl2-seqitem i{ width:9px; height:9px; border-radius:50%; display:inline-block; }
        .dash-tl2-seqitem b{ color:#cdd9f0; font-weight:600; }
        .dash-tl2-legend{ display:flex; flex-wrap:wrap; gap:4px 16px; margin-top:8px; color:#8fa3c0; font-size:11.5px; }
        .dash-tl2-legend i{ width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:5px; vertical-align:-1px; }

        /* 板块行展开 */
        .dash-sec-tbl td{ vertical-align:middle; }
        .dash-sec-row{ cursor:pointer; }
        .dash-sec-row:hover td{ background:rgba(76,141,255,.06); }
        .dash-sec-row.open td{ background:rgba(242,193,78,.05); }
        .caret{ display:inline-block; transition:transform .15s; color:#8fa3c0; }
        .sec-detail-row td{ padding:0 10px 10px; background:rgba(0,0,0,.18); }
        .sec-detail{ padding:10px 12px; border:1px solid rgba(255,255,255,.07); border-radius:10px; }
        .sec-anchor{ display:flex; flex-wrap:wrap; gap:6px 14px; align-items:center; background:rgba(242,193,78,.07);
          border:1px dashed rgba(242,193,78,.35); border-radius:9px; padding:7px 10px; font-size:12.5px; color:#e8d9a8; margin-bottom:8px; }
        .sec-anchor b{ color:#fff; }
        .sec-zt-head{ color:#5f7598; font-size:11.5px; margin-bottom:6px; }
        .zt-item{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 12px; padding:6px 4px; border-bottom:1px dashed rgba(255,255,255,.06); font-size:12.5px; }
        .zt-item:last-child{ border-bottom:0; }
        .zt-item .nm{ font-weight:600; }
        .zt-item .note{ color:#7f94b5; font-size:11.5px; flex:1 1 220px; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
        .zt-item .note:hover{ white-space:normal; }
        .role-chip{ display:inline-flex; align-items:center; border-radius:6px; padding:0 7px; font-size:11.5px; line-height:19px; }
        .role-dragon{ background:rgba(245,68,75,.16); color:#ff8a8e; border:1px solid rgba(245,68,75,.45); }
        .role-follow{ background:rgba(242,193,78,.12); color:#f2c14e; border:1px solid rgba(242,193,78,.4); }
        .role-first{ background:rgba(76,141,255,.14); color:#7aa9ff; border:1px solid rgba(76,141,255,.4); }
        .role-gray{ background:rgba(255,255,255,.05); color:#8fa3c0; border:1px solid rgba(255,255,255,.12); }
      `}</style>

      {/* 顶部小字条：行情标签 + 阶段 */}
      <div className="page-head">
        <div>
          <h1 className="page-title">情绪仪表盘</h1>
          <div className="page-sub">92K 情绪周期量化决策 · 龙头 / 补涨 / 切换 · 数据口径与免责见底部说明</div>
        </div>
        <div className="toolbar">
          <span className="tag tag-gray">{ov?.mode?.label || '行情'} · {date}</span>
          <span className="tag" style={{ color: pc, borderColor: `${pc}88`, background: `${pc}1f` }}>
            <i style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: pc, marginRight: 4 }} />
            {phase.phase_cn || phase.label || '—'}
          </span>
          {phase.mode_text ? <span className="tag tag-gray">{phase.mode_text}</span> : null}
          <span className="muted small">每 20 秒自动刷新</span>
          {err && <span style={{ color: '#ff8a8e', fontSize: 12 }}>自动刷新失败 · 重试中（展示上一份数据）</span>}
        </div>
      </div>

      {/* 1) 阶段英雄卡 */}
      <section
        className="card dash-hero"
        style={{
          background: `linear-gradient(120deg, ${pc}24 0%, transparent 52%), linear-gradient(180deg,#111c30,#0d1526)`,
          borderLeft: `4px solid ${pc}`,
        }}
      >
        <div className="dash-hero-inner">
          {/* 左侧：阶段身份 */}
          <div>
            <div className="dash-h-name"><i style={{ background: pc, color: pc }} />{phase.phase_cn || phase.label || '—'}</div>
            <div className="dash-h-sub">
              <span className="tag tag-gray">{pmeta.label}</span>
              {(phase.reasons || []).map((r, i) => (
                <span key={i} className="tag" style={{ background: 'rgba(255,255,255,.05)' }}>{r}</span>
              ))}
            </div>
            {phase.desc && <div className="dash-h-desc">{phase.desc}</div>}
          </div>

          {/* 中：策略建议区 */}
          <div className="dash-h-side">
            <div className="dash-cap">策略建议 · 总仓位区间</div>
            <div className="dash-position">{position}</div>
            {phase.mode_text && <div className="dash-mode">{phase.mode_text}</div>}
            {allocations.length > 0 && (
              <>
                <div className="dash-cap" style={{ marginTop: 10 }}>建议标的（单票分配）</div>
                <div className="dash-alloc">
                  {allocations.map((a) => (
                    <button key={a.code} className="dash-alloc-chip" onClick={() => goStock(a.code)} title={`${a.sig} · 强度${a.strength ?? '—'}`}>
                      <b>{a.name}</b>
                      <span className="pct">{fmt(a.pct, 1)}%</span>
                      <span className="sig">{a.sig || ''}</span>
                    </button>
                  ))}
                </div>
              </>
            )}
            <div className="dash-hero-btns">
              <button className="btn btn-sm" onClick={() => nav('/pools')}>查看补涨池</button>
              <button className="btn btn-sm" onClick={() => nav('/leaders')}>查看龙头榜</button>
            </div>
            {plan.single_max_pct && plan.stop_loss_pct != null && (
              <div className="dash-tiny">单票上限 ≤{plan.single_max_pct}% · 单笔止损 -{plan.stop_loss_pct}%</div>
            )}
          </div>

          {/* 右侧：情绪温度计 */}
          <div className="dash-gauge">
            <EChart option={gaugeOpt} height={185} />
            <div className="dash-gauge-cap">阶段置信度 · conf = {fmt((phase.conf || 0) * 100, 0)}%</div>
          </div>
        </div>
      </section>

      {/* 实时异动 / 涨幅榜（顶部小表，每 8~10s 轮询） */}
      <LiveTicker goStock={goStock} />

      {/* 2) 指标卡组 */}
      <div className="cards-grid">
        <Card><Stat label="涨停家数" value={stats.zt_count ?? '—'} tone="up" big sub={ladderTxt || '今日连板梯队'} /></Card>
        <Card><Stat label="跌停家数" value={stats.dt_count ?? '—'} tone="down" big sub="市场亏钱效应" /></Card>
        <Card><Stat label="最高连板" value={stats.max_streak ?? '—'} big sub="全市场空间高度" /></Card>
        <Card>
          <div className="stat-label">上涨 / 下跌家数</div>
          <div className="stat-value" style={{ fontSize: 26, marginTop: 2 }}>
            <span className="up num">{stats.up_count ?? '—'}</span>
            <span className="muted2" style={{ margin: '0 8px' }}>/</span>
            <span className="down num">{stats.down_count ?? '—'}</span>
          </div>
          <div className="stat-sub">市场均值 {fmtPct(stats.mean_pct)}</div>
        </Card>
        <Card>
          <div className="stat-label">昨日涨停今表现</div>
          <div className="stat-value" style={{ fontSize: 26, marginTop: 2 }}>
            {stats.premium_end == null ? <span className="flat">—</span> : <span className={Number(stats.premium_end) > 0 ? 'up' : 'down'}>{fmtPct(stats.premium_end)}</span>}
          </div>
          <div className="stat-sub">{stats.premium_end == null ? '无样本（昨日无涨停）' : '昨日涨停股今日平均收盘表现'}</div>
        </Card>
        <Card><Stat label="炸板率" value={`${fmt(stats.explosion, 1)}%`} big sub="炸板数 / 曾涨停数" /></Card>
        <Card><Stat label="两市成交额" value={fmtAmountYi(stats.amount_sum)} big sub="全市场成交（亿）" /></Card>
        <Card>
          <div className="stat-label">量能 · 较昨日同时段</div>
          <div className="stat-value" style={{ fontSize: 22, marginTop: 2 }}>
            {vol ? (
              <span className={volCls}>
                今 {fmt(vol.today_yi, 0)}亿
                <span className="muted2" style={{ fontSize: 13, marginLeft: 8 }}>昨同期 {fmt(vol.prev_yi, 0)}亿</span>
              </span>
            ) : (
              <span className="flat">—</span>
            )}
          </div>
          <div className="stat-sub">
            {volDiff != null
              ? `较昨 ${volDiff >= 0 ? '多' : '少'} ${Math.abs(volDiff).toFixed(0)}亿（${vol.basis}）`
              : vol ? '对比数据计算中…' : '分时档案积累中，次日自动可对比'}
          </div>
        </Card>
        <Card><Stat label="昨日涨停今高开" value={stats.premium_open == null ? '—' : `${fmt(stats.premium_open, 2)}%`} sub="开盘溢价参考" /></Card>
      </div>

      {/* 3) 两图 + 情绪阶段时间轴 */}
      <div className="grid2">
        <Card title="情绪面 · 涨停/跌停 vs 最高连板" extra={<span className="muted">近 {hist.length} 个交易日</span>}>
          {emoOpt ? <EChart option={emoOpt} height={260} /> : <Empty text="暂无历史数据" />}
        </Card>
        <Card title="赚钱效应 · 昨涨停今表现 & 炸板率" extra={<span className="muted">断点为无样本日</span>}>
          {proOpt ? <EChart option={proOpt} height={260} /> : <Empty text="暂无历史数据" />}
        </Card>
      </div>
      {hist.length > 0 && (
        <Card title="情绪周期时间轴" extra={<span className="muted">近 {hist.length} 个交易日 · 阶梯线=阶段迁移 · 悬停查看当日</span>}>
          {plOpt ? (
            <>
              <EChart option={plOpt} height={220} />
              <div className="dash-tl2-legend">
                {PHASE_KEYS.map((k) => (
                  <span key={k}><i style={{ background: PHASE_META[k].color }} />{PHASE_META[k].label}</span>
                ))}
                <span className="muted" style={{ marginLeft: 'auto' }}>
                  下→上为情绪由冷转热：主跌→试错→主升→高位震荡
                </span>
              </div>
            </>
          ) : (
            <Empty text="暂无历史数据" />
          )}
        </Card>
      )}

      {/* 3.5) 板块异动看板 */}
      <Card
        title={`板块异动 · 上证指数 9:15–15:00`}
        extra={
          <span style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
            {mv && mv.days && mv.days.length > 1 && (
              <select className="input" style={{ width: 140, padding: '2px 6px' }} value={mvDate || mv.date || ''} onChange={(e) => pickMvDate(e.target.value)}>
                {mv.days.map((d0) => (
                  <option key={d0} value={d0}>{d0}</option>
                ))}
              </select>
            )}
            <span className="muted">红▲带动上涨 · 绿▼拖累下跌 · 柱=上证5分钟成交额(亿)</span>
          </span>
        }
      >
        {mvOpt ? (
          <>
            <EChart option={mvOpt} height={320} />
            <div className="muted small" style={{ marginTop: 6 }}>
              口径：指数=上证5分钟K(新浪真实分时)；成交额=每5分钟上证成交额(亿元)；板块标记=该时段成交额增量居前且当日方向与指数异动一致的板块（需盘中分时档案，盘后仍可回看）。
            </div>
          </>
        ) : (
          <Empty text={mv ? '暂无该日分时数据' : '分时档案积累中：开盘后自动生成 上证5分钟 与 板块异动标记'} />
        )}
      </Card>

      {/* 4) 板块强弱 */}
      <div className="grid-28">
      <Card
        title={`板块强弱榜 · 涨幅前10 / 跌幅后10`}
        extra={
          <span className="muted">
            全市场共 {secCount} 个板块，仅统计两端各10名；点板块行展开“今日涨停股分层（龙头/补涨/跟风）”；* = 量能档案积累中
          </span>
        }
      >
        <div className="table-wrap">
          <table className="tbl dash-sec-tbl">
            <thead>
              <tr>
                <th style={{ width: 44 }}>排名</th>
                <SortTh label="板块" sortKey="sector" sort={secSort} />
                <SortTh label="涨跌幅" sortKey="avg_pct" sort={secSort} />
                <SortTh label="今涨停" sortKey="zt_today" sort={secSort} />
                <SortTh label="5日涨幅" sortKey="avg5_pct" sort={secSort} />
                <SortTh label="连续涨跌" sortKey="streak_n" sort={secSort} />
                <SortTh label="成交额" sortKey="amount" sort={secSort} />
                <SortTh label="量能(较昨同时段)" sortKey="vol_ratio" sort={secSort} />
                <th />
              </tr>
            </thead>
            {secCount === 0 && (
              <tbody><tr><td colSpan={9}><Empty text="暂无板块数据" /></td></tr></tbody>
            )}
            {secCount > 0 && (
              <>
                <tbody>
                  <tr className="dash-sep"><td colSpan={9}>涨幅前 10（资金主攻方向）</td></tr>
                  {sortTop10.map((s, idx) => (
                    <SectorRow key={s.sector} s={s} rank={idx + 1} open={ztOpen === s.sector}
                      busy={ztBusy} err={ztErr} detail={ztMap[s.sector]}
                      onToggle={() => toggleZt(s.sector)} goStock={goStock} />
                  ))}
                </tbody>
                <tbody>
                  <tr className="dash-sep"><td colSpan={9}>跌幅后 10（回避方向）</td></tr>
                  {sortBottom10.map((s, idx) => (
                    <SectorRow key={s.sector} s={s} rank={idx + 1} open={ztOpen === s.sector}
                      busy={ztBusy} err={ztErr} detail={ztMap[s.sector]}
                      onToggle={() => toggleZt(s.sector)} goStock={goStock} />
                  ))}
                </tbody>
              </>
            )}
          </table>
        </div>
        <div className="muted small" style={{ marginTop: 8, color: '#5f7598' }}>
          分组按涨跌幅两端各取10名；组内可点表头切换排序（当前：{secSort.key}，{secSort.dir === 'asc' ? '升序' : '降序'}）· 点击板块行展开今日涨停股分层；
          分层口径：龙头 = 今日最高连板且成交额居前；同高/中位 = 跟风（忌讳追高）；首板 = 补涨/试错观察；
          板块高标(anchors)今日未封板表示总龙断板/歇整，注意“断板即撤”。
        </div>
      </Card>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 12, minWidth: 0 }}>
          {/* 总龙快照 */}
          <Card title="总龙快照" extra={<span className="muted">板块龙 {leadersCount} 只</span>}>
            {dragon ? (
              <>
                <div className="dash-dragon-top">
                  <div>
                    <div className="dash-dragon-name">
                      <span className="link" onClick={() => goStock(dragon.code)}>{dragon.name}</span>
                    </div>
                    <div className="muted small">{dragon.code} · {dragon.sector}</div>
                  </div>
                  <div className="dash-score-hero">
                    <b>{fmt(dragon.score, 1)}</b>
                    <div>六维总分</div>
                  </div>
                </div>
                <Meter value={dragon.score} color={YELLOW} height={5} />
                <div className="dash-chips">
                  <span className="dash-chip"><small>现价</small><b>{fmt(dragon.price)}</b></span>
                  <span className="dash-chip"><small>今日</small><PctText value={dragon.pct_today} /></span>
                  <span className="dash-chip"><small>最高20日连板</small><b>{dragon.streak}板</b></span>
                  <span className="dash-chip"><small>60日涨幅</small><PctText value={dragon.run60} /></span>
                  <span className="dash-chip"><small>换手</small><b>{fmt(dragon.turnover, 2)}%</b></span>
                </div>
                {dragon.broken_today && (
                  <div style={{ marginTop: 8 }}>
                    <Tag cls="tag-strong" >⚠ 高位断板回落 — 龙头断板即撤信号</Tag>
                  </div>
                )}
                <div className="dash-cta">
                  <span className="muted small">板块内评分最强且具备辨识度</span>
                  <button className="btn btn-sm" onClick={() => nav('/leaders')}>查看龙头榜</button>
                </div>
              </>
            ) : (
              <>
                <Empty text="当前无明确总龙" />
                <div className="dash-cta">
                  <span className="muted small">等待下一只辨识度标的出现</span>
                  <button className="btn btn-sm" onClick={() => nav('/leaders')}>查看龙头榜</button>
                </div>
              </>
            )}
          </Card>

          {/* 股票池状态 */}
          <Card title="股票池 & 信号状态">
            <div className="dash-pools">
              <div className="dash-pool-box">
                <div className="lbl">补涨池 buyang</div>
                <div className="val up">{pools.buyang ?? '—'}</div>
              </div>
              <div className="dash-pool-box">
                <div className="lbl">切换池 qiehuan</div>
                <div className="val" style={{ color: '#cdd9f0' }}>{pools.qiehuan ?? '—'}</div>
              </div>
            </div>
            <div className="dash-sig-row">
              <Tag tone="buy">买入 {sigs.buy ?? 0}</Tag>
              <Tag tone="sell">卖出 {sigs.sell ?? 0}</Tag>
              <Tag tone="watch">观察 {sigs.watch ?? 0}</Tag>
            </div>
            <div className="dash-cta" style={{ borderTop: 0, paddingTop: 0, marginTop: 0 }}>
              <button className="btn btn-sm" onClick={() => nav('/pools')}>查看股票池</button>
              <button className="btn btn-sm" onClick={() => nav('/signals')}>查看信号</button>
            </div>
          </Card>
        </div>
      </div>

      {/* 5) 风控纪律条 + disclaimer */}
      <Card
        title="风控纪律"
        extra={<span className="muted">{date} · 总仓位 {position} · 阶段 {phase.mode_text || pmeta.label}</span>}
      >
        <div className="dash-rules">
          {rules.length === 0 && <span className="muted small">暂无风控规则</span>}
          {rules.map((r, i) => (
            <span key={i} className="dash-rule" title={r.text}><b>{r.name}</b>{r.text}</span>
          ))}
        </div>
        {ov.disclaimer && (
          <div className="dash-disc" title={ov.disclaimer}>{ov.disclaimer}</div>
        )}
      </Card>
    </div>
  )
}

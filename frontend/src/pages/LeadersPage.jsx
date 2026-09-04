import { useState, useEffect, useMemo, useCallback } from 'react'
import { api } from '../api'
import { Card, Tag, Meter, Empty, Loading, ErrorBox, PctText, ConditionList, useTableSort, sortRows, SortTh } from '../components/ui'
import EChart, { TOOLTIP, axisCommon, AXIS_TEXT } from '../components/EChart'
import { UP, DOWN, FLAT, fmt } from '../format'

const TXT = '#cdd9f0'
const YELLOW = '#f2c14e'
const NOTE_DEFAULT =
  '龙头识别依据：辨识度/逻辑硬/带动性/换手/价格/市场共识六维评分(L-01..L-06)；总龙 = 全市场最高分(≥45)，板块龙 = 各板块内最强。断板/炸板以当日行情自动标记。'

function sigClr(n) {
  const v = Number(n)
  return v > 0 ? UP : v < 0 ? DOWN : FLAT
}

/* ---------- 总龙 K 线（蜡烛，红涨绿跌） ---------- */
function candleOption(rows) {
  const dates = rows.map((r) => r.date)
  return {
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      axisPointer: { type: 'cross', label: { color: '#0d1421', backgroundColor: AXIS_TEXT } },
      formatter: (ps) => {
        if (!ps || !ps.length) return ''
        const i = ps[0].dataIndex
        const r = rows[i]
        if (!r) return ''
        const c = sigClr(r.pct)
        const html = `
          <div style="font-weight:700;margin-bottom:4px;">${r.date}</div>
          开 <b>${fmt(r.open)}</b>　收 <b style="color:${c}">${fmt(r.close)}</b><br/>
          高 <b>${fmt(r.high)}</b>　低 <b>${fmt(r.low)}</b><br/>
          涨跌 <b style="color:${c}">${Number(r.pct) > 0 ? '+' : ''}${fmt(r.pct, 2)}%</b>　换手 ${fmt(r.turnover, 2)}%<br/>
          成交额 ${fmt(r.amount, 2)}亿${r.streak > 0 ? `　阶段连板 <b style="color:${YELLOW}">${r.streak}板</b>` : ''}${r.limit_up ? '　<span style="color:#ff6b70">涨停</span>' : ''}${r.limit_down ? '　<span style="color:#35e0a0">跌停</span>' : ''}
        `
        return html
      },
    },
    grid: { left: 8, right: 12, top: 16, bottom: 6, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      ...axisCommon(true),
      axisLabel: { color: AXIS_TEXT, fontSize: 10, interval: 'auto', hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      scale: true,
      ...axisCommon(false),
      axisLabel: { color: AXIS_TEXT, fontSize: 10, formatter: (v) => Number(v).toFixed(2) },
    },
    dataZoom: [{ type: 'inside', throttle: 60 }],
    series: [
      {
        name: 'K线',
        type: 'candlestick',
        data: rows.map((r) => [r.open, r.close, r.low, r.high]),
        itemStyle: { color: UP, color0: DOWN, borderColor: UP, borderColor0: DOWN },
      },
    ],
  }
}

/* ---------- 龙头历史回溯：当日市场最高连板 + 当期龙头 ---------- */
function historyOption(rows) {
  return {
    color: [YELLOW],
    tooltip: {
      ...TOOLTIP,
      trigger: 'axis',
      formatter: (ps) => {
        if (!ps || !ps.length) return ''
        const i = ps[0].dataIndex
        const r = rows[i]
        if (!r) return ''
        const has = r.code && r.name
        const head = has
          ? `<div style="font-weight:700;margin-bottom:2px;">当期龙头：${r.name} <span style="color:${AXIS_TEXT};font-weight:400;">(${r.code} · ${r.sector || '—'})</span></div>`
          : `<div style="font-weight:700;margin-bottom:2px;">当期龙头：<span style="color:${AXIS_TEXT}">无 ≥3 板</span></div>`
        return `${head}
          <div style="margin-bottom:2px;">${r.date} · 当日市场最高连板 <b style="color:${YELLOW}">${r.streak}板</b></div>
          ${r.limit ? '<div style="color:#8fa3c0;">当日仍处涨停状态</div>' : ''}`
      },
    },
    grid: { left: 8, right: 16, top: 26, bottom: 4, containLabel: true },
    legend: { top: 0, data: ['当日市场最高连板'], textStyle: { color: TXT }, itemWidth: 12, itemHeight: 8 },
    xAxis: {
      type: 'category',
      data: rows.map((r) => r.date),
      ...axisCommon(true),
      axisLabel: { color: AXIS_TEXT, fontSize: 10, interval: 'auto', hideOverlap: true },
    },
    yAxis: {
      type: 'value',
      name: '连板',
      minInterval: 1,
      ...axisCommon(false),
      nameTextStyle: { color: AXIS_TEXT, fontSize: 10 },
      axisLabel: { color: AXIS_TEXT, fontSize: 11 },
    },
    series: [
      {
        name: '当日市场最高连板',
        type: 'line',
        data: rows.map((r) => r.streak),
        symbol: 'circle',
        symbolSize: 4,
        smooth: false,
        lineStyle: { color: YELLOW, width: 2 },
        itemStyle: { color: YELLOW },
        areaStyle: { color: 'rgba(242,193,78,0.10)' },
      },
    ],
  }
}

/* ================================================================ */
export default function LeadersPage({ route, params, nav, goStock }) {
  const [ld, setLd] = useState(null)
  const [hist, setHist] = useState(null)
  const [err, setErr] = useState(null)
  const [st, setSt] = useState(null)
  const [stErr, setStErr] = useState(null)

  const load = useCallback(() => {
    setErr(null)
    Promise.all([api.leaders(), api.leadersHistory(60)])
      .then(([l, h]) => {
        setLd(l)
        setHist(h && h.rows ? h.rows : [])
      })
      .catch((e) => setErr(e))
  }, [])

  useEffect(() => { load() }, [load])

  /* 总龙存在时再取个股 K 线；失败自动重试(最多3次)，仍失败则静默降级为文字 */
  const dragonCode = ld && ld.dragon ? ld.dragon.code : null
  useEffect(() => {
    if (!dragonCode) { setSt(null); setStErr(null); return }
    let on = true
    let tries = 0
    const load = () => {
      setStErr(null)
      api.stock(dragonCode)
        .then((d) => { if (on) setSt(d) })
        .catch((e) => {
          tries += 1
          if (on) {
            if (tries < 3) {
              setTimeout(load, 4000)
            } else {
              setStErr(e)
            }
          }
        })
    }
    setSt(null)
    load()
    return () => { on = false }
  }, [dragonCode])

  /* 图表 option：必须无条件调用 hooks（放在提前 return 之前） */
  const kRows = st && st.kline && st.kline.length ? st.kline.slice(-120) : []
  const cndOpt = useMemo(() => (kRows.length ? candleOption(kRows) : null), [kRows])
  const hisOpt = useMemo(() => (hist && hist.length ? historyOption(hist) : null), [hist])
  const ldSort = useTableSort('score')

  if (err && !ld) {
    return (
      <div className="page">
        <ErrorBox error={err} onRetry={load} />
      </div>
    )
  }
  if (!ld || !hist) return <div className="page"><Loading /></div>

  const dragon = ld.dragon || null
  const sectLeaders = ld.sector_leaders || []
  const sortedLeaders = sortRows(sectLeaders, ldSort.key, ldSort.dir)

  return (
    <div className="page">
      <style>{`
        /* ===== Leaders 页专属 ===== */
        .ldr-total{ align-items:stretch; }
        .ldr-id-row{ display:flex; align-items:flex-start; justify-content:space-between; gap:14px; }
        .ldr-name{ font-size:26px; font-weight:800; color:#fff; line-height:1.25; }
        .ldr-name .link{ color:#fff; }
        .ldr-name .link:hover{ color:var(--accent2); }
        .ldr-meta{ color:#8fa3c0; font-size:12.5px; margin:2px 0 8px; }
        .ldr-score-box{ text-align:right; flex:0 0 auto; }
        .ldr-score-box b{ display:block; font-size:40px; line-height:1; color:var(--gold); font-variant-numeric:tabular-nums; }
        .ldr-score-box span{ color:#5f7598; font-size:11px; }
        .ldr-warn-row{ margin-top:8px; display:flex; flex-wrap:wrap; gap:6px; }
        .ldr-chips{ display:flex; flex-wrap:wrap; gap:8px; margin:14px 0 2px; }
        .ldr-chip{ background:rgba(255,255,255,.04); border:1px solid rgba(255,255,255,.08); border-radius:9px; padding:4px 12px; font-size:12.5px; color:#cdd9f0; }
        .ldr-chip small{ color:#5f7598; margin-right:5px; }
        .ldr-chip b{ color:#fff; font-variant-numeric:tabular-nums; }
        .ldr-conds{ margin-top:12px; }
        .ldr-none-tip{ text-align:center; color:#5f7598; font-size:12.5px; padding:4px 0 12px; }
        .ldr-sc-cell{ width:86px; }
        .ldr-sc-num{ font-variant-numeric:tabular-nums; font-size:13px; margin-bottom:3px; }
        .ldr-st-name b{ color:#e9effc; }
        .ldr-st-name .code{ color:#5f7598; font-size:11px; font-family:var(--mono); }
        .ldr-hint{ margin-top:10px; font-size:12.5px; color:var(--gold); background:rgba(242,193,78,.07);
          border:1px dashed rgba(242,193,78,.3); border-radius:9px; padding:8px 12px; }
        .ldr-hint b{ color:#fff; }
        .ldr-taboo{ display:flex; flex-wrap:wrap; gap:8px; }
        .ldr-taboo-item{ display:inline-flex; align-items:center; gap:6px; background:rgba(255,255,255,.03);
          border:1px solid rgba(255,255,255,.08); color:#8fa3c0; border-radius:9px; padding:6px 14px; font-size:13px; }
        .ldr-taboo-item b{ color:#b9c6dd; font-weight:600; }
        .ldr-taboo-item i{ color:#f5444b; font-style:normal; }
        .ldr-kline-fallback{ color:#8fa3c0; font-size:12.5px; padding:22px 4px; text-align:center; line-height:1.8; }
        .ldr-kline-fallback b{ color:#cdd9f0; }
        .ldr-legend-dot{ display:inline-block; width:10px; height:10px; border-radius:2px; margin-right:5px; vertical-align:-1px; }
      `}</style>

      {/* 1) 页头 */}
      <div className="page-head">
        <div>
          <h1 className="page-title">龙头榜</h1>
          <div className="page-sub">{ld.note || NOTE_DEFAULT}</div>
        </div>
        <div className="toolbar">
          <span className="tag tag-gray">{ld.mode?.label || '行情'} · {ld.date}</span>
          <span className="tag" style={{ color: YELLOW, borderColor: 'rgba(242,193,78,.45)', background: 'rgba(242,193,78,.1)' }}>
            当前阶段：{ld.phase || '—'}
          </span>
        </div>
      </div>

      {/* 2) 总龙卡 */}
      {dragon ? (
        <div className="grid-28 ldr-total">
          <Card
            title="市场总龙 · 六维确认"
            extra={
              <span className="muted">
                <span className="ldr-legend-dot" style={{ background: UP }} />角色：{dragon.role === 'total' ? '市场总龙' : dragon.role}
              </span>
            }
          >
            <div className="ldr-id-row">
              <div style={{ minWidth: 0 }}>
                <div className="ldr-name">
                  <span className="link" onClick={() => goStock(dragon.code)}>{dragon.name}</span>{' '}
                  <Tag tone="buy">市场总龙</Tag>
                </div>
                <div className="ldr-meta">{dragon.code} · {dragon.sector} · 市场最高分标的</div>
                <div className="ldr-warn-row">
                  <Tag cls="tag-gray">六维评分 L-01..L-06</Tag>
                  {dragon.limit_today && <Tag tone="buy">今日涨停</Tag>}
                </div>
                {dragon.broken_today && (
                  <div className="ldr-warn-row">
                    <Tag cls="tag-strong">⚠ 高位断板回落 — 龙头断板即撤信号</Tag>
                  </div>
                )}
              </div>
              <div className="ldr-score-box">
                <b>{fmt(dragon.score, 1)}</b>
                <span>总评分（0-100）</span>
              </div>
            </div>

            <div className="ldr-chips">
              <span className="ldr-chip"><small>现价</small><b>{fmt(dragon.price)}</b></span>
              <span className="ldr-chip"><small>今日</small><PctText value={dragon.pct_today} /></span>
              <span className="ldr-chip"><small>最高20日连板</small><b>{dragon.streak}板</b></span>
              <span className="ldr-chip"><small>60日涨幅</small><PctText value={dragon.run60} /></span>
              <span className="ldr-chip"><small>换手</small><b>{fmt(dragon.turnover, 2)}%</b></span>
              <span className="ldr-chip"><small>是否涨停</small><b>{dragon.limit_today ? '是' : '否'}</b></span>
            </div>

            <div className="ldr-conds">
              <ConditionList items={dragon.conds} />
            </div>
          </Card>

          {/* K 线卡（失败静默降级为文字） */}
          <Card title={`总龙 ${dragon.name} K线`} extra={<span className="muted">近 {kRows.length} 日</span>}>
            {cndOpt ? (
              <EChart option={cndOpt} height={380} />
            ) : (
              <div className="ldr-kline-fallback">
                <div style={{ fontSize: 22, marginBottom: 6 }}>📉</div>
                <b>K 线数据暂不可用</b>
                {stErr ? <div style={{ fontSize: 11, color: '#5f7598' }}>{String(stErr.message || stErr)}</div> : <div>（接口未返回历史行情）</div>}
                <div>以上方六维评分与关键指标为准</div>
              </div>
            )}
          </Card>
        </div>
      ) : (
        <Card title="市场总龙">
          <Empty text="当前无明确市场总龙" />
          <div className="ldr-none-tip">等待下一只评分 ≥45、具备辨识度的连板龙头出现再参与；此期间多看少动。</div>
        </Card>
      )}

      {/* 3) 板块龙表 */}
      <Card
        title="板块龙头表"
        extra={<span className="muted">板块内评分最强且具备辨识度 · 断板 / 炸板自动标记</span>}
      >
        {sectLeaders.length === 0 ? (
          <Empty text="暂无板块龙头数据" />
        ) : (
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <SortTh label="板块龙头" sortKey="name" sort={ldSort} />
                  <SortTh label="板块" sortKey="sector" sort={ldSort} />
                  <SortTh label="评分" sortKey="score" sort={ldSort} />
                  <SortTh label="最高20日连板" sortKey="streak" sort={ldSort} />
                  <SortTh label="今日" sortKey="pct_today" sort={ldSort} />
                  <SortTh label="60日涨幅" sortKey="run60" sort={ldSort} />
                  <SortTh label="换手" sortKey="turnover" sort={ldSort} />
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {sortedLeaders.map((x) => (
                  <tr key={x.code}>
                    <td className="ldr-st-name">
                      <b><span className="link" onClick={() => goStock(x.code)}>{x.name}</span></b>
                      <div className="code">{x.code}</div>
                    </td>
                    <td>{x.sector}</td>
                    <td className="ldr-sc-cell">
                      <div className="ldr-sc-num" style={{ color: x.score >= 60 ? '#35e0a0' : '#cdd9f0' }}>{fmt(x.score, 1)}</div>
                      <div style={{ width: 80 }}><Meter value={x.score} color={x.score >= 60 ? '#35e0a0' : YELLOW} height={4} /></div>
                    </td>
                    <td className="num">{x.streak > 0 ? `${x.streak}板` : <span className="flat">—</span>}</td>
                    <td><PctText value={x.pct_today} /></td>
                    <td><PctText value={x.run60} /></td>
                    <td className="num">{fmt(x.turnover, 2)}%</td>
                    <td>
                      {x.broken_today ? (
                        <Tag cls="tag-strong">⚠ 断板</Tag>
                      ) : x.limit_today ? (
                        <Tag tone="buy">涨停</Tag>
                      ) : (
                        <span className="muted2">—</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {/* 4) 龙头历史回溯 */}
      <Card
        title="龙头历史回溯（近 60 日）"
        extra={<span className="muted"><span className="ldr-legend-dot" style={{ background: YELLOW }} />当日市场最高连板 · 悬停查看当期龙头</span>}
      >
        {hisOpt ? (
          <>
            <EChart option={hisOpt} height={250} />
            <div className="ldr-hint">
              💡 <b>老龙头见顶日 = 新周期启动日（切换时机）</b> —— 连板梯队断层、高位龙头断板当日即为切换窗口；高标不在时，无 ≥3 板阶段宜空仓等待。
            </div>
          </>
        ) : (
          <Empty text="暂无历史回溯数据" />
        )}
      </Card>

      {/* 5) 禁忌条 */}
      <Card title="龙头战法 · 禁忌" extra={<span className="muted">纪律大于技术</span>}>
        <div className="ldr-taboo">
          <span className="ldr-taboo-item"><i>✕</i><b>不做中位跟风</b>——只做辨识度最高者</span>
          <span className="ldr-taboo-item"><i>✕</i><b>不追一致高潮</b>——一致加速日不接力</span>
          <span className="ldr-taboo-item"><i>✕</i><b>不格局杂毛</b>——杂毛股不及预期即走</span>
          <span className="ldr-taboo-item"><i>✕</i><b>龙头断板即撤</b>——断板不博弈穿越</span>
        </div>
      </Card>
    </div>
  )
}

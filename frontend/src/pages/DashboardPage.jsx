import { useState, useEffect, useMemo, useCallback } from 'react'
import { api } from '../api'
import { Card, Stat, Tag, Meter, Empty, Loading, ErrorBox, PctText } from '../components/ui'
import EChart, { TOOLTIP, axisCommon, AXIS_TEXT } from '../components/EChart'
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
    const t = setInterval(load, 9000)
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
    const t = setInterval(loadOv, 20000)
    return () => clearInterval(t)
  }, [loadOv])

  /* 图表 option：必须无条件调用 hooks（放在提前 return 之前） */
  const phase = (ov && ov.phase) || {}
  const pmeta = (phase.phase && PHASE_META[phase.phase]) || { label: phase.phase_cn || '—', color: '#4c8dff', short: '—' }
  const pc = pmeta.color
  const hist = (ov && ov.stats_history) || []
  const gaugeOpt = useMemo(() => gaugeOption(phase.conf, pc), [phase.conf, pc])
  const emoOpt = useMemo(() => (hist.length ? emotionOption(hist) : null), [hist])
  const proOpt = useMemo(() => (hist.length ? profitOption(hist) : null), [hist])

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
        <Card title="情绪阶段时间轴" extra={<span className="muted">色块 = 当日阶段 · 悬停查看日期</span>}>
          <div className="dash-tl-dates">
            <span>{hist[0].date}</span>
            <span>{hist[hist.length - 1].date}</span>
          </div>
          <div className="dash-tl">
            {hist.map((r, i) => {
              const m = PHASE_META[r.phase]
              return (
                <b
                  key={i}
                  style={{ background: m ? m.color : '#4c8dff' }}
                  title={`${r.date} · ${m ? m.label : r.phase}（涨停${r.zt} / 跌停${r.dt}）`}
                />
              )
            })}
          </div>
          <div className="dash-tl-meta">
            {PHASE_KEYS.map((k) => (
              <span key={k}><i style={{ background: PHASE_META[k].color }} />{PHASE_META[k].label}</span>
            ))}
          </div>
        </Card>
      )}

      {/* 4) 板块强弱 */}
      <div className="grid-28">
        <Card title="板块强弱榜" extra={<span className="muted">主线 = 总龙所在板块（金色）</span>}>
          <div className="table-wrap">
            <table className="tbl">
              <thead>
                <tr>
                  <th>板块</th><th>涨跌幅</th><th>今涨停</th><th>5日涨停</th><th>成交额</th>
                </tr>
              </thead>
              <tbody>
                <tr className="dash-sep"><td colSpan={5}>涨幅前 {secTop.length}<span>资金主攻方向</span></td></tr>
                {secTop.map((s) => (
                  <tr key={s.sector}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{s.sector}</span>
                      {s.is_dragon_sector && <Tag cls="tag-main" >主线</Tag>}
                    </td>
                    <td><PctText value={s.avg_pct} /></td>
                    <td className="num">{s.zt_today ?? '—'}</td>
                    <td className="num">{s.zt_5d ?? '—'}</td>
                    <td className="num">{fmtAmountYi(s.amount)}</td>
                  </tr>
                ))}
                <tr className="dash-sep"><td colSpan={5}>垫底 {secBottom.length}<span>回避方向</span></td></tr>
                {secBottom.map((s) => (
                  <tr key={s.sector}>
                    <td>
                      <span style={{ fontWeight: 600 }}>{s.sector}</span>
                      {s.is_dragon_sector && <Tag cls="tag-main">主线</Tag>}
                    </td>
                    <td><PctText value={s.avg_pct} /></td>
                    <td className="num">{s.zt_today ?? '—'}</td>
                    <td className="num">{s.zt_5d ?? '—'}</td>
                    <td className="num">{fmtAmountYi(s.amount)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
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

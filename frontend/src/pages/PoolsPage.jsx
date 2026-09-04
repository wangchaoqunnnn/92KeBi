import React, { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { Card, Tag, Empty, Loading, ErrorBox, PctText, ConditionList } from '../components/ui'
import { PHASE_META, PHASE_KEYS, fmt } from '../format'

/* ================= 股票池（补涨候选池 U-03 / 切换候选池 U-04） ================= */

const POOL_META = {
  buyang: {
    title: '补涨候选池',
    code: 'U-03',
    sub: '龙头高度确立后 / 高位震荡赚钱效应未散时，找同题材低位标的',
    empty: '当前非补涨窗口。补涨战法适用阶段：龙头高度确立后/高位震荡中赚钱效应未散时，找同题材低位标的（三低+图形+低位首板/二板）',
  },
  qiehuan: {
    title: '切换候选池',
    code: 'U-04',
    sub: '只做与上轮完全不同的新题材低位首板（新闻驱动）',
    empty: '切换窗口：主跌后的试错期或老龙头见顶≤5日，只做与上轮完全不同的新题材低位首板（新闻驱动）',
  },
}

// 阶段标签配色：优先用对象自带 phase key，退化用中文 label 反查
function phaseMeta(label, keyHint) {
  if (keyHint && PHASE_META[keyHint]) return PHASE_META[keyHint]
  const hit = PHASE_KEYS.map((k) => PHASE_META[k]).find(
    (m) => label && (m.label === label || m.short === label)
  )
  return hit || { label: label || '—', color: '#8fa3c0' }
}

function scoreColor(s) {
  const n = Number(s) || 0
  if (n >= 75) return '#f2c14e'
  if (n >= 60) return '#4c8dff'
  return '#8fa3c0'
}

function PoolItem({ item, kind, open, onToggle, goStock }) {
  if (!item) return null
  return (
    <div className="pool-item">
      <div className="pool-top">
        <div className="pool-id">
          <div className="pool-name-line">
            {item.entry_state === 'first_board' && <Tag tone="buy">首板·今日</Tag>}
            {item.entry_state === 'one_to_two' && <Tag cls="tag-amber">一进二·今日</Tag>}
            {!!item.limit_today && <span className="limit-dot" title="今日涨停" />}
            <button type="button" className="link pool-name" onClick={() => goStock(item.code)}>
              {item.name || '—'}
            </button>
          </div>
          <div className="pool-code">
            {item.code} · {item.sector || '—'}
            {item.price != null && ` · ${fmt(item.price)}元`}
            {item.float_cap != null && ` · 流通${fmt(item.float_cap, 1)}亿`}
          </div>
        </div>

        <div className="pool-pct">
          <PctText value={item.pct_today} />
          <span
            className="score-chip"
            style={{
              color: scoreColor(item.score),
              borderColor: `${scoreColor(item.score)}66`,
              background: `${scoreColor(item.score)}1a`,
            }}
          >
            策略分 {fmt(item.score, 1)}
          </span>
        </div>

        <div className="pool-reasons" title={(item.reasons || []).join('；')}>
          {(item.reasons || []).slice(0, 3).join(' · ') || '暂无理由'}
        </div>

        <div className="pool-act">
          <button
            type="button"
            className={`btn btn-sm ${open ? 'on' : ''}`}
            onClick={onToggle}
          >
            {open ? '收起理由' : '入选理由'}
          </button>
        </div>
      </div>

      {open && (
        <div className="pool-conds">
          <ConditionList items={item.conds} dense />
        </div>
      )}
    </div>
  )
}

function PoolCard({ kind, pool, openMap, onToggle, nav, goStock }) {
  const meta = POOL_META[kind]
  const items = pool && Array.isArray(pool.items) ? pool.items : []
  const isEmpty = !pool || items.length === 0
  return (
    <Card
      title={
        <span>
          {meta.title} <span className="muted2 card-code">{meta.code}</span>
        </span>
      }
      extra={
        <span className="card-extra">
          <Tag cls="tag-gray">{isEmpty ? 0 : items.length} 只候选</Tag>
          {isEmpty && <Tag cls="tag-gray">未触发</Tag>}
        </span>
      }
    >
      {isEmpty ? (
        <div className="pool-empty">
          <Empty text={meta.empty} />
          {kind === 'buyang' && (
            <button type="button" className="btn btn-sm" onClick={() => nav('/leaders')}>
              查看龙头榜 →
            </button>
          )}
        </div>
      ) : (
        <>
          {pool.trigger_note && <div className="pool-trigger">触发原因：{pool.trigger_note}</div>}
          <div className="pool-list">
            {items.map((it) => {
              const key = `${kind}:${it.code}`
              return (
                <PoolItem
                  key={key}
                  item={it}
                  kind={kind}
                  open={!!openMap[key]}
                  onToggle={() => onToggle(kind, it.code)}
                  goStock={goStock}
                />
              )
            })}
          </div>
          <div className="pool-foot muted2">
            按策略评分降序 · 点击「入选理由」展开逐条条件（B/S 编号）核对
          </div>
        </>
      )}
    </Card>
  )
}

const PAGE_CSS = `
.pool-empty { text-align: center; padding: 6px 2px; }
.pool-empty .empty { max-width: 560px; margin: 0 auto 6px; line-height: 1.9; }
.pool-trigger {
  font-size: 12px; color: var(--muted); line-height: 1.7;
  background: rgba(76,141,255,.06); border: 1px solid rgba(76,141,255,.18);
  border-radius: 8px; padding: 7px 10px; margin-bottom: 10px;
}
.pool-list { display: flex; flex-direction: column; gap: 8px; }
.pool-item {
  border: 1px solid var(--border-soft); border-radius: 10px;
  padding: 10px 12px; background: rgba(255,255,255,.015);
}
.pool-item:hover { border-color: #2b3a55; }
.pool-top {
  display: grid; gap: 12px; align-items: center;
  grid-template-columns: minmax(180px, 1fr) 96px minmax(0, 1.6fr) auto;
}
@media (max-width: 980px) {
  .pool-top { grid-template-columns: minmax(160px,1fr) 92px auto; }
  .pool-reasons { grid-column: 1 / -1; }
}
.pool-name-line { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; }
.pool-name {
  background: none; border: 0; padding: 0;
  font-size: 15px; font-weight: 700; color: #e9effc; text-align: left;
}
.pool-name:hover { color: #fff; }
.pool-code { font-size: 11px; color: var(--muted2); margin-top: 3px; }
.pool-pct { display: flex; flex-direction: column; align-items: flex-start; gap: 5px; }
.pool-pct .up, .pool-pct .down, .pool-pct .flat { font-size: 17px; font-weight: 700; font-variant-numeric: tabular-nums; }
.score-chip {
  font-size: 11px; border: 1px solid; border-radius: 6px; padding: 0 7px;
  line-height: 17px; font-variant-numeric: tabular-nums;
}
.pool-reasons {
  color: var(--muted2); font-size: 11.5px; line-height: 1.6; min-width: 0;
  display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 2; overflow: hidden;
}
.pool-act { justify-self: end; }
.pool-item .btn.on { background: rgba(76,141,255,.14); border-color: rgba(76,141,255,.5); color: #fff; }
.pool-conds {
  margin-top: 8px; padding-top: 6px;
  border-top: 1px dashed var(--border-soft);
}
.pool-foot { font-size: 11.5px; margin-top: 8px; }
.limit-dot {
  width: 8px; height: 8px; border-radius: 50%; flex: none; display: inline-block;
  background: var(--red); box-shadow: 0 0 7px rgba(245,68,75,.85);
}
.card-code { font-size: 11px; font-weight: 400; }
`

export default function PoolsPage({ route, params, nav, goStock }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const [openMap, setOpenMap] = useState({})

  const load = useCallback((silent) => {
    api
      .pools()
      .then((d) => {
        setData(d)
        if (!silent) setErr(null)
      })
      .catch((e) => {
        if (!silent || !data) setErr(e)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    load(false)
    const t = setInterval(() => load(true), 30000) // 每 30s 静默刷新
    return () => clearInterval(t)
  }, [load])

  const onToggle = useCallback((kind, code) => {
    setOpenMap((m) => {
      const key = `${kind}:${code}`
      const n = { ...m }
      if (n[key]) delete n[key]
      else n[key] = true
      return n
    })
  }, [])

  if (err) return <ErrorBox error={err} onRetry={() => load(false)} />
  if (!data) return <Loading />

  const ph = phaseMeta(data.phase, data.buyang?.phase || data.qiehuan?.phase || '')

  return (
    <div className="page">
      <style>{PAGE_CSS}</style>

      <div className="page-head">
        <div>
          <h2 className="page-title">股票池</h2>
          <div className="page-sub">补涨候选池 · 切换候选池 —— 规则初筛仅供研究，题材判断需人工复核</div>
        </div>
        <div className="toolbar">
          {ph && (
            <span
              className="tag"
              style={{
                color: ph.color,
                borderColor: `${ph.color}88`,
                background: `${ph.color}1f`,
              }}
            >
              当前阶段：{ph.label}
            </span>
          )}
          <Tag cls="tag-gray">更新 {data.date || '—'}</Tag>
        </div>
      </div>

      <div className="grid2">
        <PoolCard
          kind="buyang"
          pool={data.buyang}
          openMap={openMap}
          onToggle={onToggle}
          nav={nav}
          goStock={goStock}
        />
        <PoolCard
          kind="qiehuan"
          pool={data.qiehuan}
          openMap={openMap}
          onToggle={onToggle}
          nav={nav}
          goStock={goStock}
        />
      </div>

      <p className="muted small" style={{ margin: 0 }}>
        {data.note || '候选池为规则初筛，仅供研究；入选不代表必涨，最终决策需结合新闻、盘面与风控人工确认。'}
      </p>
    </div>
  )
}

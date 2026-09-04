# 前端开发契约（页面子代理必读）

项目：92K 情绪周期决策台（龙头/补涨/切换 情绪周期量化决策辅助，暗色看盘风格）
真实接口响应样例在 `docs/snap/*.json`，开发前先打开对照（字段以快照为准）。

## 环境与规则
- 文件位置：`frontend/src/`
- 只允许 **新建** 你负责的页面文件，禁止改动：`main.jsx / App.jsx / api.js / format.js / styles.css / components/ui.jsx / components/EChart.jsx`
- 页面特殊样式：在页面组件内写 `<style>{`...`}</style>`（React 内联 style 标签），或用 styles.css 已有类。
- 语言中文；所有价格/百分比用红涨绿跌：UP=#f5444b DOWN=#0ecb81（见 format.js）。
- 组件 props：页面统一收到 `{ route, params, nav, goStock }`。`goStock(code)` 跳个股页。
- 每个页面文件默认导出一个 React 组件（function），文件名固定。

## 可用 imports（全部已存在）
```js
import { api } from '../api'                       // api.overview() 等
import { Card, Stat, Tag, Meter, Section, Empty, Loading, ErrorBox, PctText, ConditionList } from '../components/ui'
import EChart, { AXIS_TEXT, GRID_LINE, SPLIT_LINE, TOOLTIP, axisCommon } from '../components/EChart'
import { PHASE_META, UP, DOWN, FLAT, fmt, fmtPct, fmtNum, fmtAmountYi, fmtDate, clsPct, SIGNAL_DIR_META, STRENGTH_META, dateStr } from '../format'
```
- api 方法：meta, live, overview, sectors, history(days), leaders, leadersHistory(days), pools, signals, search(q), stock(code), backtestMeta, runBacktest({mode,start,end,capital}), adminStatus, adminRefresh, adminAdvance(days)
- 组件用法示例：
  - `<Card title="标题" extra={<Tag tone="buy">…</Tag>}>…</Card>`
  - `<Stat label="涨停" value={9} tone="up"/>`（tone: up/down 只给数值上色，见 ui.jsx）
  - `<Meter value={score} color="#4c8dff"/>`
  - `<PctText value={pct}/>`  自动上色
  - `<ConditionList items={conds}/>` items: {id,name,ok,score,note}
  - `<Loading/> <Empty text/> <ErrorBox error onRetry/>`

## 页面结构约定
```jsx
export default function LeadersPage({ route, params, nav, goStock }) {
  const [data, setData] = useState(null); const [err, setErr] = useState(null)
  useEffect(() => { api.leaders().then(setData).catch(setErr) }, [])
  if (err) return <ErrorBox error={err} onRetry={...} />
  if (!data) return <Loading/>
  return <div className="page"> ... </div>
}
```
数据为空/某对象为 null 时给 Empty/说明卡片，不要崩溃。

## PHASE_META
`PHASE_META['main_decline'|'probe'|'main_ascend'|'high_oscillate'] = { label, color, short, order }`
阶段卡配色统一用 meta.color。

---

# 接口字段速查（详细见 snap/*.json）

## GET /api/dashboard/overview → snap/overview.json
```
date           今日日期
phase: { phase, label, phase_cn, conf(0-1), reasons[中文依据], desc, position_range_pct:[0,10]仓位区间, mode_text }
stats: { zt_count涨停, dt_count跌停, up_count涨, down_count跌, mean_pct, amount_sum(亿), max_streak最高连板,
         ladder: {板数:数量}, premium_open昨涨今高开%, premium_end昨涨今收盘表现%(null=无样本), explosion炸板率% }
stats_history[≤40]: {date, phase, zt, dt, max_streak, premium(null可), explosion, mean_pct, up, down, amount}
sectors: { top:[...板块], bottom:[...] } 每项: {sector, avg_pct, zt_today, dt_today, amount, up_ratio, zt_5d, is_dragon_sector}
leaders: { dragon:{code,name,sector,score,streak,run60,pct_today,turnover,price,limit_today,broken_today}|null, count }
pools: { buyang:数量, qiehuan:数量 }  signals: { buy, sell, watch }
plan: { phase, conf, cap_label:[低,高]总仓位区间, cap_frac, mode_tip, single_max_pct:25, stop_loss_pct:5,
        allocations:[{code,name,sector,pct,sig,strength}]建议单票分配, rules:[{name,text}]风控纪律 }
disclaimer
```

## GET /api/leaders → snap/leaders.json
```
date, phase(中文), dragon|null: {code,name,sector,score,streak,run60,pct_today,turnover,price,limit_today,broken_today,role, conds:[{id:'L-01',name,ok,score,note}]}
sector_leaders[]: 同上(板块龙, 无 dragon 则每板块最强)
pool[]: 候选(不含 conds): {code,name,sector,score,streak,run60,pct_today,turnover,price,limit_today,broken_today}
note: 规则说明文本
```

## GET /api/leaders/history?days=40 → snap/leaderhistory.json
```
rows[]: { date, code|null, name|null, sector|null, streak(当日最高连板), limit }
```
用途：龙头时间线（谁在哪个时间段是龙头）。若当日无>=3板则 code=null。

## GET /api/pools → snap/pools.json
```
date, phase(中文),
buyang|null: { asof, phase, strategy:'buyang', trigger_note,
               items[]: { code,name,sector,score(0-100), conds:[{id:'B-01',name,ok,score,note}],
                          reasons[中文理由], entry_state('first_board'|'one_to_two'|null),
                          limit_today, streak, run60, price, float_cap, turnover, pct_today, vol20, dist_high60 } }
qiehuan|null: 同上 strategy:'qiehuan' conds id S-01.., 字段多 news_n
```

## GET /api/signals → snap/signals.json
```
date, items[]: { code,name,sector,strategy('leader'|'buyang'|'qiehuan'|'generic'), strategy_cn,
                 signal(信号名), dir('buy'|'sell'|'watch'), dir_cn, strength('强'|'中'|'警示'),
                 reason(中文依据), date, price, pct_today, turnover }
count:{buy,sell,watch}
```

## GET /api/stocks/{code} → snap/stock_300908.json
```
meta: {code,name,sector,sector_idx,tags,float_cap,start_price,pe,market}
sector_heat(0-100), sector_policy(bool), sector_keywords[]
date, phase(null), role: '市场总龙'|'主线板块'|'普通'
feat: { close, run20_pct, run60_pct, dist_high60_pct, gain_low60_pct, vol20_pct, flat_ratio40,
        streak_max20, turnover_avg5, turnover_max60, seal_ratio, news_code_n, news_sector_n,
        sector_avg_pct, sector_zt_5d, sector_zt_today, one_word_today }
conds: { leader:{score,items[L-xx]}, buyang:{score,items[B-xx]}, qiehuan:{score,items[S-xx]} }
signals[]: 同 /api/signals 结构
kline[]: 最近300根(旧→新): { date,open,high,low,close,pct,turnover,amount,volume,streak,limit_up,limit_down }
news[]: 个股+所属板块新闻(旧无序,date desc): { date,code,sector,title,sentiment(-1..1),source,kind }
```

## GET /api/dashboard/sectors → snap/sectors.json  rows[]（全板块,is_dragon_sector 标记）
## GET /api/dashboard/history?days=30 → {series: 同 overview.stats_history}
## GET /api/market/live → { rows[]:{code,name,price,pre_close,pct,high,low,amount,ts,base_pct}(按pct降序), tick_ts, date }
## GET /api/backtest/meta → { range:[首日,末日], modes:[{key,cn,phases}], note }
## POST /api/backtest/run  body {mode:'auto'|'leader'|'buyang'|'qiehuan', start:'YYYY-MM-DD', end:'YYYY-MM-DD'或'', capital:100000}
 → snap/backtest_auto.json:
```
stats: { mode, start,end,days, init_capital, final_equity, total_ret_pct, annual_ret_pct,
         max_drawdown_pct, win_rate_pct, profit_factor, trade_count, gross_profit, gross_loss,
         avg_win_pct, avg_loss_pct, by_strategy:{策略名:{n,win,pnl_sum}} }
equity[]: {date, value(基准=100起点)}, benchmark[]: {date,value(基准=100)}, drawdown[]: {date,value(-x%)}
trades[]: { code,name,strategy_cn,entry_date,exit_date,entry_px,exit_px,shares,pnl_pct,pnl_cash,reason,hold_days }
phases[]: { date, phase_cn }
```

# 页面负责人（两份任务并行）
A：`src/pages/DashboardPage.jsx`（仪表盘）、`src/pages/LeadersPage.jsx`（龙头榜）
B：`src/pages/PoolsPage.jsx`（股票池）、`src/pages/SignalsPage.jsx`（信号看板）、`src/pages/StockPage.jsx`（个股分析）、`src/pages/BacktestPage.jsx`（回测中心）

需求要点（详见各任务卡描述）。完成后在最终回复里列出你创建的文件与实现要点；无需运行构建。

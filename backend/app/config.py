"""全局配置：环境变量可覆盖，默认开箱即用的模拟数据源模式。"""
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 项目根
DATA_DIR = os.path.join(BASE_DIR, "data")
# 数据源: mock=内置确定性模拟市场(离线演示) | real=新浪系公开接口实时实盘(默认)
DATA_SOURCE = os.environ.get("DATA_SOURCE", "real")
_db_name = "market_real.sqlite3" if DATA_SOURCE == "real" else "market.sqlite3"
DB_PATH = os.environ.get("KB_DB_PATH", os.path.join(DATA_DIR, _db_name))
MOCK_SEED = int(os.environ.get("MOCK_SEED", "920202406"))
# 模拟交易日数（含最近一日），默认约 2.1 年。522 天落在"高位震荡-低位补涨首板日"，演示效果最佳
MOCK_TRADING_DAYS = int(os.environ.get("MOCK_TRADING_DAYS", "522"))

# ---- real 参数(新浪+腾讯双源) ----
REAL_POLL_SECONDS = float(os.environ.get("REAL_POLL_SECONDS", "6"))      # 交易时段轮询间隔
REAL_FULL_EVERY = int(os.environ.get("REAL_FULL_EVERY", "18"))           # 每N次tick做一次全量同步(成分/新增股)
REAL_PREF = os.environ.get("REAL_PREF", "")                              # 强制快速源: tencent | sina_hq (留空自动择优)
REAL_QUOTE_URL = os.environ.get("REAL_QUOTE_URL",
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData")
REAL_HQ_URL = "https://hq.sinajs.cn/list={symbols}"
REAL_KLINE_URL = ("https://quotes.sina.cn/cn/api/json_v2.php/"
                  "CN_MarketDataService.getKLineData?symbol={sym}&scale=240&ma=no&datalen={n}")
# 分析/回测/历史使用的“实时样本池”：按当日成交额取前 N 只(真实历史行情/情绪历史/回测仅覆盖样本池，
# 全市场当日实时统计另做大盘卡展示)。接入更完整数据后可调大(建议 500~1500)。
REAL_CRAWL_N = int(os.environ.get("REAL_CRAWL_N", "500"))
REAL_CRAWL_DAYS = int(os.environ.get("REAL_CRAWL_DAYS", "520"))
REAL_CRAWL_CONCURRENCY = int(os.environ.get("REAL_CRAWL_CONCURRENCY", "8"))
# 行业成员关系缓存刷新间隔(秒)
REAL_INDUSTRY_REFRESH = int(os.environ.get("REAL_INDUSTRY_REFRESH", "7200"))

# 真实市场情绪规则阈值（全市场约5000+口径，经验值，可按需校准）
# 判定状态机与 mock 一致：主跌 → 试错 → 主升 → 高位震荡
REAL_RULE = {
    "dt_decline_min": 80,        # 单日跌停家数阈值(全市场)
    "zt_boom_min": 60,           # 赚钱效应涨停家数阈值
    "ladder_ascend_min": 5,      # 主升连板高度
    "premium_probe_min": 1.5,    # 昨涨停今表现阈值
    "mean_strong": 0.5,          # 上证指数涨跌幅阈值(弱化权重)
}

# 服务端口 / 主机
HOST = os.environ.get("KB_HOST", "127.0.0.1")
PORT = int(os.environ.get("KB_PORT", "8720"))

# 盘中模拟 tick 间隔(秒)：用于仪表盘"实时行情"动态效果
MOCK_TICK_SECONDS = float(os.environ.get("MOCK_TICK_SECONDS", "6"))

# 模拟市场规则引擎阈值（针对 48 只模拟股票的小市场刻度）
# 换到真实全市场数据时，按股票总数比例放大这些阈值即可复用同一套引擎。
RULE = {
    "dt_decline_min": 6,          # 单日跌停>=6 且连续 2 日 → 触发主跌判定
    "zt_boom_min": 9,             # 单日涨停>=9 → 赚钱效应强
    "premium_probe_min": 2.0,     # 昨日涨停今日平均溢价率 > 2% → 试错期特征
    "ladder_ascend_min": 5,       # 最高连板 >= 5 → 主升特征
    "avg_pct_strong": 1.5,        # 全市场平均涨幅(指数强度代理)
}

PHASE_POSITION = {  # 情绪周期 → 建议总仓位/模式（来自 92 策略体系）
    "main_decline": {"pct": [0, 10], "mode": "空仓为主,尾盘可轻仓博弈修复", "label": "主跌阶段"},
    "probe": {"pct": [0, 20], "mode": "小仓试错新题材首板(切换战法)", "label": "低位震荡/试错期"},
    "main_ascend": {"pct": [70, 100], "mode": "分歧买龙头,重仓主升(龙头战法)", "label": "主升阶段"},
    "high_oscillate": {"pct": [0, 10], "mode": "轻仓补涨,不博弈穿越(补涨战法)", "label": "高位震荡"},
}
PHASE_ORDER = ["main_decline", "probe", "main_ascend", "high_oscillate"]

# 风控常量
RISK = {
    "single_position_max": 0.25,   # 单票仓位上限 25%
    "stop_loss": 0.05,             # 单笔 -5% 无条件止损
    "leverage": False,             # 永不使用杠杆
    "fees_rate": 0.0015,           # 单边交易成本(佣金+滑点近似)
}

# 企业微信群机器人 Webhook(可选): 买点入池等事件推送微信。
# 留空则不推送。也可把 webhook 写入 data/wechat_webhook.txt(一行一个, 运行期, 不入git) 或 DB meta 'wechat_webhook'。
WECHAT_WEBHOOK = os.environ.get("WECHAT_WEBHOOK", "").strip()

# 推送消息里的“点击直达”页面地址(企业微信 markdown 超链接)。
# 默认打板操作台公网地址; 不同部署可用环境变量 WECHAT_PAGE_URL 覆盖, 留空=纯文本不附加链接。
WECHAT_PAGE_URL = os.environ.get("WECHAT_PAGE_URL",
                                  "https://wangchaoqun.top/92kebi/#/ops").strip()

os.makedirs(DATA_DIR, exist_ok=True)

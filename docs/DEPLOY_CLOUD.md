# 云服务器部署指南（92K 打板决策台 · 实盘模式）

目标：把后端**常驻云服务器**，实现——
1. **A股交易时段自动监测全市场**：进程内调度循环独立于浏览器运行，每 ~6s 轮询全市场快照、每 ~10s 重算信号并把买点/卖点自动放入 买入池/卖出池（网页不开也照常执行）；
2. **微信推送不依赖网页**：推送由后端直接调企业微信群机器人 webhook（出站 HTTPS），只要进程存活即送达。

---

## 一、运行架构（为什么“不打开网页也能跑”）

```
systemd(92kebi.service, Restart=always)
 └─ python3 run.py  →  uvicorn(FastAPI)
     ├─ lifespan 启动: 行情快照→行业映射→样本选池→(首次)日K回填
     └─ 进程内 4 个 asyncio 调度循环(与 HTTP 请求无关):
         ├─ poll    : 交易时段每 6s 轮询全市场快照(新浪/腾讯双源)
         ├─ analysis: 每 10s 重算情绪视图 + 打板扫描(买/卖点 → 入池 + 微信推送)
         ├─ daily   : 北京时间跨日 → 盘后(15:00+)重选样本回填日K + 复盘
         └─ watchdog: 每 30s 巡检, 任一循环意外退出自动重建(进程看门狗)
```

前端页面只是“查看器”。服务端所有逻辑都在 uvicorn 进程内；systemd 保证进程常驻与崩溃自启。

## 二、服务器要求
- Ubuntu 20.04+/Debian 11+（其他 Linux 类似），Python 3.10+（推荐 3.12）
- 出站可访问：`hq.sinajs.cn`、`vip.stock.finance.sina.com.cn`、`qt.gtimg.cn`、`qyapi.weixin.qq.com`（国内云服务器默认均可）
- 内存 ≥1GB（建议 2GB，行情快照 + SQLite 缓存），磁盘 ≥10GB
- **无需 Node.js**（前端已构建进 `backend/app/static`）

## 三、部署步骤
```bash
# 1. 取代码(本机示例部署目录为 /root/92KeBi; 换成你自己的绝对路径即可)
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
cd /root
git clone git@github.com:wangchaoqunnnn/92KeBi.git   # 或用 HTTPS 免密
cd 92KeBi

# 2. 建虚拟环境并装依赖(依赖很少: fastapi + uvicorn)
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 放置运行期密钥/数据(不入 git)
mkdir -p data
# 微信机器人 webhook → 必须写这个文件(一行一个 URL; # 开头为注释)
# 把本机的 data/wechat_webhook.txt 内容复制过去:
nano data/wechat_webhook.txt
#    例: https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=xxxx

# (可选)环境变量(不设置即走默认 real + 8720):
#    cp .env.example .env  并在其中按需修改 REAL_POLL_SECONDS / REAL_CRAWL_N 等
#     KB_HOST 保持 127.0.0.1, 对外走 nginx; 也可改 0.0.0.0 直开端口
#    WECHAT_PAGE_URL=https://你的域名/92kebi/#/ops
#       ← 推送消息“点击直达”地址。代码不写死绝对地址: 留空=自动按用户访问来源推导
#         (子路径部署时 nginx 已回传 X-Forwarded-Prefix, 无需此变量);
#         如无 nginx 直连或想固定, 才用此环境变量显式指定。

# 4. 首次启动验证(会做首轮行情同步; 首次日K回填约 2~5 分钟)
.venv/bin/python -X utf8 run.py
#  看到 “实盘就绪: … 全市场 涨停… 跌停…” 即 OK, Ctrl+C 退出

# 5. 注册 systemd 守护(开机自启 + 崩溃自启)
sudo cp deploy/92kebi.service /etc/systemd/system/
# 注意: 92kebi.service 内 WorkingDirectory/ExecStart 已按 /root/92KeBi 写好;
# 若实际路径不同, 用下面 sed 一键替换(把 /root/92KeBi 换成你的绝对路径):
sudo sed -i 's#/root/92KeBi#你的绝对路径#g' /etc/systemd/system/92kebi.service
sudo systemctl daemon-reload
sudo systemctl enable --now 92kebi
systemctl status 92kebi

# 6. (可选) nginx 反代 + 开放端口
sudo cp deploy/nginx.92kebi.conf /etc/nginx/conf.d/92kebi.conf   # 改域名/IP
sudo nginx -t && sudo systemctl reload nginx
# 或直连: sudo ufw allow 8720/tcp
```

## 四、部署后自检（全部建议跑一遍）
```bash
# a) 健康检查(进程/时区/调度循环/行情/交易窗口/微信)
curl -s http://127.0.0.1:8720/api/health
#   关注: scheduler.running=true, tasks 含 kb-poll/kb-analysis/kb-daily/kb-watchdog,
#         cn_time 为北京时间, market.quote_date=最近交易日

# b) 调度详情(轮询次数/样本进度/数据源延迟)
curl -s http://127.0.0.1:8720/api/admin/status | python3 -m json.tool

# c) 微信连通性(应返回 sent:1)
curl -s -X POST http://127.0.0.1:8720/api/ops/push-test

# d) 立即手动跑一次打板扫描(应返回 state:done; 交易时段才会产生买/卖入池)
curl -s -X POST http://127.0.0.1:8720/api/ops/flush

# e) 看实时日志(确认盘中 poll/analysis 在跑、有无报错)
journalctl -u 92kebi -f --no-hostname
```

## 五、时间与行情口径（重要）
- 代码**强制北京时间**（`cn_time` + 启动时钉 TZ=Asia/Shanghai），服务器无论设 UTC 还是别的时区都正确；
- 自动买卖只在 **交易日(周一到周五) 09:25–14:59 北京时间**窗口内执行（非交易时段入池/结算暂停，观察池照常记录）：
  - 买点=规则信号 → 买入池 + 微信推送；
  - 卖点/止损-5% → 结算进卖出池 + 微信推送（含盈亏）；
  - 手动了结/删除卖出池记录同样即时推送。
- 周末/节假日无 A股行情，服务处于低频待命（每 600s 一次轻轮询），开盘日自动恢复高频。

## 六、首次上线建议（重要）
- 首次部署若在**开盘前**（如 08:00–09:15）启动：启动时会自动拉 500 只样本的 520 日日K（约 2~5 分钟），**请务必在 09:25 前启动完成**；若来不及，可提前一天盘后启动，让其完成回填并跨日进入次日交易。
- 验证“网页不打开也能入池/推送”的完整流程（建议交易时段做）：
  1. 不开浏览器，仅保留 systemd 服务；
  2. 等待盘中出现买/卖点（或盘中手动触发 `/api/ops/flush`）；
  3. 观察微信群消息 + `journalctl` 日志（应出现 `wechat push ok`）；
  4. 之后再打开网页 → 打板台能看到已入池记录。

## 七、备份与恢复
```bash
# 数据全在 data/ 目录(sqlite + 分时档案 + 行业缓存), 定时打包即可
tar -czf 92kebi_data_$(date +%F).tgz /root/92KeBi/data
# 恢复: 解压回 /root/92KeBi/data 后 systemctl restart 92kebi
```
注意：`data/wechat_webhook.txt` 属敏感配置，备份包注意保密，勿提交 git。

## 八、常见故障排查
| 症状 | 排查 |
|---|---|
| 微信收不到 | `POST /api/ops/push-test` 看返回；`errcode=93000` = webhook key 失效，重发新 key；`sent:0` 检查服务器能否访问 qyapi（防火墙/代理） |
| 盘中没入池 | 看 `api/health` 的 `ops_window.open` 是否 true；`admin/status` 的 `poll_count/last_poll` 是否在增长；日志有无 `real poll error` |
| 服务反复重启 | `journalctl -u 92kebi -n 100` 看退出原因；常见为内存不足(OOM)或磁盘写满 |
| 页面打不开 | 确认端口/nginx；`curl -I http://127.0.0.1:8720/` |
| 时区不对 | `curl -s http://127.0.0.1:8720/api/health` 看 `cn_time`；确认用的是新版本代码 |

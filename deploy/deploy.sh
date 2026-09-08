#!/usr/bin/env bash
# =============================================================================
# 92K 打板决策台 —— 全新服务器一键部署脚本 (Ubuntu/Debian, Python3>=3.10)
#
# 用法:
#   sudo bash deploy/deploy.sh                 # 默认: /root/92KeBi, 无 nginx 域名
#   sudo bash deploy/deploy.sh wangchaoqun.top # 指定对外域名(nginx 子路径 /92kebi/)
#
# 常用覆盖(环境变量):
#   DEPLOY_DIR=/opt/92KeBi  REPO=git@github.com:wangchaoqunnnn/92KeBi.git \
#   WEBHOOK_FILE=/root/old.txt bash deploy/deploy.sh mydomain.com
#
# 说明:
#   - 幂等: 目录已存在则 git pull; 重复执行会重新 daemon-reload + restart
#   - 不会覆盖已存在的 data/wechat_webhook.txt / .env / 已有 nginx 配置段
#   - webhook 若未提供(WEBHOOK_FILE), 脚本结束前会提醒手动填写
# =============================================================================
set -euo pipefail

# ----------------------------- 可配置参数 -----------------------------
DOMAIN="${1:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/root/92KeBi}"
REPO="${REPO:-git@github.com:wangchaoqunnnn/92KeBi.git}"
PY_BIN="${PY_BIN:-python3}"
WEBHOOK_FILE="${WEBHOOK_FILE:-}"          # 旧服务器导出的 wechat_webhook.txt 路径(可选)
MEM_LIMIT_MB="${MEM_LIMIT_MB:-1200}"
WECHAT_PAGE_URL="${WECHAT_PAGE_URL:-}"    # 留空自动按 DOMAIN 生成 /92kebi/#/ops

SRV=92kebi
SRV_FILE=/etc/systemd/system/${SRV}.service
NGINX_CONF=/etc/nginx/conf.d/${SRV}.conf

log()  { printf '\033[1;36m[92k]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[92k ERROR]\033[0m %s\n' "$*" >&2; exit 1; }

command -v "${PY_BIN}" >/dev/null || die "未找到 ${PY_BIN}, 请先: apt install python3"
"${PY_BIN}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "Python 需要 >=3.10"

# ----------------------------- 1. 取代码 -----------------------------
if [ -d "${DEPLOY_DIR}/.git" ]; then
  log "代码已存在, git pull 更新"
  git -C "${DEPLOY_DIR}" pull --ff-only
else
  mkdir -p "$(dirname "${DEPLOY_DIR}")"
  log "克隆代码 → ${DEPLOY_DIR}"
  git clone "${REPO}" "${DEPLOY_DIR}"
fi
cd "${DEPLOY_DIR}"

# ----------------------------- 2. 虚拟环境 + 依赖 -----------------------------
if [ ! -x "${DEPLOY_DIR}/.venv/bin/python" ]; then
  log "创建虚拟环境"
  "${PY_BIN}" -m venv "${DEPLOY_DIR}/.venv"
fi
log "安装依赖"
"${DEPLOY_DIR}/.venv/bin/pip" install -q -r requirements.txt

# ----------------------------- 3. 运行期数据 / 密钥 -----------------------------
mkdir -p "${DEPLOY_DIR}/data/intraday"
if [ -n "${WEBHOOK_FILE}" ] && [ -f "${WEBHOOK_FILE}" ] && \
   [ ! -s "${DEPLOY_DIR}/data/wechat_webhook.txt" ]; then
  log "复制 webhook 文件 ← ${WEBHOOK_FILE}"
  cp "${WEBHOOK_FILE}" "${DEPLOY_DIR}/data/wechat_webhook.txt"
fi

# 若 WEBHOOK_FILE 指向“旧项目整个 data 目录”, 复制 sqlite 档案(可选迁移)
if [ -n "${WEBHOOK_FILE}" ] && [ -d "${WEBHOOK_FILE}" ]; then
  log "从目录迁移 data 内容 ← ${WEBHOOK_FILE}"
  cp -n "${WEBHOOK_FILE}"/market_real.sqlite3 "${DEPLOY_DIR}/data/" 2>/dev/null || true
  cp -rn "${WEBHOOK_FILE}"/intraday/. "${DEPLOY_DIR}/data/intraday/" 2>/dev/null || true
fi

# ----------------------------- 4. .env(不存在才生成, 不覆盖已有) -----------------------------
ENV_FILE="${DEPLOY_DIR}/.env"
touch "${ENV_FILE}"
grep -q '^DATA_SOURCE=' "${ENV_FILE}" || printf 'DATA_SOURCE=real\n' >> "${ENV_FILE}"
if [ -n "${DOMAIN}" ] && [ "${DOMAIN}" != "IP" ]; then
  PUSH="${WECHAT_PAGE_URL:-https://${DOMAIN}/92kebi/#/ops}"
  grep -q '^WECHAT_PAGE_URL=' "${ENV_FILE}" \
    || printf 'WECHAT_PAGE_URL=%s\n' "${PUSH}" >> "${ENV_FILE}"
fi
grep -q '^MEM_LIMIT_MB=' "${ENV_FILE}" \
  || printf 'MEM_LIMIT_MB=%s\n' "${MEM_LIMIT_MB}" >> "${ENV_FILE}"
log ".env 就绪(DATA_SOURCE=real, MEM_LIMIT_MB=${MEM_LIMIT_MB})"

# ----------------------------- 5. systemd 服务 -----------------------------
log "安装 systemd 服务(${SRV})"
sed -e "s#WorkingDirectory=/root/92KeBi#WorkingDirectory=${DEPLOY_DIR}#" \
    -e "s#ExecStart=/root/92KeBi/.venv/bin/python3#ExecStart=${DEPLOY_DIR}/.venv/bin/python3#" \
    "${DEPLOY_DIR}/deploy/92kebi.service" > "${SRV_FILE}"
systemctl daemon-reload
systemctl enable "${SRV}" >/dev/null 2>&1 || true
log "启动服务(首次会做样本日K回填 2~5 分钟, 端口稍后可用)"
systemctl restart "${SRV}"

# ----------------------------- 6. nginx(可选: 传入域名才配) -----------------------------
if [ -n "${DOMAIN}" ] && [ "${DOMAIN}" != "IP" ] && command -v nginx >/dev/null 2>&1; then
  if [ ! -f "${NGINX_CONF}" ]; then
    log "配置 nginx 子路径 /92kebi/ → 域名 ${DOMAIN}"
    sed "s/你的域名或服务器IP;/${DOMAIN};/" \
      "${DEPLOY_DIR}/deploy/nginx.92kebi.conf" > "${NGINX_CONF}"
    nginx -t && systemctl reload nginx || log "警告: nginx -t 失败, 请检查 ${NGINX_CONF}"
  else
    log "nginx 配置已存在, 跳过: ${NGINX_CONF}"
  fi
else
  log "未配置 nginx; 若需直连请开端口: ufw allow 8720/tcp"
fi

# ----------------------------- 7. 自检 -----------------------------
log "自检 /api/health(等待初始化完成, 最长 5 分钟)"
OK=""
for i in $(seq 1 60); do
  if curl -sf -m 5 http://127.0.0.1:8720/api/health >/dev/null 2>&1; then OK=1; break; fi
  sleep 5
done
if [ -n "${OK}" ]; then
  log "健康检查通过:"
  curl -s http://127.0.0.1:8720/api/health \
    | "${DEPLOY_DIR}/.venv/bin/python" -c \
      'import sys,json;d=json.load(sys.stdin);print("  data_source =",d.get("data_source"));print("  market =",(d.get("market") or {}).get("quote_date"));print("  push_page_url =",d.get("push_page_url"));print("  mem_mb(任务) =", list((d.get("scheduler") or {}).get("tasks",{}).keys()))'
else
  log "警告: 健康检查未通过, 请查看日志: journalctl -u ${SRV} -n 50 --no-pager"
fi

log "部署完成 ✅"
echo "---------------------------------------------------------------"
echo " 服务: systemctl status ${SRV} | journalctl -u ${SRV} -f"
if [ -n "${DOMAIN}" ] && [ "${DOMAIN}" != "IP" ]; then
  echo " 前端: https://${DOMAIN}/92kebi/"
else
  echo " 前端(本机): http://127.0.0.1:8720/"
  echo " 如需对外直连: sudo ufw allow 8720/tcp (或配 nginx/域名)"
fi
if [ ! -s "${DEPLOY_DIR}/data/wechat_webhook.txt" ]; then
  echo " ⚠ 尚未配置微信 webhook → 手动填入: nano ${DEPLOY_DIR}/data/wechat_webhook.txt"
fi
echo " 验证微信: curl -s -X POST http://127.0.0.1:8720/api/ops/push-test"
echo "---------------------------------------------------------------"

"""启动入口(本地/云服务器一致)：python run.py
- 先钉死进程时区为北京时间(Asia/Shanghai), 保证交易窗口/日期判断不随服务器时区漂移
- 再以 uvicorn 启动 FastAPI; 启动即完成 行情初始化 + 调度任务(盘中自动监测/打板入池/微信推送)
  —— 全部与浏览器无关: 网页不打开, 服务照常工作(由 systemd/进程守护保持常驻)。
"""
import os
import sys

_BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_BASE, "backend"))

from app.cn_time import init_process_timezone  # noqa: E402
init_process_timezone()                          # 必须在导入 app.config 之前

from app.config import HOST, PORT  # noqa: E402

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host=HOST, port=PORT, reload=False)

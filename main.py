import os
import sys
import logging
from dotenv import load_dotenv

# 将项目根目录添加到Python路径，以确保可以正确导入src模块
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.login import ScutChargeMonitor
from src.query import ChargeQuery
from src.analysis import UsageAnalyzer
from src.notify import NotificationManager

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    """应用主入口：加载配置，执行登录，然后查询电费。"""
    load_dotenv()

    # --- 加载和检查登录配置 ---
    login_config = {
        "username": os.environ.get("SCUT_USERNAME"),
        "password": os.environ.get("SCUT_PASSWORD"),
        "llm_model": os.environ.get("LLM_MODEL"),
        "llm_api_key": os.environ.get("LLM_API_KEY"),
        "llm_api_base": os.environ.get("LLM_API_BASE"),
        "llm_recognition_retries": int(os.environ.get("LLM_RECOGNITION_RETRIES", "1") or "1")
    }
    if not all(login_config.get(k) for k in ["username", "password", "llm_model", "llm_api_key"]):
        logging.error("登录配置不完整，请检查 .env 文件。")
        return



    # --- 运行核心流程 ---
    monitor = ScutChargeMonitor(**login_config)
    if monitor.login():
        logging.info("登录成功，正在获取用电历史记录...")
        
        # 创建查询实例并获取用电历史和实时剩余电量
        query_agent = ChargeQuery(monitor.session, monitor.jsessionid)
        usage_history, _ = query_agent.get_usage_history()
        left_quantity, _ = query_agent.get_realtime_balance()

        if usage_history is not None and left_quantity is not None:
            # 预测电量耗尽日期
            prediction = UsageAnalyzer.predict_runout_date(usage_history, left_quantity)
            
            # 检查低电量告警
            alert = UsageAnalyzer.check_low_balance_alert(left_quantity)
            
            # 根据分析结果，决定是否需要发送告警
            notification_manager = NotificationManager()
            notification_manager.dispatch_alert_if_needed(prediction, alert)
        else:
            logging.error("未能获取用电历史或剩余电量，无法进行分析。")
    else:
        logging.error("应用主流程失败，因为登录步骤未能成功。")

if __name__ == "__main__":
    main()

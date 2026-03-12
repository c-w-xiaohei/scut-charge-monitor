import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 将项目根目录添加到Python路径，以确保可以正确导入src模块
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from src.login import ScutChargeMonitor
from src.query import ChargeQuery
from src.analysis import UsageAnalyzer
from src.notify import NotificationManager


def setup_logging() -> Path:
    """终端输出详细日志，文件仅记录每次执行的一行摘要。"""
    project_root = Path(__file__).resolve().parent
    log_dir = project_root / "logs"
    log_dir.mkdir(exist_ok=True)

    log_file = log_dir / "execution-summary.log"
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    logging.basicConfig(
        level=logging.INFO,
        handlers=[stream_handler],
        force=True,
    )
    return log_file


def append_execution_summary(log_file: Path, result: str) -> None:
    """向日志文件追加一行执行结果摘要。"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with log_file.open("a", encoding="utf-8") as file:
        file.write(f"{timestamp} - {result}\n")

def main():
    """应用主入口：加载配置，执行登录，然后查询电费。"""
    log_file = setup_logging()
    execution_result = "失败：未知原因"

    try:
        load_dotenv()

        # --- 加载和检查登录配置 ---
        login_config = {
            "username": os.environ.get("SCUT_USERNAME"),
            "password": os.environ.get("SCUT_PASSWORD"),
            "llm_model": os.environ.get("LLM_MODEL"),
            "llm_api_key": os.environ.get("LLM_API_KEY"),
            "llm_api_base": os.environ.get("LLM_API_BASE"),
            "llm_recognition_retries": int(os.environ.get("LLM_RECOGNITION_RETRIES", "1") or "1"),
        }
        if not all(login_config.get(k) for k in ["username", "password", "llm_model", "llm_api_key"]):
            execution_result = "失败：登录配置不完整"
            logging.error("登录配置不完整，请检查 .env 文件。")
            return

        # --- 运行核心流程 ---
        monitor = ScutChargeMonitor(**login_config)
        if monitor.login():
            logging.info("登录成功，正在获取用电历史记录...")

            # 创建查询实例并获取用电历史和剩余电量
            query_agent = ChargeQuery(monitor.session, monitor.jsessionid)
            usage_history, left_quantity = query_agent.get_usage_history()

            if usage_history is not None and left_quantity is not None:
                # 预测电量耗尽日期
                prediction = UsageAnalyzer.predict_runout_date(usage_history, left_quantity)

                # 检查低电量告警
                alert = UsageAnalyzer.check_low_balance_alert(left_quantity)

                # 根据分析结果，决定是否需要发送告警
                notification_manager = NotificationManager()
                notification_manager.dispatch_alert_if_needed(prediction, alert)
                execution_result = f"成功，剩余电量 {left_quantity} 度"
            else:
                execution_result = f"失败：{query_agent.last_error}"
                logging.error("未能获取用电历史或剩余电量，无法进行分析。")
        else:
            execution_result = f"失败：{monitor.last_error}"
            logging.error("应用主流程失败，因为登录步骤未能成功。")
    except Exception as exc:
        execution_result = f"失败：{exc}"
        logging.exception("执行过程中发生未处理异常。")
        raise
    finally:
        append_execution_summary(log_file, execution_result)
        logging.info("本次执行结束：%s", execution_result)

if __name__ == "__main__":
    main()

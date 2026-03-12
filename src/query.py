import requests
import logging
import json

class ChargeQuery:
    """负责处理用电历史查询操作。"""

    # 用电历史查询URL
    USAGE_HISTORY_URL = 'https://dfyc.utc.scut.edu.cn/sdms-weixin-pay-sp/service/ele/list'

    def __init__(self, monitor_session, jsessionid: str | None = None):
        """
        使用一个已经通过认证的 ScutChargeMonitor 实例的会话进行初始化。
        :param monitor_session: 一个包含有效token和cookie的requests.Session对象。
        :param jsessionid: 登录阶段捕获到的JSESSIONID，用于用电历史查询。
        """
        if not hasattr(monitor_session, 'headers'):
            raise ValueError("传入的会话对象无效。")
        self.session = monitor_session
        self.jsessionid = jsessionid
        self.last_error = "查询失败"

    def get_usage_history(self):
        """
        查询近几日的用电历史记录。
        :return: 一个元组 (history, left_quantity)，包含历史记录列表和剩余电量。失败则返回 (None, None)。
        """
        logging.info("正在查询用电历史记录...")
        # 按文档固定idCode=1001
        params = {'idCode': '1001'}
        response = None
        try:
            # 显式携带JSESSIONID（该cookie属于dfyc域，登录阶段已捕获其值）
            if not self.jsessionid:
                self.last_error = "缺少 JSESSIONID"
                logging.warning("未找到 JSESSIONID，用电历史查询可能失败。")
            cookie_dict = {'JSESSIONID': self.jsessionid} if self.jsessionid else None
            response = self.session.get(self.USAGE_HISTORY_URL, params=params, cookies=cookie_dict)
            response.raise_for_status()
            data = response.json()

            if data.get('statusCode') == '200' and 'resultObject' in data:
                history = data['resultObject']
                if isinstance(history, list) and len(history) > 0:
                    # 从第一条记录中提取剩余电量
                    left_quantity_str = history[0].get('leftEleQuantity', '0')
                    left_quantity = float(left_quantity_str)
                    logging.info(f"成功获取 {len(history)} 条用电历史记录，剩余电量: {left_quantity} 度。")
                    return history, left_quantity
                else:
                    logging.info("用电历史记录为空。")
                    # 如果没有历史记录，我们无法知道剩余电量，返回0
                    return [], 0.0
            self.last_error = f"查询失败：{data.get('message', '返回数据格式不符合预期')}"

        except json.JSONDecodeError:
            self.last_error = "查询响应不是有效的 JSON"
            logging.error(f"解析用电历史响应失败，响应内容非JSON格式：{response.text if response else 'N/A'}")
        except Exception as e:
            self.last_error = f"查询用电历史失败：{e}"
            logging.error(f"查询用电历史时发生未知错误: {e}")

        return None, None

import logging
import os
import smtplib
import re
from email.mime.text import MIMEText
from abc import ABC, abstractmethod

# --- 抽象通知基类 ---
class NotificationChannel(ABC):
    """通知渠道的抽象基类。"""
    @abstractmethod
    def send(self, subject: str, body: str) -> bool:
        """发送通知的抽象方法。"""
        pass

# --- 邮件通知实现 ---
class EmailNotifier(NotificationChannel):
    """通过邮件发送通知。"""
    def __init__(self):
        self.smtp_server = os.getenv("EMAIL_SMTP_SERVER")
        self.smtp_port = int(os.getenv("EMAIL_SMTP_PORT", 587))
        self.smtp_user = os.getenv("EMAIL_SMTP_USER")
        self.smtp_password = os.getenv("EMAIL_SMTP_PASSWORD")
        # 支持多收件人：优先 EMAIL_RECIPIENTS，其次 EMAIL_RECIPIENT（单个）
        recipients_raw = os.getenv("EMAIL_RECIPIENTS") or os.getenv("EMAIL_RECIPIENT")
        self.recipient_emails = self._parse_recipients(recipients_raw)

        if not all([self.smtp_server, self.smtp_user, self.smtp_password]) or not self.recipient_emails:
            raise ValueError("邮件通知服务配置不完整，请检查 .env 文件中的 EMAIL_* 变量。")

    @staticmethod
    def _parse_recipients(value: str | None) -> list[str]:
        """
        将收件人字符串解析为列表，支持分隔符：逗号、分号、空格、换行；会去重并去除空白。
        示例："a@x.com, b@x.com; c@x.com  d@x.com" -> ["a@x.com","b@x.com","c@x.com","d@x.com"]
        """
        if not value:
            return []
        parts = [p.strip() for p in re.split(r"[\s,;]+", value) if p and p.strip()]
        # 去重并保持顺序
        return list(dict.fromkeys(parts))

    def send(self, subject: str, body: str) -> bool:
        # 在方法入口处再次检查，确保类型安全
        if not all([self.smtp_server, self.smtp_user, self.smtp_password]) or not self.recipient_emails:
            logging.error("邮件发送前检查失败：配置不完整。")
            return False

        # 类型断言：经过上面的检查，这些值不会是 None
        assert self.smtp_server is not None
        assert self.smtp_user is not None  
        assert self.smtp_password is not None
        assert isinstance(self.recipient_emails, list) and len(self.recipient_emails) > 0

        to_header = ", ".join(self.recipient_emails)
        logging.info(f"正在通过邮件向 {to_header} 发送告警...主题: {subject}")
        msg = MIMEText(body, 'plain', 'utf-8')
        msg['From'] = self.smtp_user
        msg['To'] = to_header
        msg['Subject'] = subject

        try:
            if self.smtp_port in (465, 994):
                server_cm = smtplib.SMTP_SSL(self.smtp_server, self.smtp_port)
            else:
                server_cm = smtplib.SMTP(self.smtp_server, self.smtp_port)
            with server_cm as server:
                if self.smtp_port == 587:
                    server.starttls()
                server.login(self.smtp_user, self.smtp_password)
                # 显式传入收件人列表，确保所有人都能收到
                server.send_message(msg, to_addrs=self.recipient_emails)
                logging.info("邮件告警发送成功！")
                return True
        except Exception as e:
            logging.error(f"发送邮件失败: {e}")
            return False

# --- 日志通知实现 (用于调试和默认行为) ---
class LogNotifier(NotificationChannel):
    """将通知内容输出到日志。"""
    def send(self, subject: str, body: str):
        logging.info(f"--- {subject} ---\n{body}\n--------------------")
        return True

# --- 通知管理器 ---
class NotificationManager:
    """管理通知的发送逻辑和渠道。"""
    def __init__(self):
        self.channels = []
        # 如果配置了邮件，则启用邮件通知（兼容 EMAIL_RECIPIENTS/EMAIL_RECIPIENT）
        has_basic = all(os.getenv(k) for k in ["EMAIL_SMTP_SERVER", "EMAIL_SMTP_USER", "EMAIL_SMTP_PASSWORD"])
        has_recipients = bool(os.getenv("EMAIL_RECIPIENTS") or os.getenv("EMAIL_RECIPIENT"))
        if has_basic and has_recipients:
            try:
                self.channels.append(EmailNotifier())
                logging.info("检测到邮件配置，已启用邮件通知渠道。")
            except Exception as e:
                logging.warning(f"初始化邮件通知渠道失败，将回退到仅日志通知。原因：{e}")
        else:
            logging.info("未检测到完整的邮件配置，将仅使用日志进行通知。")
        
        # 始终保留日志通知
        self.channels.append(LogNotifier())

    def dispatch_alert_if_needed(self, prediction: dict | None, alert: dict | None):
        """
        根据规则判断是否需要发送告警，并分发给所有已配置的渠道。
        :param prediction: 预测结果字典。
        :param alert: 告警结果字典。
        """
        force_send = os.getenv("EMAIL_FORCE_SEND", "").lower() in ("1", "true", "yes")

        if not alert:
            logging.info("无告警信息，无需发送通知。")
            return

        # 告警规则：余额低于阈值 或 预计可用天数少于3天
        is_low_balance = alert.get('is_alert', False)
        is_urgent_runout = prediction and prediction.get('days_left', float('inf')) < 3

        if not force_send and not is_low_balance and not is_urgent_runout:
            logging.info("电量充足且不紧急，无需发送告警。")
            # 对于非告警情况，只使用日志记录完整报告
            subject, body = self._format_report(prediction, alert, is_alert=False)
            LogNotifier().send(subject, body)
            return

        # 生成告警信息并发送
        subject, body = self._format_report(prediction, alert, is_alert=is_low_balance or is_urgent_runout)
        for channel in self.channels:
            try:
                channel.send(subject, body)
            except Exception as e:
                logging.error(f"通过渠道 {type(channel).__name__} 发送通知时出错: {e}")

    def _format_report(self, prediction: dict | None, alert: dict | None, is_alert: bool) -> tuple[str, str]:
        """
        根据分析结果生成通知的标题和正文。
        :param is_alert: 标记是否为告警邮件。
        :return: (subject, body) 元组。
        """
        # 尽管调用上下文保证了 alert 不为 None，但为函数健壮性，在此添加检查
        if not alert:
            return "[错误] 无告警数据", "无法生成报告，因为缺少告警信息。"

        current_balance = alert.get('current_balance', 0.0)

        if is_alert:
            subject = f"[紧急] 宿舍电量告警 - 剩余 {current_balance:.2f} 度"
            body_lines = [f"请注意：宿舍电量即将耗尽，请尽快充值！"]
        else:
            subject = f"[信息] 宿舍电量报告 - 剩余 {current_balance:.2f} 度"
            body_lines = ["这是您的例行宿舍电量报告。"]

        body_lines.append("\n--- 当前状态 ---")
        status = "低于阈值" if alert.get('is_alert') else "正常"
        body_lines.append(f"- 剩余电量: {current_balance:.2f} 度 (状态: {status}) ")
        body_lines.append(f"- 告警阈值: {alert.get('threshold', 'N/A')} 度")

        if prediction:
            body_lines.append("\n--- 未来预测 ---")
            avg_consumption = prediction.get('avg_daily_consumption', 0.0)
            days_left = prediction.get('days_left', 0.0)
            predicted_date = prediction.get('predicted_date', 'N/A')
            body_lines.append(f"- 日均消耗: {avg_consumption:.2f} 度")
            body_lines.append(f"- 预计可用: {days_left:.1f} 天")
            body_lines.append(f"- 预计耗尽日期: {predicted_date}")
        
        body = "\n".join(body_lines)
        return subject, body

import requests
import hashlib
import base64
import json
import os
import logging
from io import BytesIO
from PIL import Image
from dotenv import load_dotenv

# --- 日志配置 ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

class ScutChargeMonitor:
    """华南理工大学校园统一支付平台电费监控器。"""

    # --- API端点常量 ---
    BASE_URL = 'https://ecardwxnew.scut.edu.cn'
    CAPTCHA_URL = f'{BASE_URL}/berserker-auth/oauth/captcha'
    KEYBOARD_URL_TEMPLATE = f'{BASE_URL}/berserker-secure/keyboard?type=Standard&order=0&synAccessSource=h5&key={{key}}'
    LOGIN_URL = f'{BASE_URL}/berserker-auth/oauth/token'


    def __init__(self, username, password, llm_model, llm_api_key, llm_api_base=None, llm_recognition_retries=1):
        self.username = username
        self.password = password
        self.llm_model = llm_model
        self.llm_api_key = llm_api_key
        self.llm_api_base = llm_api_base
        # 识别重试次数（非网络层）：若一次识别得到的所有验证码候选均失败，则整体重试
        try:
            self.llm_recognition_retries = int(llm_recognition_retries) if llm_recognition_retries is not None else 1
            if self.llm_recognition_retries < 1:
                self.llm_recognition_retries = 1
        except (TypeError, ValueError):
            self.llm_recognition_retries = 1
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36',
            'Authorization': 'Basic bW9iaWxlX3NlcnZpY2VfcGxhdGZvcm06bW9iaWxlX3NlcnZpY2VfcGxhdGZvcm1fc2VjcmV0'
        })
        self.token = None
        self.login_data = None  # 用于存储完整的登录响应
        self.keyboard_info = None
        self.jsessionid = None  # 用于存储会话Cookie
        self.REDIRECT_URL = 'https://ecardwxnew.scut.edu.cn/berserker-base/redirect'
        self.last_error = "登录失败"

    def _get_captcha_data(self):
        """获取验证码的key和Base64编码的图像数据。"""
        logging.info("正在获取验证码和Key...")
        try:
            response = self.session.get(self.CAPTCHA_URL)
            response.raise_for_status()
            data = response.json()
            captcha_key = data.get('key')
            captcha_image_base64 = data.get('image').split(',')[-1]
            logging.info("获取验证码和Key成功。")
            return captcha_key, captcha_image_base64
        except (requests.exceptions.RequestException, json.JSONDecodeError, KeyError, AttributeError) as e:
            self.last_error = f"获取验证码失败：{e}"
            logging.error(f"获取验证码失败: {e}")
            return None, None

    def _fetch_keyboard_info(self, captcha_key):
        """获取与指定验证码key配对的键盘加密映射表。"""
        keyboard_url = self.KEYBOARD_URL_TEMPLATE.format(key=captcha_key)
        logging.info("正在动态获取键盘加密清单...")
        try:
            response = self.session.get(keyboard_url)
            response.raise_for_status()
            data = response.json()
            if data.get('code') == 200 and data.get('success'):
                self.keyboard_info = data.get('data')
                logging.info("键盘加密清单获取成功！")
                return self.keyboard_info
            else:
                self.last_error = f"获取键盘加密清单失败：{data.get('msg', '未知错误')}"
                logging.error(f"获取键盘加密清单失败: {data.get('msg', '未知错误')}")
                return None
        except (requests.exceptions.RequestException, json.JSONDecodeError) as e:
            self.last_error = f"获取键盘加密清单失败：{e}"
            logging.error(f"获取键盘加密清单网络请求失败: {e}")
            return None

    def _custom_encrypt(self, plain_password):
        """
        使用动态键盘信息进行字符替换加密。
        加密逻辑基于字符在标准键盘布局上的物理位置进行映射。
        """
        if not self.keyboard_info or 'numberKeyboard' not in self.keyboard_info:
            self.last_error = "键盘加密信息不可用"
            logging.error("键盘加密信息不完整或未初始化，无法加密。")
            raise ValueError("键盘加密信息不可用")

        logging.info("正在使用动态清单加密密码...")
        standard_layout = {
            'number': "0123456789", 'lower': "abcdefghijklmnopqrstuvwxyz", 'upper': "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        }
        dynamic_layout = {
            'number': self.keyboard_info['numberKeyboard'],
            'lower': self.keyboard_info['lowerLetterKeyboard'],
            'upper': self.keyboard_info['upperLetterKeyboard']
        }
        char_map = {}
        for key in standard_layout:
            for i, standard_char in enumerate(standard_layout[key]):
                if i < len(dynamic_layout[key]):
                    char_map[standard_char] = dynamic_layout[key][i]

        encrypted_password = "".join(char_map.get(char, char) for char in plain_password)
        encrypted_string = f"{encrypted_password}$1${self.keyboard_info['uuid']}"
        logging.info("密码加密完成。")
        return encrypted_string

    def _recognize_captcha(self, captcha_image_base64):
        """使用LLM识别验证码，获取多个候选结果并启用重试。"""
        logging.info("正在调用LLM识别验证码（将重试3次）...")
        response_text = ""  # 初始化以修复linter警告
        try:
            from litellm import completion

            img_str = base64.b64encode(base64.b64decode(captcha_image_base64)).decode('utf-8')
            prompt_text = (
                "分析这张验证码图片。验证码由英文字母和数字构成。请返回一个包含3个最可能结果的JSON数组，按可能性从高到低排序。"
                '例如：[\"abcd\", \"abce\", \"abcf\"]。请严格遵守JSON格式，不要包含任何其他说明文字。'
            )
            response = completion(
                model=self.llm_model, api_key=self.llm_api_key, api_base=self.llm_api_base,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_str}"}}
                ]}],
                num_retries=3
            )
            choices = getattr(response, 'choices', None)
            first_choice = choices[0] if choices and len(choices) > 0 else None
            first_message = getattr(first_choice, 'message', None) if first_choice else None
            first_content = getattr(first_message, 'content', None) if first_message else None
            if not (response and first_content):
                self.last_error = "LLM调用失败或返回无效响应"
                logging.error("LLM调用失败或返回了无效的响应。")
                return None

            response_text = str(first_content).strip()
            logging.info(f"LLM返回的原始结果: {response_text}")

            if response_text.startswith('```json'):
                response_text = response_text[7:-3].strip()
            elif response_text.startswith('```'):
                response_text = response_text[3:-3].strip()
            
            return json.loads(response_text)
        except json.JSONDecodeError:
            logging.warning(f"无法将LLM响应解析为JSON，将把其作为唯一候选: {response_text}")
            return [response_text]
        except Exception as e:
            self.last_error = f"识别验证码失败：{e}"
            logging.error(f"识别验证码时发生严重错误: {e}", exc_info=True)
            return None

    def _perform_auth_redirect(self):
        """
        在主登录成功后，执行必要的重定向以从dfyc域获取JSESSIONID。
        """
        logging.info("正在执行认证重定向以获取 JSESSIONID...")
        if not self.token:
            self.last_error = "缺少 access_token，无法执行认证重定向"
            logging.error("没有有效的 access_token，无法执行重定向。")
            return False
        
        params = {
            'appId': '360',
            'loginFrom': 'h5',
            'synAccessSource': 'h5',
            'synjones-auth': self.token,
            'type': 'app'
        }
        
        redirect_response = None
        try:
            # 我们不关心最终内容，只关心cookie是否被设置，所以禁止重定向，手动处理
            redirect_response = self.session.get(self.REDIRECT_URL, params=params, allow_redirects=False)
            redirect_response.raise_for_status()

            # 检查dfyc域的JSESSIONID是否已在session的cookie jar中
            dfyc_cookies = [cookie for cookie in self.session.cookies if cookie.name == 'JSESSIONID' and 'dfyc.utc.scut.edu.cn' in cookie.domain]
            
            if dfyc_cookies:
                self.jsessionid = dfyc_cookies[0].value
                js_preview = (self.jsessionid[:8] + "...") if isinstance(self.jsessionid, str) and len(self.jsessionid) >= 8 else str(self.jsessionid)
                logging.info(f"成功通过重定向获取到 JSESSIONID: {js_preview}")
                return True
            else:
                # 如果第一次请求没有直接设置cookie（可能在Location头里），则跟进跳转
                if redirect_response.headers and 'Location' in redirect_response.headers:
                    location_url = redirect_response.headers['Location']
                    logging.info(f"正在跟随跳转到: {location_url[:70]}...")
                    self.session.get(location_url, allow_redirects=True) # 允许requests处理后续跳转
                    # 再次检查cookie
                    dfyc_cookies = [cookie for cookie in self.session.cookies if cookie.name == 'JSESSIONID' and 'dfyc.utc.scut.edu.cn' in cookie.domain]
                    if dfyc_cookies:
                        self.jsessionid = dfyc_cookies[0].value
                        js_preview = (self.jsessionid[:8] + "...") if isinstance(self.jsessionid, str) and len(self.jsessionid) >= 8 else str(self.jsessionid)
                        logging.info(f"成功在二次跳转后获取到 JSESSIONID: {js_preview}")
                        return True
                logging.warning("重定向请求完成，但未能找到 dfyc 域的 JSESSIONID cookie。")
                self.last_error = "未获取到用电查询所需的 JSESSIONID"
                return False

        except requests.exceptions.RequestException as e:
            self.last_error = f"认证重定向失败：{e}"
            logging.error(f"认证重定向过程中发生网络错误: {e}")
            if redirect_response:
                logging.debug(f"失败时的Cookies: {redirect_response.cookies.get_dict()}")
                logging.debug(f"失败时的响应头: {redirect_response.headers}")
                logging.debug(f"失败时的响应体: {redirect_response.text[:300]}")
            return False

    def login(self):
        """
        执行完整的登录流程，包含健壮的API调用顺序和多验证码候选的智能重试机制。
        成功登录后，会将token和完整的登录响应数据保存在实例属性中。
        """
        # 外层重试：若一次识别获得的所有验证码候选均失败，则整体重试
        total_rounds = max(1, getattr(self, 'llm_recognition_retries', 1))
        for round_idx in range(total_rounds):
            logging.info(f"=== 第 {round_idx + 1}/{total_rounds} 轮验证码识别与登录尝试开始 ===")

            # 步骤 1: 获取验证码和Key
            captcha_key, captcha_image_base64 = self._get_captcha_data()
            if not captcha_key:
                logging.warning("获取验证码失败，进入下一轮尝试。")
                continue

            # 步骤 2: 获取配对的键盘信息
            if not self._fetch_keyboard_info(captcha_key):
                logging.warning("获取键盘加密清单失败，进入下一轮尝试。")
                continue

            # 步骤 3: 识别验证码
            captcha_candidates = self._recognize_captcha(captcha_image_base64)
            if not captcha_candidates:
                self.last_error = "未能识别出有效验证码"
                logging.error("未能获取任何验证码候选，本轮尝试结束。")
                continue
            logging.info(f"获取到验证码候选列表: {captcha_candidates}")

            # 步骤 4: 加密密码
            try:
                encrypted_password = self._custom_encrypt(self.password)
            except ValueError as e:
                self.last_error = str(e)
                logging.error(e)
                return False

            # 步骤 5: 遍历候选验证码，尝试登录
            for i, captcha_code in enumerate(captcha_candidates):
                logging.info(f"--- 正在使用第 {i+1}/{len(captcha_candidates)} 个候选验证码 '{captcha_code}' 尝试登录 ---")
                payload = {
                    'grant_type': 'password', 'scope': 'all', 'username': self.username,
                    'password': encrypted_password, 'logintype': 'card',
                    'captcha_header_code': captcha_code, 'captcha_header_key': captcha_key,
                    'loginFrom': 'h5', 'device_token': 'h5', 'synAccessSource': 'h5'
                }
                try:
                    response = self.session.post(self.LOGIN_URL, data=payload)
                except requests.exceptions.RequestException as e:
                    logging.error(f"网络请求发生严重错误: {e}")
                    continue

                response_data = None
                parse_error = None
                if response.content:
                    try:
                        response_data = response.json()
                    except json.JSONDecodeError as e:
                        parse_error = e

                if response.status_code == 200:
                    if not isinstance(response_data, dict) or 'access_token' not in response_data:
                        self.last_error = "登录响应不是有效的 JSON"
                        snippet = response.text[:200]
                        logging.error(f"登录响应不是有效的JSON格式: {snippet}...，错误原因：{parse_error or '缺少 access_token 字段'}")
                        continue

                    self.login_data = response_data
                    self.token = self.login_data['access_token']
                    self.jsessionid = self.session.cookies.get('JSESSIONID')
                    token_preview = self.token[:8] + "..." if isinstance(self.token, str) and len(self.token) >= 8 else str(self.token)
                    logging.info(f"登录成功！Token: {token_preview}")
                    # 登录成功后，执行认证重定向以获取用电查询所需的JSESSIONID
                    if self._perform_auth_redirect():
                        return True
                    self.last_error = "登录成功，但获取 JSESSIONID 失败"
                    logging.error("获取JSESSIONID的认证重定向失败。")
                    return False

                # 处理非 200 状态码
                context = f"验证码候选 '{captcha_code}' (第 {i+1}/{len(captcha_candidates)})"
                message = None
                error_code = None
                if isinstance(response_data, dict):
                    message = response_data.get('message')
                    error_code = response_data.get('code')

                if response.status_code == 400:
                    if error_code == 8002:
                        desc = message or "验证码有误"
                        self.last_error = f"验证码错误：{desc}"
                        logging.warning(f"{context} 验证失败：{desc}。继续尝试下一个候选。")
                        continue
                    if error_code == 8000:
                        desc = message or "用户名或密码错误"
                        self.last_error = f"账号或密码错误：{desc}"
                        logging.error(f"{context} 登录失败：{desc}。已确认账号/密码不匹配，后续不再重试。")
                        return False
                    friendly = message or "未知的业务错误"
                    self.last_error = f"登录请求失败：{friendly}"
                    logging.error(f"{context} 登录请求返回 400：{friendly} (code={error_code})。")
                    continue

                if response.status_code == 401:
                    friendly = message or "未通过身份验证（可能缺少关键字段或凭证）"
                    self.last_error = f"登录请求被拒绝：{friendly}"
                    logging.error(f"{context} 登录请求被拒绝：{friendly} (HTTP 401)。")
                    continue

                if parse_error:
                    self.last_error = f"登录响应解析失败：{parse_error}"
                    snippet = response.text[:200]
                    logging.error(f"{context} 登录响应无法解析为JSON (HTTP {response.status_code})：{snippet}...，错误原因：{parse_error}")
                else:
                    self.last_error = f"登录请求HTTP错误：状态码 {response.status_code}"
                    snippet = response.text[:200]
                    logging.error(f"{context} 登录请求发生未知HTTP错误，状态码: {response.status_code}, 响应: {snippet}...")
                continue

            logging.warning("本轮所有验证码候选均已尝试失败，将进入下一轮重试（若仍有剩余次数）。")

        if self.last_error == "登录失败":
            self.last_error = "所有验证码重试次数已用尽"
        logging.error("所有验证码重试次数已用尽，登录未能成功。")
        return False

def main():
    """主执行函数，负责加载配置并运行监控器。"""
    load_dotenv()
    
    # --- 加载和检查配置 ---
    config = {
        "username": os.environ.get("SCUT_USERNAME"),
        "password": os.environ.get("SCUT_PASSWORD"),
        "llm_model": os.environ.get("LLM_MODEL"),
        "llm_api_key": os.environ.get("LLM_API_KEY"),
        "llm_api_base": os.environ.get("LLM_API_BASE"),
        # 非网络层识别重试次数，默认为1
        "llm_recognition_retries": int(os.environ.get("LLM_RECOGNITION_RETRIES", "1"))
    }
    if not all(config.get(k) for k in ["username", "password", "llm_model", "llm_api_key"]):
        logging.error("配置不完整，请检查 .env 文件。需要设置 SCUT_USERNAME, SCUT_PASSWORD, LLM_MODEL, LLM_API_KEY")
        return

    # --- 运行监视器 ---
    monitor = ScutChargeMonitor(**config)
    if monitor.login():
        logging.info("登录模块测试成功！")
    else:
        logging.error("登录模块测试失败。")

if __name__ == '__main__':
    main()

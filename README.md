# 华南理工大学电费监控与告警脚本

这是一个自动化脚本，旨在帮助华南理工大学的学生监控宿舍的电费情况。


## 项目结构

项目代码主要位于 `src/` 目录下，结构清晰，各模块职责分明：

```
scut-charge-monitor/
│
├── main.py             # 🎯 主程序入口，编排整个监控流程
├── .env.example        # ⚙️ 环境变量配置示例
├── pyproject.toml      # 📦 项目依赖与元数据
└── src/
    ├── login.py        # 🔑 负责处理复杂的登录逻辑，包括验证码识别
    ├── query.py        # 📊 登录成功后，用于查询剩余电量与用电历史
    ├── analysis.py     # 🧠 分析用电数据，预测耗尽日期并检查低电量
    └── notify.py       # 📧 管理通知发送（目前支持邮件和日志）
```

## 快速上手

请遵循以下步骤来配置和运行此脚本。

### 1. 环境准备

确保您的系统中已安装 [Python 3.11+](https://www.python.org/) 和 [uv](https://github.com/astral-sh/uv) 包管理器。

### 2. 安装依赖

克隆本仓库到本地，然后使用 `uv` 来创建虚拟环境并同步项目依赖。

```bash
# 克隆仓库
git clone <your-repo-url>
cd scut-charge-monitor

# 使用 uv 创建虚拟环境并安装依赖
uv sync
```

### 3. 配置环境变量

在项目根目录下创建一个名为 `.env` 的文件。您可以复制 `.env.example` 的内容（如下所示）并填入您自己的信息。

```dotenv
# --- 卡号和密码 ---
# 注: 此处为一卡通界面的“卡号”，非学号
SCUT_USERNAME="your_username"
# 注: 此处为查询密码
SCUT_PASSWORD="your_password"

# --- LiteLLM 配置 (用于识别验证码) ---
# 模型名称, 例如: "gemini/gemini-2.5-pro" 等
# 详见: https://docs.litellm.ai/docs/providers
LLM_MODEL="gemini/gemini-2.5-pro"
LLM_API_KEY="your_llm_api_key_here"
# 如果使用自定义或代理的LLM服务, 请取消下面的注释并设置API Base URL
# LLM_API_BASE="https://your-custom-llm-service.com/v1"

# --- 告警配置 ---
# 低电量告警阈值(单位: 度)，当剩余电量低于此值时将触发告警
ELECTRICITY_ALERT_THRESHOLD=20

# --- LLM识别重试 ---
# 当一轮识别出的所有验证码候选都失败时，重新获取图片并识别的总次数
# 建议取值: 1-5, 默认1
LLM_RECOGNITION_RETRIES=3

# --- 邮件通知配置 (可选) ---
# 仅当以下所有 EMAIL_* 均已配置时，程序才会通过邮箱发送告警
EMAIL_SMTP_SERVER="smtp.qq.com"
EMAIL_SMTP_PORT=587
EMAIL_SMTP_USER="your_email@qq.com"
# 此处应为邮箱的“客户端授权码”或“应用专用密码”，不是登录密码
EMAIL_SMTP_PASSWORD="your_email_app_password_or_token"
# 收件人邮箱地址（支持多个）
# 方式一：使用 EMAIL_RECIPIENTS，多个地址用逗号/分号/空格/换行分隔
# EMAIL_RECIPIENTS="a@example.com, b@example.com; c@example.com d@example.com"
# 方式二：保留兼容单个的 EMAIL_RECIPIENT
EMAIL_RECIPIENT="recipient_email@example.com"
```

### 4. 运行脚本

完成配置后，在项目根目录下执行以下命令即可运行监控脚本：

```bash
uv run main.py
```

脚本将开始执行登录、查询、分析和（如果需要）发送通知的全过程。您可以在终端看到详细的日志输出。

此外，程序会在项目根目录下自动创建 `logs/execution-summary.log`。这个文件每次只追加一行摘要，例如“2026-03-12 06:00:00 - 成功，剩余电量 23.5 度”或“2026-03-12 06:00:00 - 失败：获取验证码失败”，方便配合 Linux `cron` 查看结果。

---

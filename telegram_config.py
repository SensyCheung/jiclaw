"""
telegram_config.py - Telegram Bot 配置

使用方法：
1. 在 Telegram 中搜索 @BotFather，创建新 Bot，获取 BOT_TOKEN
2. 将 Bot 添加到你的频道/群组，并设置为管理员
3. 获取 CHAT_ID（频道 ID 通常以 -100 开头）
4. 在环境变量中设置 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID
   非半导体文章可用 TELEGRAM_CHAT_ID_OTHER 指定另一个群组
"""

import os

# Telegram Bot Token（从 @BotFather 获取）
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")

# Telegram Chat ID（频道或群组 ID）——半导体行业文章推送目标
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# 非半导体文章（家电、泛科技等）推送目标（频道或群组 ID，可选）
# 未设置时，非半导体文章将被剔除（不推送）
TELEGRAM_CHAT_ID_OTHER = os.environ.get("TELEGRAM_CHAT_ID_OTHER", "")

# 是否启用 Telegram 推送
TELEGRAM_ENABLED = bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)

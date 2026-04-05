"""
discord_config.py - Discord Bot 配置

使用方法：
1. 在 Discord Developer Portal 创建应用和 Bot
2. 获取 Bot Token 和 Application ID
3. 将 Bot 邀请到你的服务器
4. 获取频道 ID（Channel ID）
5. 在环境变量中设置 DISCORD_BOT_TOKEN 和 DISCORD_CHANNEL_ID
"""

import os

# Discord Bot Token（从 Discord Developer Portal 获取）
DISCORD_BOT_TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "")

# Discord Channel ID（频道 ID）
DISCORD_CHANNEL_ID = os.environ.get("DISCORD_CHANNEL_ID", "")

# 是否启用 Discord 推送
DISCORD_ENABLED = bool(DISCORD_BOT_TOKEN and DISCORD_CHANNEL_ID)

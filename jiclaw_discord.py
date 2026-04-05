"""
jiclaw_discord.py - Discord 推送模块
将 AI 摘要后的文章信息发送到 Discord 频道
"""

import os
import re
import requests
from discord_config import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, DISCORD_ENABLED


def strip_html_tags(text: str) -> str:
    """去除 HTML 标签，返回纯文本"""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def format_discord_message(
    title_cn: str,
    title_en: str,
    summary_cn: str,
    link: str,
    tags: list,
    website_name: str,
) -> dict:
    """
    格式化 Discord 消息（Embed 格式）

    Discord Embed 支持丰富的格式，包括标题、描述、字段、颜色等
    """
    # 截断过长的标题
    max_title_len = 100
    if len(title_cn) > max_title_len:
        title_cn = title_cn[:max_title_len - 3] + "..."
    if len(title_en) > max_title_len:
        title_en = title_en[:max_title_len - 3] + "..."

    # 处理摘要（去除 HTML 标签，限制长度）
    summary_plain = strip_html_tags(summary_cn)
    max_summary_len = 1000  # Discord Embed 描述限制 4096 字符
    if len(summary_plain) > max_summary_len:
        summary_plain = summary_plain[:max_summary_len - 3] + "..."

    # 格式化摘要行
    summary_lines = re.split(r'[➀➁➂➃➄➅➆➇➈➉]', summary_plain)
    formatted_summary = ""
    for i, line in enumerate(summary_lines[:8], 1):
        line = line.strip().rstrip(';').rstrip('；').rstrip('.')
        if line:
            formatted_summary += f"• {line}\n"

    # 格式化标签
    tag_str = ""
    if tags:
        tag_str = " ".join(f"#{tag.replace(' ', '')}" for tag in tags[:5])

    # 构建 Embed
    embed = {
        "title": f"【{website_name}】{title_cn}",
        "description": formatted_summary.strip() or "无摘要",
        "color": 0x5865F2,  # Discord 品牌色（蓝紫色）
        "fields": [
            {
                "name": "🔤 原标题",
                "value": title_en,
                "inline": False
            },
            {
                "name": "🏷️ 标签",
                "value": tag_str if tag_str else "无标签",
                "inline": True
            },
            {
                "name": "\u200b",  # 空白字段用于对齐
                "value": "\u200b",
                "inline": True
            }
        ],
        "url": link,
        "footer": {
            "text": f"来自 {website_name}",
            "icon_url": "https://cdn-icons-png.flaticon.com/512/10303/10303661.png"
        },
        "timestamp": requests.get("https://api.ipify.org?format=json").headers.get("Date") or ""
    }

    # 如果标签为空，移除标签字段
    if not tag_str:
        embed["fields"] = embed["fields"][:1]

    return embed


def send_to_discord(
    title_cn: str,
    title_en: str,
    summary_cn: str,
    link: str,
    tags: list,
    website_name: str,
    bot_token: str = None,
    channel_id: str = None,
) -> bool:
    """
    发送消息到 Discord

    Args:
        title_cn: 中文标题
        title_en: 英文标题
        summary_cn: 中文摘要
        link: 原文链接
        tags: 标签列表
        website_name: 网站名称
        bot_token: Bot Token（可选，覆盖配置）
        channel_id: Channel ID（可选，覆盖配置）

    Returns:
        bool: 发送成功返回 True，失败返回 False
    """
    token = bot_token or DISCORD_BOT_TOKEN
    cid = channel_id or DISCORD_CHANNEL_ID

    if not token or not cid:
        print("Discord 未配置（缺少 BOT_TOKEN 或 CHANNEL_ID），跳过发送。")
        return False

    # 格式化 Embed 消息
    embed = format_discord_message(
        title_cn=title_cn,
        title_en=title_en,
        summary_cn=summary_cn,
        link=link,
        tags=tags,
        website_name=website_name,
    )

    # Discord Bot API
    url = f"https://discord.com/api/v10/channels/{cid}/messages"

    headers = {
        "Authorization": f"Bot {token}",
        "Content-Type": "application/json",
    }

    payload = {
        "embeds": [embed],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        result = resp.json()

        if resp.ok:
            message_id = result.get("id")
            print(f"已发送到 Discord：Message ID {message_id}")
            return True
        else:
            # 解析错误信息
            error_code = resp.status_code
            error_msg = result.get("message", "Unknown error")
            print(f"Discord 发送失败：{error_code} - {error_msg}")

            if error_code == 401:
                print("💡 401 Unauthorized：Bot Token 无效")
            elif error_code == 403:
                print("💡 403 Forbidden：Bot 没有权限发送到该频道")
            elif error_code == 404:
                print("💡 404 Not Found：频道 ID 不存在")
            elif error_code == 429:
                retry_after = result.get("retry_after", 1)
                print(f"💡 429 Rate Limited：请等待 {retry_after} 秒后重试")

            return False

    except requests.exceptions.RequestException as e:
        print(f"Discord 发送异常：{e}")
        return False


def test_discord_bot() -> bool:
    """测试 Discord Bot 配置是否正确"""
    if not DISCORD_ENABLED:
        print("Discord 未启用，请检查环境变量 DISCORD_BOT_TOKEN 和 DISCORD_CHANNEL_ID")
        return False

    embed = {
        "title": "🧪 jiclaw Discord 测试",
        "description": "如果您看到这条消息，说明 Bot 配置正确！",
        "color": 0x00FF00,
        "fields": [
            {
                "name": "✅ Bot Token",
                "value": "有效",
                "inline": True
            },
            {
                "name": "✅ Channel ID",
                "value": "有效",
                "inline": True
            },
            {
                "name": "✅ 消息发送",
                "value": "成功",
                "inline": True
            }
        ],
        "footer": {
            "text": "现在可以开始接收 RSS 推送了"
        }
    }

    url = f"https://discord.com/api/v10/channels/{DISCORD_CHANNEL_ID}/messages"
    headers = {
        "Authorization": f"Bot {DISCORD_BOT_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "embeds": [embed],
    }

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=15)
        result = resp.json()

        print(f"HTTP 状态码：{resp.status_code}")
        print(f"API 响应：{result}")

        if resp.ok:
            print("✅ Discord Bot 测试成功！")
            return True
        else:
            error_code = resp.status_code
            error_msg = result.get("message", "Unknown error")
            print(f"\n❌ 错误代码：{error_code}")
            print(f"❌ 错误描述：{error_msg}")

            if error_code == 401:
                print("\n💡 401 Unauthorized：Bot Token 无效")
            elif error_code == 403:
                print("\n💡 403 Forbidden：Bot 没有权限发送到该频道")
            elif error_code == 404:
                print("\n💡 404 Not Found：频道 ID 不存在")

            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Discord Bot 测试异常：{e}")
        return False


if __name__ == "__main__":
    print("测试 Discord 推送功能...\n")
    test_discord_bot()

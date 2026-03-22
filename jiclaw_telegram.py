"""
jiclaw_telegram.py - Telegram 推送模块
将 AI 摘要后的文章信息发送到 Telegram 频道/群组
"""

import os
import re
import requests
from telegram_config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_ENABLED


def strip_html_tags(text: str) -> str:
    """去除 HTML 标签，返回纯文本"""
    if not text:
        return ""
    # 去除 <p> </p> 等 HTML 标签
    clean = re.sub(r'<[^>]+>', '', text)
    # 清理多余空白
    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean


def format_telegram_message(
    title_cn: str,
    title_en: str,
    summary_cn: str,
    link: str,
    tags: list,
    website_name: str,
) -> str:
    """
    格式化 Telegram 消息
    
    使用 HTML 格式：
    【网站名称】
    
    📌 标题（中文）
    🔤 原标题（英文）
    
    🔑 要点摘要
    
    🏷️ #标签
    
    🔗 阅读全文：链接
    """
    # 截断过长的标题
    max_title_len = 80
    if len(title_cn) > max_title_len:
        title_cn = title_cn[:max_title_len - 3] + "..."
    if len(title_en) > max_title_len:
        title_en = title_en[:max_title_len - 3] + "..."
    
    # 处理摘要（去除 HTML 标签，限制长度）
    summary_plain = strip_html_tags(summary_cn)
    max_summary_len = 800  # Telegram 消息限制 4096 字符，预留空间
    if len(summary_plain) > max_summary_len:
        summary_plain = summary_plain[:max_summary_len - 3] + "..."
    
    # 格式化摘要行（将 ➀ ➁ ➂ 转换为独立行）
    summary_lines = re.split(r'[➀➁➂➃➄➅➆➇➈➉]', summary_plain)
    formatted_summary = ""
    for i, line in enumerate(summary_lines[:8], 1):  # 最多 8 条
        line = line.strip().rstrip(';').rstrip('；').rstrip('.')
        if line:
            formatted_summary += f"• {line}\n"
    
    # 格式化标签
    tag_str = ""
    if tags:
        tag_str = "🏷️ " + " ".join(f"#{tag.replace(' ', '')}" for tag in tags[:5]) + "\n\n"
    
    # 构建消息
    message = f"""【{website_name}】

📌 {title_cn}
🔤 {title_en}

🔑 要点摘要
{formatted_summary}
{tag_str}🔗 阅读全文：{link}"""
    
    return message


def send_to_telegram(
    title_cn: str,
    title_en: str,
    summary_cn: str,
    link: str,
    tags: list,
    website_name: str,
    bot_token: str = None,
    chat_id: str = None,
) -> bool:
    """
    发送消息到 Telegram
    
    Args:
        title_cn: 中文标题
        title_en: 英文标题
        summary_cn: 中文摘要（HTML 格式）
        link: 原文链接
        tags: 标签列表
        website_name: 网站名称
        bot_token: Bot Token（可选，覆盖配置）
        chat_id: Chat ID（可选，覆盖配置）
    
    Returns:
        bool: 发送成功返回 True，失败返回 False
    """
    # 使用传入的参数或配置文件
    token = bot_token or TELEGRAM_BOT_TOKEN
    cid = chat_id or TELEGRAM_CHAT_ID
    
    if not token or not cid:
        print("Telegram 未配置（缺少 BOT_TOKEN 或 CHAT_ID），跳过发送。")
        return False
    
    # 格式化消息
    message = format_telegram_message(
        title_cn=title_cn,
        title_en=title_en,
        summary_cn=summary_cn,
        link=link,
        tags=tags,
        website_name=website_name,
    )
    
    # Telegram Bot API
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    
    payload = {
        "chat_id": cid,
        "text": message,
        "parse_mode": "HTML",  # 使用 HTML 格式
        "disable_web_page_preview": False,  # 显示链接预览
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=15)
        resp.raise_for_status()
        result = resp.json()
        
        if result.get("ok"):
            message_id = result.get("result", {}).get("message_id")
            print(f"已发送到 Telegram：Message ID {message_id}")
            return True
        else:
            print(f"Telegram 发送失败：{result}")
            return False
            
    except requests.exceptions.RequestException as e:
        print(f"Telegram 发送异常：{e}")
        return False


def test_telegram_bot() -> bool:
    """测试 Telegram Bot 配置是否正确"""
    if not TELEGRAM_ENABLED:
        print("Telegram 未启用，请检查环境变量 TELEGRAM_BOT_TOKEN 和 TELEGRAM_CHAT_ID")
        return False
    
    message = """🧪 <b>jiclaw Telegram 测试</b>

如果您看到这条消息，说明 Bot 配置正确！

✅ Bot Token: 有效
✅ Chat ID: 有效
✅ 消息发送：成功

现在可以开始接收 RSS 推送了。"""
    
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    
    try:
        resp = requests.post(url, json=payload, timeout=15)
        result = resp.json()

        # 打印详细响应以便调试
        print(f"HTTP 状态码：{resp.status_code}")
        print(f"API 响应：{result}")

        if not resp.ok:
            # 解析错误原因
            error_code = resp.status_code
            description = result.get("description", "Unknown error")
            print(f"\n❌ 错误代码：{error_code}")
            print(f"❌ 错误描述：{description}")

            # 常见错误提示
            if error_code == 400:
                print("\n💡 400 Bad Request 常见原因：")
                print("   1. Chat ID 格式错误（频道 ID 必须以 -100 开头）")
                print("   2. Bot 不是频道/群组的管理员")
                print("   3. Chat ID 不存在或 Bot 无法访问该频道")
                print("\n💡 获取正确的 Chat ID：")
                print("   访问：https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates")
                print("   查看返回 JSON 中的 chat.id 字段")
            elif error_code == 401:
                print("\n💡 401 Unauthorized：Bot Token 无效，请检查 TELEGRAM_BOT_TOKEN")
            elif error_code == 403:
                print("\n💡 403 Forbidden：Bot 没有权限发送到该频道")

        if result.get("ok"):
            print("✅ Telegram Bot 测试成功！")
            return True
        else:
            print(f"❌ Telegram Bot 测试失败")
            return False

    except requests.exceptions.RequestException as e:
        print(f"❌ Telegram Bot 测试异常：{e}")
        return False


if __name__ == "__main__":
    # 测试模式
    print("测试 Telegram 推送功能...\n")
    test_telegram_bot()

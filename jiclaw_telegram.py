"""
jiclaw_telegram.py - Telegram 推送模块
将 AI 摘要后的文章信息发送到 Telegram 频道/群组
"""

import os
import re
import requests
from telegram_config import (
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    TELEGRAM_CHAT_ID_OTHER,
    TELEGRAM_ENABLED,
)


def normalize_chat_id(chat_id):
    """补全被省略的 -100 前缀。

    频道/超级群的 ID 形如 ``-1003824126934``（-100 + 10 位数字）。
    手工填 secret 时很容易只填了后面那 10 位，或者漏了负号
    （``3824126934`` / ``-3824126934``），Telegram 会当成用户 ID 去找，
    于是报 ``Bad Request: chat not found``。这里统一补成 14 位。

    已是 -100 开头、或不是纯数字的值原样返回，不做猜测。
    """
    if chat_id is None:
        return chat_id

    s = str(chat_id).strip().strip('"').strip("'")
    if not s or s.startswith("-100"):
        return s

    digits = s[1:] if s.startswith("-") else s
    # 超级群/频道的裸 ID 固定 10 位；带不带负号都补成 -100xxxxxxxxxx
    if digits.isdigit() and len(digits) == 10:
        return "-100" + digits

    return s


def _chat_id_candidates(chat_id):
    """返回尝试顺序：补全后的值优先，原始值兜底（兼容私聊 10 位用户 ID）。"""
    if chat_id is None:
        return []
    fixed = normalize_chat_id(chat_id)
    raw = str(chat_id).strip().strip('"').strip("'")
    return [c for c in dict.fromkeys([fixed, raw]) if c]


# ---------------------------------------------------------------------------
# 原文语言判定
#
# 主群组只保留「外文原文、由模型译成中文」的文章；原文就是中文的
# （国内媒体、中文站点稿件）统一归到 others 群。
# 判定只能用抓到的原始标题 / 正文 / RSS 摘要，**绝不能**用 title_cn /
# summary_cn —— 那两个字段是模型生成的，恒为中文。
# ---------------------------------------------------------------------------
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _han_ratio(text: str):
    """返回 (汉字在有意义字符中的占比, 汉字数)。数字、标点不计入分母。"""
    if not text:
        return 0.0, 0
    # 先剥掉 HTML 标签再截断：RSS 摘要常带 <p>/<a href=...> 之类的英文标记，
    # 不剥会把中文占比稀释，也会白占 600 字的采样预算。
    sample = re.sub(r"<[^>]+>", " ", text)[:600]
    han = len(_HAN_RE.findall(sample))
    latin = len(_LATIN_RE.findall(sample))
    total = han + latin
    return (han / total if total else 0.0), han


def is_chinese_original_text(title: str, content: str = "", summary: str = "") -> bool:
    """判断原文是否为中文内容。

    标题是最可靠的信号：中文站标题以汉字为主（允许夹品牌名/型号），
    英文站标题偶尔出现一两个中文专名，靠「汉字数 >= 4 且占比 >= 0.3」挡掉。
    少数转载稿标题是英文、正文却是中文（或标题为空、只有中文 RSS 摘要），
    再用正文前 600 字兜底：既要有一定汉字量，也要汉字占绝对方，
    这样英文文章里几处中文引文不会被误判。
    """
    ratio, han = _han_ratio(title or "")
    if han >= 4 and ratio >= 0.3:
        return True

    body_ratio, body_han = _han_ratio(content or summary or "")
    return body_han >= 12 and body_ratio >= 0.5


def resolve_telegram_chat_id(is_semiconductor: bool, is_chinese_original: bool = False):
    """根据原文语言与行业分类，返回 (chat_id, 说明)。

    优先级：**原文语言 > 行业分类**。

    - 原文为中文：一律推送到 TELEGRAM_CHAT_ID_OTHER（others 群），
      即使模型判定它是半导体文章；主群组只保留外文原文文章。
    - 原文非中文 + 半导体：推送到 TELEGRAM_CHAT_ID（主群组）
    - 原文非中文 + 非半导体：推送到 TELEGRAM_CHAT_ID_OTHER
      （未配置该群组时返回 chat_id=None，表示剔除、不推送）
    """
    if is_chinese_original:
        if TELEGRAM_CHAT_ID_OTHER:
            return normalize_chat_id(TELEGRAM_CHAT_ID_OTHER), "中文原文 → others 群组"
        return None, "中文原文（未配置 TELEGRAM_CHAT_ID_OTHER，已剔除）"

    if is_semiconductor:
        return normalize_chat_id(TELEGRAM_CHAT_ID), "半导体 → 主群组"
    if TELEGRAM_CHAT_ID_OTHER:
        return normalize_chat_id(TELEGRAM_CHAT_ID_OTHER), "非半导体 → 其他群组"
    return None, "非半导体（未配置 TELEGRAM_CHAT_ID_OTHER，已剔除）"


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

    # 依次尝试补全后的 ID 与原始 ID；一旦某个写法成功就记住，后续文章不再重试
    tried = []
    for candidate in _chat_id_candidates(cid):
        tried.append(candidate)
        payload = {
            "chat_id": candidate,
            "text": message,
            "parse_mode": "HTML",  # 使用 HTML 格式
            "disable_web_page_preview": False,  # 显示链接预览
        }

        try:
            resp = requests.post(url, json=payload, timeout=15)
        except requests.exceptions.RequestException as e:
            print(f"Telegram 发送异常：chat_id={candidate} | {e}")
            return False

        if resp.ok:
            result = resp.json()
            if result.get("ok"):
                message_id = result.get("result", {}).get("message_id")
                if candidate != str(cid).strip():
                    print(
                        f"chat_id 已自动补全：{cid} → {candidate}"
                        "（请修正 Secret，免得每次都走兜底）"
                    )
                print(f"已发送到 Telegram：Message ID {message_id}")
                return True

            print(f"Telegram 发送失败：chat_id={candidate} | {result}")
            return False

        # 400 常见原因：chat_id 写错/缺 -100 前缀、Bot 未加入该群或被踢出
        try:
            detail = resp.json().get("description", "")
        except ValueError:
            detail = resp.text[:200]

        # 只有「找不到该会话」才值得换一种写法再试；
        # 403（没权限）、429（限流）换写法也没用，直接报错退出
        if "chat not found" in detail.lower() and len(tried) < len(_chat_id_candidates(cid)):
            print(
                f"chat_id={candidate} 找不到会话（{detail}），"
                f"换 {_chat_id_candidates(cid)[len(tried)]} 重试..."
            )
            continue

        print(f"Telegram 发送失败：HTTP {resp.status_code} | chat_id={candidate} | {detail}")
        return False

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
        "chat_id": normalize_chat_id(TELEGRAM_CHAT_ID),
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

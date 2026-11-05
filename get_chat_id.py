"""
get_chat_id.py - 查询 Telegram Bot 能看到的群组/频道 Chat ID

用法：
    # 1) 列出 Bot 最近收到的所有会话（跟着打印出的 ID 填进 GitHub Secrets）
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    python get_chat_id.py

    # 2) 验证某个 chat_id 是否可用（会真的发一条测试消息）
    python get_chat_id.py --check -1001234567890

获取步骤：
    1. 先把 Bot 拉进「其他群」（必须，否则 Telegram 不会告诉 Bot 有这个群）
    2. 在群里随便发一条消息（内容任意，@一下 Bot 更保险）
    3. 运行本脚本，找到对应群名的 chat.id（一般是 -100 开头）
    4. 填到 GitHub 仓库 Settings → Secrets and variables → Actions
       → 新建 Secret：TELEGRAM_CHAT_ID_OTHER
"""

import argparse
import os
import sys

import requests

API = "https://api.telegram.org/bot{token}/{method}"


def _call(token: str, method: str, **params):
    url = API.format(token=token, method=method)
    resp = requests.post(url, json=params, timeout=20)
    try:
        data = resp.json()
    except ValueError:
        raise RuntimeError(f"HTTP {resp.status_code}，返回非 JSON：{resp.text[:200]}")
    if not data.get("ok"):
        raise RuntimeError(
            f"HTTP {resp.status_code} | {data.get('error_code')} {data.get('description')}"
        )
    return data.get("result")


def cmd_list(token: str) -> int:
    me = _call(token, "getMe")
    print(f"Bot：@{me.get('username')}（{me.get('first_name')}）")
    print(f"  可加入群组：{me.get('can_join_groups')} | 能读取群内全部消息：{me.get('can_read_all_group_messages')}\n")

    try:
        updates = _call(token, "getUpdates", limit=100, timeout=0)
    except RuntimeError as e:
        msg = str(e)
        if "409" in msg or "webhook" in msg.lower():
            print("❌ 该 Bot 已设置 Webhook，getUpdates 无法使用。")
            print("   如果这个 Webhook 不是你在用的，可以先执行：")
            print(f"   curl -X POST https://api.telegram.org/bot<TOKEN>/deleteWebhook")
            print("   然后重新运行本脚本。")
            return 1
        raise

    chats = {}
    for up in updates:
        for key in ("message", "edited_message", "channel_post", "my_chat_member", "chat_member"):
            obj = up.get(key)
            if not obj:
                continue
            chat = obj.get("chat") or {}
            if chat.get("id") is not None:
                chats[chat["id"]] = chat
            # 频道里的消息，from 也可能是目标频道
            if key in ("channel_post",) and obj.get("sender_chat"):
                c = obj["sender_chat"]
                chats[c["id"]] = c

    if not chats:
        print("没有查到任何会话。请确认：")
        print("  1. Bot 已被拉进目标群（不是只加了链接）")
        print("  2. 在群里发过至少一条消息（Bot 只能看到自己收到过的会话）")
        print("  3. 消息发送时间在 Bot 入群之后，且在最近 24 小时内")
        if not me.get("can_read_all_group_messages"):
            print("\n⚠️  该 Bot 的 Privacy Mode 是开启的，群里普通消息它收不到。")
            print("   解决办法：进群后 @ 一下它，例如发「@bot 你好」，然后再查一次。")
            print("   或者到 @BotFather → /setprivacy → 选该 Bot → Disable，再把它移出重拉进群。")
        return 1

    print(f"共找到 {len(chats)} 个会话：\n")
    print(f"{'chat_id':>20}  {'类型':<12} 标题")
    print("-" * 78)
    for cid, chat in chats.items():
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "-"
        print(f"{str(cid):>20}  {chat.get('type', '-'):<12} {title}")

    print("\n将 -100 开头（超级群/频道）的那一行填进 TELEGRAM_CHAT_ID_OTHER。")
    print("注意：ID 带减号，别漏；填的时候不要加引号以外的任何字符。")
    return 0


def cmd_check(token: str, chat_id: str) -> int:
    cid = chat_id.strip()
    if cid and not cid.startswith("-"):
        # 纯数字说明大概率漏了前缀
        print(f"⚠️  {cid} 不以 - 开头，群组/频道 ID 一定是负数，请检查是否漏了前缀\n")

    try:
        chat = _call(token, "getChat", chat_id=cid)
    except RuntimeError as e:
        print(f"❌ getChat 失败：{e}")
        print("   chat not found → ID 写错、或 Bot 不在该群、或群已被删除")
        return 1

    title = chat.get("title") or chat.get("username") or chat.get("first_name") or "-"
    print(f"✅ 找到会话：{title}（type={chat.get('type')}）")

    try:
        res = _call(token, "sendMessage", chat_id=cid, text="🧪 jiclaw chat_id 测试消息")
        print(f"✅ 测试消息已发送，message_id={res.get('message_id')}，去群里确认一下")
    except RuntimeError as e:
        print(f"❌ 发送失败：{e}")
        print("   400 chat not found → ID 错；403 → Bot 不是管理员或没有发言权限")
        return 1
    return 0


def cmd_probe_config(token: str) -> int:
    """拿现有的 TELEGRAM_CHAT_ID_OTHER 去试探，找出正确写法（不打印原始 ID）。"""
    raw = (os.environ.get("TELEGRAM_CHAT_ID_OTHER") or "").strip()
    if not raw:
        print("TELEGRAM_CHAT_ID_OTHER 未配置，无需试探。")
        return 0

    print(f"当前值长度 {len(raw)}，以 {'-' if raw.startswith('-') else '数字'} 开头\n")

    digits = raw.lstrip("-")
    candidates = []
    for c in (raw, "-100" + digits, "-" + digits, digits):
        if c and c not in candidates:
            candidates.append(c)

    ok_any = False
    for c in candidates:
        label = "当前值原样" if c == raw else f"加前缀 → {c[:4]}***{c[-2:]}(长度{len(c)})"
        try:
            chat = _call(token, "getChat", chat_id=c)
        except RuntimeError as e:
            print(f"  [FAIL] {label}: {str(e)[:110]}")
            continue
        title = chat.get("title") or chat.get("username") or chat.get("first_name") or "-"
        print(f"  [OK]   {label}: {title}（type={chat.get('type')}）")
        if c != raw:
            print("\n👉 正确写法是在你填的那个数字前面加 -100：")
            print("   原值：3824...（你填的 10 位数）")
            print("   改为：-100 加上同一串数字，共 14 位")
            ok_any = True
        else:
            print("\n👉 当前值本身是对的就说明问题在别处（Bot 权限）")
            ok_any = True

    if not ok_any:
        print("\n❌ 所有写法都失败，说明该 ID 确实不存在或 Bot 不在群里。")
        print("   请把 Bot 拉进「其他群」并设为管理员，然后在群里发一条：")
        print("   @SemiVoiceCLaw002_Bot test")
        print("   再运行不带参数的查询（列出会话）即可拿到真实 ID。")
        return 1
    return 0


def cmd_config() -> int:
    """以掩码形式显示当前环境里的 chat id 配置，便于核对格式。"""
    names = ["TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_ID_OTHER"]
    for name in names:
        v = (os.environ.get(name) or "").strip()
        if not v:
            print(f"{name:<22} = (未配置)")
            continue
        masked = f"{v[:4]}...{v[-4:]}" if len(v) > 8 else v
        hint = "" if v.startswith("-100") else "  ⚠️ 不是 -100 开头，超级群/频道 ID 必须如此"
        print(f"{name:<22} = {masked}  (长度 {len(v)}){hint}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查询 Telegram Chat ID")
    parser.add_argument("-t", "--token", help="Bot Token（默认读 TELEGRAM_BOT_TOKEN）")
    parser.add_argument("--check", metavar="CHAT_ID", help="验证指定 chat_id 并发送测试消息")
    parser.add_argument("--config", action="store_true", help="以掩码形式显示当前配置的 chat id")
    parser.add_argument("--probe-config", action="store_true", help="试探 CURRENT TELEGRAM_CHAT_ID_OTHER 的正确前缀")
    args = parser.parse_args()

    if args.config:
        return cmd_config()

    token = (args.token or os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        print("❌ 未提供 Bot Token。")
        print("   方式一：export TELEGRAM_BOT_TOKEN=\"123456:ABC...\"")
        print("   方式二：python get_chat_id.py -t \"123456:ABC...\"")
        return 2

    try:
        if args.check:
            return cmd_check(token, args.check)
        if args.probe_config:
            return cmd_probe_config(token)
        return cmd_list(token)
    except RuntimeError as e:
        print(f"❌ 请求失败：{e}")
        if "401" in str(e):
            print("   Token 无效，请到 @BotFather 用 /mybots → API Token 重新获取")
        if "timeout" in str(e).lower() or "timed out" in str(e).lower():
            print("   网络超时：国内直连 api.telegram.org 常常不通，需要开代理")
        return 1
    except requests.exceptions.RequestException as e:
        print(f"❌ 网络异常：{e}")
        print("   国内直连 api.telegram.org 通常不可用，请配置代理后再试")
        return 1


if __name__ == "__main__":
    sys.exit(main())

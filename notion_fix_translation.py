#!/usr/bin/env python3
"""notion_fix_translation.py - 修正 Notion 数据库里已写入标题的翻译错误。

背景：LLM 现场翻译时偶发两类错误：
  1) 望文生义：专有名词按字面意译，如 Moonshot AI（月之暗面）被译成「嫦娥」；
  2) 串台：同批次多条一起处理时，title_cn 被写成了相邻文章的标题。
错误标题既在本地 risk_events 又在 Notion 里。本脚本按 POSTID 精确定位页面，
把 Title_cn（必要时含 LLM 字段）改成正确值，只动映射表列出的字段，绝不全量重写。

用法（需要 NOTION_API_KEY / NOTION_DATABASE_ID 环境变量）：
    python notion_fix_translation.py --mode probe     # 只读：打印目标页面当前值
    python notion_fix_translation.py --mode dry-run   # 打印将要修改的字段，不写
    python notion_fix_translation.py --mode apply     # 真正 PATCH 更新

线上通过 workflow_dispatch 的 notion_fix_translation 输入触发，密钥由 Actions Secrets 注入。
"""

import argparse
import json
import os
import sys
import time

import requests

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com"

# 修正映射：POSTID -> {属性名: 正确值}。只列需要改的字段。
# 值来自原文核对（title_cn 应与英文原文逐条对应，中文原文则与 title 一致）。
FIXES = {
    "0c778e9625": {
        "Title_cn": "中国月之暗面(Moonshot AI)取消在印尼获取英伟达芯片的 1.5 亿美元交易",
    },
    "cadd764f85": {
        "Title_cn": "【头条】沁恒微向深交所递交招股书 二度冲击A股IPO",
    },
    "a058876b86": {
        "Title_cn": "【破产】总投资规划50亿元，浙江旺荣半导体进入破产重整",
    },
    "8fd9f9c3e5": {
        "Title_cn": "【头条】投资五亿,合肥半导体项目破产审查！钱大宏:携手芯原打通从“芯”到“医”最后一公里；688498,签逾17亿芯片大单",
    },
    "2bb38d8276": {
        "Title_cn": "【头条】黄仁勋复职联署破13万人，马斯克、张一鸣在列！",
    },
}


def headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def rich_text_value(text):
    """Notion rich_text 属性格式。长文本按 2000 字切块。"""
    chunks = [text[i:i + 1900] for i in range(0, len(text), 1900)] or [""]
    return {"rich_text": [{"text": {"content": c}} for c in chunks]}


def get_text(prop):
    """取 rich_text / title 属性的纯文本。"""
    if not isinstance(prop, dict):
        return ""
    arr = prop.get("rich_text") or prop.get("title") or []
    return "".join(
        c.get("plain_text") or c.get("text", {}).get("content", "")
        for c in arr if isinstance(c, dict)
    )


def find_page(token, db_id, post_id):
    """按 POSTID 精确查询页面，返回 (page_id, props) 或 (None, None)。"""
    payload = {
        "filter": {"property": "POSTID", "rich_text": {"equals": post_id}},
        "page_size": 10,
    }
    resp = requests.post(
        f"{BASE}/v1/databases/{db_id}/query",
        headers=headers(token),
        data=json.dumps(payload),
        timeout=20,
    )
    if resp.status_code >= 300:
        print(f"  [query] HTTP {resp.status_code}: {resp.text[:300]}")
        return None, None
    results = resp.json().get("results", [])
    if not results:
        return None, None
    if len(results) > 1:
        print(f"  [warn] POSTID {post_id} 命中 {len(results)} 个页面，全部处理")
    return results, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("probe", "dry-run", "apply"), default="probe")
    args = ap.parse_args()

    token = os.environ.get("NOTION_API_KEY", "")
    db_id = os.environ.get("NOTION_DATABASE_ID", "")
    if not token or not db_id:
        print("缺少 NOTION_API_KEY / NOTION_DATABASE_ID，退出")
        return 1
    print(f"模式: {args.mode} | 密钥状态: 已注入（值不打印） | 修正条目: {len(FIXES)}")

    plan = []  # (page_id, post_id, prop_name, old, new)
    for post_id, fields in FIXES.items():
        pages, _ = find_page(token, db_id, post_id)
        if not pages:
            print(f"[miss] POSTID {post_id} 未在 Notion 找到，跳过")
            continue
        for page in pages:
            props = page.get("properties") or {}
            for prop_name, new_val in fields.items():
                old = get_text(props.get(prop_name))
                flag = "CHANGE" if old != new_val else "SAME  "
                print(f"[{flag}] {post_id} | {prop_name}")
                print(f"       旧: {old}")
                print(f"       新: {new_val}")
                if old != new_val:
                    plan.append((page["id"], post_id, prop_name, new_val))

    if args.mode == "probe":
        print(f"\nprobe 完成：需修改 {len(plan)} 处（未写入）")
        return 0

    print(f"\n{args.mode}：共 {len(plan)} 处待修改")
    if args.mode == "dry-run":
        return 0

    changed = failed = 0
    for page_id, post_id, prop_name, new_val in plan:
        resp = requests.patch(
            f"{BASE}/v1/pages/{page_id}",
            headers=headers(token),
            data=json.dumps({"properties": {prop_name: rich_text_value(new_val)}}),
            timeout=20,
        )
        if resp.status_code >= 300:
            failed += 1
            print(f"  更新失败 {post_id}/{prop_name}: HTTP {resp.status_code} {resp.text[:200]}")
        else:
            changed += 1
            print(f"  已更新 {post_id}/{prop_name}")
        time.sleep(0.35)  # Notion 限流约 3 req/s
    print(f"\napply 完成：更新 {changed} 处，失败 {failed} 处")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

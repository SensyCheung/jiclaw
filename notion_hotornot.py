#!/usr/bin/env python3
"""notion_hotornot.py - 维护 Notion 数据库里的 HotorNot 字段。

背景：部分 RSS 源会周期性推同名的栏目型文章（如「政治与市场」/ Seeking Alpha），
这类内容不是半导体产业新闻，不该占用首页。历史页面需要批量修正，
新写入的文章由 jiclaw_core.resolve_hotornot 自动判定。

用法（需要 NOTION_API_KEY / NOTION_DATABASE_ID 环境变量）：
    python notion_hotornot.py --mode probe     # 只读：打印选项、标题重复分布、命中样本
    python notion_hotornot.py --mode dry-run   # 列出将要修改的页面，不写
    python notion_hotornot.py --mode apply     # 真正批量更新为 nohome

线上通过 workflow_dispatch 的 notion_hotornot 输入触发，密钥由 Actions Secrets 注入，
本地无需（也不应）保存 token。
"""

import argparse
import collections
import json
import os
import re
import sys
import time

import requests

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com"
PAGE_SIZE = 100

# 标题归一化：去掉首尾空格、统一大小写、压缩空白，用于发现「周期性重复标题」
_WS = re.compile(r"\s+")


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def norm_title(value):
    return _WS.sub(" ", str(value or "").strip()).lower()


def _text(prop):
    """取 title / rich_text 的纯文本。"""
    if not isinstance(prop, dict):
        return ""
    chunks = prop.get("title") or prop.get("rich_text") or []
    return "".join(c.get("plain_text") or c.get("text", {}).get("content", "")
                   for c in chunks if isinstance(c, dict))


def _select(prop):
    if not isinstance(prop, dict):
        return None
    sel = prop.get("select")
    return (sel or {}).get("name") if isinstance(sel, dict) else None


def _tags(prop):
    if not isinstance(prop, dict):
        return []
    return [m.get("name") for m in (prop.get("multi_select") or []) if isinstance(m, dict)]


def fetch_schema(token, db_id):
    resp = requests.get(f"{BASE}/v1/databases/{db_id}", headers=_headers(token), timeout=20)
    if resp.status_code >= 300:
        print(f"[schema] 查询失败 HTTP {resp.status_code}: {resp.text[:500]}")
        return {}
    prop = (resp.json().get("properties") or {}).get("HotorNot") or {}
    return prop.get("select", {}).get("options", []) or []


def iter_pages(token, db_id):
    """分页拉取数据库全部页面。"""
    cursor, total = None, 0
    while True:
        payload = {"page_size": PAGE_SIZE}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{BASE}/v1/databases/{db_id}/query",
            headers=_headers(token),
            data=json.dumps(payload),
            timeout=30,
        )
        if resp.status_code >= 300:
            print(f"[query] 查询失败 HTTP {resp.status_code}: {resp.text[:500]}")
            break
        data = resp.json()
        rows = data.get("results", [])
        total += len(rows)
        for row in rows:
            yield row
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        time.sleep(0.35)


def describe(page):
    props = page.get("properties") or {}
    return {
        "id": page.get("id"),
        "title": _text(props.get("Name")),
        "title_cn": _text(props.get("Title_cn")),
        "website": _text(props.get("Website")),
        "tags": _tags(props.get("tags")),
        "hotornot": _select(props.get("HotorNot")),
        "postid": _text(props.get("POSTID")),
    }


def pick_nohome_option(options):
    """从数据库已有选项里找出「不上首页」那个，避免大小写不一致造出重复选项。"""
    for opt in options:
        name = str(opt.get("name") or "")
        if re.sub(r"[\s_-]", "", name).lower() in ("nohome", "nonhome", "nohot"):
            return name
    return "nohome"


def probe(rows, options):
    print(f"=== HotorNot 选项 ===\n{[o.get('name') for o in options]}")
    print(f"=== 页面总数 ===\n{len(rows)}")

    dist = collections.Counter(r["hotornot"] for r in rows)
    print(f"=== HotorNot 分布 ===\n{dict(dist)}")

    sites = collections.Counter(r["website"] for r in rows)
    print(f"=== Website Top 15 ===\n{sites.most_common(15)}")

    groups = collections.defaultdict(list)
    for r in rows:
        key = norm_title(r["title_cn"] or r["title"])
        if key:
            groups[key].append(r)
    repeated = sorted(((k, v) for k, v in groups.items() if len(v) >= 2),
                      key=lambda kv: -len(kv[1]))
    print(f"=== 重复标题（≥2 次）共 {len(repeated)} 组，Top 40 ===")
    for key, items in repeated[:40]:
        sites_in = sorted({i["website"] for i in items})
        hn = sorted({str(i["hotornot"]) for i in items})
        sample = next((i["title_cn"] or i["title"] for i in items), key)
        print(f"{len(items):>3}x | {key[:60]:<62} | {','.join(sites_in)[:28]:<30} | {','.join(hn):<12} | {sample[:50]}")

    by_site = collections.defaultdict(list)
    for r in rows:
        key = norm_title(r["title_cn"] or r["title"])
        if key:
            by_site[(r["website"], key)].append(r)
    site_repeated = sorted(((k, v) for k, v in by_site.items() if len(v) >= 2),
                           key=lambda kv: -len(kv[1]))
    print(f"=== 同源站重复标题（≥2 次）共 {len(site_repeated)} 组，Top 40 ===")
    for (site, key), items in site_repeated[:40]:
        print(f"{len(items):>3}x | {site[:24]:<26} | {key[:70]}")

    kw = ("政治", "市场", "politics", "markets", "macro", "fed", "tariff", "election")
    hits = [r for r in rows if any(k in norm_title(r["title_cn"] + " " + r["title"]) for k in kw)]
    print(f"=== 标题含政治/市场类词：{len(hits)} 条，样本 30 ===")
    for r in hits[:30]:
        print(f"  [{r['hotornot']}] {r['website'][:18]:<20} | {(r['title_cn'] or r['title'])[:60]} | {(r['title'])[:70]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("probe", "dry-run", "apply"), default="probe")
    args = ap.parse_args()

    token = os.environ.get("NOTION_API_KEY", "")
    db_id = os.environ.get("NOTION_DATABASE_ID", "")
    if not token or not db_id:
        print("缺少 NOTION_API_KEY / NOTION_DATABASE_ID，退出")
        return 1
    print(f"模式: {args.mode} | 密钥状态: 已注入（值不打印）")

    options = fetch_schema(token, db_id)
    rows = [describe(p) for p in iter_pages(token, db_id)]

    if args.mode == "probe":
        probe(rows, options)
        return 0

    # 规则见 jiclaw_core.is_nohome_article：周期性重复标题 + 非半导体内容
    from jiclaw_core import is_nohome_article

    counts = collections.Counter(
        (r["website"], norm_title(r["title_cn"] or r["title"])) for r in rows
    )
    targets = []
    for r in rows:
        key = norm_title(r["title_cn"] or r["title"])
        if is_nohome_article(
            title_en=r["title"], title_cn=r["title_cn"], website_name=r["website"],
            tags=r["tags"], repeat_count=counts.get((r["website"], key), 0),
        ):
            targets.append(r)

    option_name = pick_nohome_option(options)
    print(f"=== 命中 {len(targets)} 条，目标选项: {option_name!r} ===")
    by_title = collections.Counter(norm_title(r["title_cn"] or r["title"]) for r in targets)
    for key, n in by_title.most_common(40):
        print(f"{n:>3}x | {key[:80]}")
    print("=== 样本 ===")
    for r in targets[:25]:
        print(f"  {r['id']} | {r['website'][:18]:<20} | {(r['title_cn'] or r['title'])[:60]}")

    if args.mode == "dry-run":
        print("dry-run：未写入任何页面")
        return 0

    changed = failed = 0
    for r in targets:
        if r["hotornot"] == option_name:
            continue
        resp = requests.patch(
            f"{BASE}/v1/pages/{r['id']}",
            headers=_headers(token),
            data=json.dumps({"properties": {"HotorNot": {"select": {"name": option_name}}}}),
            timeout=20,
        )
        if resp.status_code >= 300:
            failed += 1
            print(f"  更新失败 {r['id']}: HTTP {resp.status_code} {resp.text[:200]}")
        else:
            changed += 1
        time.sleep(0.35)  # Notion 限流约 3 req/s
    print(f"=== apply 完成：更新 {changed} 条，失败 {failed} 条 ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

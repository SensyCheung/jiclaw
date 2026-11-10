"""
probe_feeds.py - RSS 源可达性探测（只读，不写 Notion、不调 LLM、不发消息）

用途：本地网络到部分境外域名不通（代理 502 / 直连超时）时，借 GitHub Actions
runner 验证新 feed 是否真的能解析出文章，再加进订阅清单。

用法：
    python probe_feeds.py URL [URL ...]
    PROBE_FEEDS="url1 url2" python probe_feeds.py
    python probe_feeds.py --extract-authors https://www.bloomberg.com/news/articles/xxx

作者发现：Bloomberg 作者 RSS 需要 /authors/{id}/{slug}.rss，id 无法凭名字推断，
可从文章页原始 HTML 里正则提取链接。批量时用 PROBE_AUTHOR_PAGES（空格分隔）。
"""

import argparse
import os
import re
import socket

import feedparser
import requests

UA = "Mozilla/5.0 (compatible; jiclaw-rss-probe/1.0)"
_AUTHOR_LINK_RE = re.compile(r"/authors/([A-Za-z0-9_-]+)/([a-z0-9-]+)")


def probe(url: str, timeout: int = 45, use_feedparser: bool = False) -> bool:
    """返回该 URL 是否给出了至少一条有效条目。

    use_feedparser=True 时走 feedparser.parse(url)，与 jiclaw_core 的真实抓取路径
    完全一致（urllib + socket 默认超时），用来验证慢源在生产代码里也能等到响应。
    """
    print(f"\n=== {url} ===" + (" [feedparser 真实路径]" if use_feedparser else ""), flush=True)
    if use_feedparser:
        socket.setdefaulttimeout(timeout)
        try:
            feed = feedparser.parse(url)
        except Exception as exc:
            print(f"  feedparser 请求失败: {type(exc).__name__}: {str(exc)[:200]}")
            return False
        print(f"  feed title: {feed.feed.get('title')!r} | items: {len(feed.entries)}")
        if feed.bozo:
            print(f"  解析警告: {str(feed.bozo_exception)[:150]}")
        for entry in feed.entries[:3]:
            date = entry.get("published") or entry.get("updated") or ""
            print(f"    - {(entry.get('title') or '')[:88]} | {date}")
        return len(feed.entries) > 0

    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    except Exception as exc:
        print(f"  请求失败: {type(exc).__name__}: {str(exc)[:200]}")
        return False

    body = resp.content
    ctype = resp.headers.get("content-type", "?")
    print(f"  HTTP {resp.status_code} | {ctype} | {len(body)} bytes")
    if resp.url != url:
        print(f"  最终 URL: {resp.url}")

    if resp.status_code >= 400:
        print(f"  响应片段: {body[:400].decode('utf-8', 'replace')}")
        return False
    if b"<html" in body[:1000].lower():
        # RSSHub 的错误页是 HTML，不是 feed
        text = re.sub(r"<[^>]+>", " ", body[:6000].decode("utf-8", "replace"))
        print(f"  HTML 页面（非 feed）: {' '.join(text.split())[:260]}")
        return False
    if not any(tag in body[:2000].lower() for tag in (b"<rss", b"<feed", b"<?xml")):
        # JSON / 纯文本接口，打印片段便于排查（例如找作者 ID 的 lineup API）
        print(f"  响应片段（非 XML）: {body[:300].decode('utf-8', 'replace')}")

    feed = feedparser.parse(body)
    print(f"  feed title: {feed.feed.get('title')!r} | items: {len(feed.entries)}")
    if feed.bozo:
        print(f"  解析警告: {str(feed.bozo_exception)[:150]}")
    for entry in feed.entries[:3]:
        date = entry.get("published") or entry.get("updated") or ""
        print(f"    - {(entry.get('title') or '')[:88]} | {date}")
    return len(feed.entries) > 0


def extract_authors(url: str, timeout: int = 30) -> None:
    """从 Bloomberg 文章页 HTML 里提取 /authors/{id}/{slug} 链接。"""
    print(f"\n=== 提取作者链接: {url} ===", flush=True)
    try:
        resp = requests.get(url, headers={"User-Agent": UA}, timeout=timeout)
    except Exception as exc:
        print(f"  请求失败: {type(exc).__name__}: {str(exc)[:200]}")
        return
    print(f"  HTTP {resp.status_code} | {len(resp.content)} bytes")
    hits = sorted(set(_AUTHOR_LINK_RE.findall(resp.text)))
    for author_id, slug in hits:
        print(f"  https://www.bloomberg.com/authors/{author_id}/{slug}.rss")
    if not hits:
        print("  未找到 /authors/{id}/{slug} 链接（可能是反爬拦截或 JS 渲染）")


def main() -> None:
    ap = argparse.ArgumentParser(description="探测 RSS 源可达性（只读）")
    ap.add_argument("urls", nargs="*", help="要探测的 feed URL")
    ap.add_argument("--extract-authors", metavar="ARTICLE_URL",
                    help="从 Bloomberg 文章页提取作者 ID/slug")
    ap.add_argument("--timeout", type=int, default=45, help="单请求超时秒数")
    ap.add_argument("--feedparser", action="store_true",
                    help="用 feedparser.parse(url) 走生产代码相同的抓取路径")
    args = ap.parse_args()

    urls = args.urls or os.environ.get("PROBE_FEEDS", "").split()
    pages = ([args.extract_authors] if args.extract_authors else []) \
        or os.environ.get("PROBE_AUTHOR_PAGES", "").split()

    if not urls and not pages:
        ap.error("没有目标：用位置参数 / PROBE_FEEDS / --extract-authors / PROBE_AUTHOR_PAGES 提供")

    ok = sum(bool(probe(u, args.timeout, args.feedparser)) for u in urls)
    for page in pages:
        extract_authors(page, args.timeout)
    # 只读探测：部分源不可用是「探测结论」，不该把 workflow 标成失败，由日志里看结果即可
    print(f"\n=== 可用 feed: {ok}/{len(urls)} ===", flush=True)


if __name__ == "__main__":
    main()

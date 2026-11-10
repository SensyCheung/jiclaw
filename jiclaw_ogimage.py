"""jiclaw_ogimage.py - 抓取原站的分享封面（og:image）。

SemiVoice 文章页的 og:image / twitter:image 直接使用这里的绝对外链：
社交平台爬虫自己会去取图，站内页面既不用托管图片，也无需在渲染时
请求第三方。og:image 本来就是给外部平台抓的公开地址，通常不做
Referer 校验（实测 10 个源站封面，带不带 Referer 都是 200；个别
超时的是 Google / Twitter 的 CDN 在国内不可达，不是防盗链）。

调用方只需要两步：
  1. 抓正文拿到 HTML 时 remember_og_image(url, html) —— 零额外请求；
  2. 写 Notion 前 fetch_og_image(link) —— 命中缓存就不发请求，
     没抓过正文的源（RSS 摘要型 / 特殊分支）才单独取一次页面头部。
"""

from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

# key 为原文 URL，value 为绝对图片地址（没抓到就是空串）。
OG_IMAGE_CACHE: dict[str, str] = {}

_HEAD_LIMIT = 200_000
_MAX_URL_LEN = 2000
_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0 Safari/537.36"
)

_META_SELECTORS = (
    {"property": "og:image"},
    {"property": "og:image:url"},
    {"name": "twitter:image"},
    {"name": "twitter:image:src"},
)


def _extract(html: str, base_url: str) -> str:
    """只解析 head，避免为几 MB 的正文建整棵 DOM 树。"""
    head = html[:_HEAD_LIMIT]
    end = head.lower().find("</head>")
    if end > 0:
        head = head[:end + 7]
    soup = BeautifulSoup(head, "html.parser")
    for attrs in _META_SELECTORS:
        tag = soup.find("meta", attrs=attrs)
        if not tag:
            continue
        raw = (tag.get("content") or "").strip()
        if not raw or raw.startswith(("data:", "blob:", "javascript:")):
            continue
        absolute = urljoin(base_url, raw)
        if absolute.startswith(("http://", "https://")):
            return absolute[:_MAX_URL_LEN]
    return ""


def remember_og_image(url: str, html: str) -> str:
    """正文抓取时顺手记下分享封面；返回绝对路径或空串。"""
    if not url or not html:
        return ""
    value = _extract(html, url)
    OG_IMAGE_CACHE[url] = value
    return value


def fetch_og_image(url: str) -> str:
    """取分享封面：优先用正文抓取时缓存的结果，否则单独请求一次页面头部。"""
    if not url or not url.startswith(("http://", "https://")):
        return ""
    if url in OG_IMAGE_CACHE:
        return OG_IMAGE_CACHE[url]

    resp = None
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": _UA},
            timeout=8,
            stream=True,
        )
        resp.raise_for_status()
        chunk = next(resp.iter_content(chunk_size=_HEAD_LIMIT), b"") or b""
        html = chunk.decode(resp.encoding or "utf-8", errors="ignore")
        return remember_og_image(url, html)
    except Exception as e:
        print(f"抓取 og:image 失败（不影响正文与入库）：{str(e)[:100]}")
        OG_IMAGE_CACHE[url] = ""
        return ""
    finally:
        if resp is not None:
            resp.close()

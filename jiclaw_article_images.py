"""jiclaw_article_images.py - 统计原站**正文**里的配图数量。

SemiVoice 用这个数字在封面右下角提示「原文还有几张图」，帮读者判断值不值得
点进原文。只存一个数字，不下载、不托管正文图：站内的封面仍旧是 og:image，
正文配图留在原站。

和 jiclaw_ogimage.py 一样走「零额外请求」路线：抓正文时本来就拿到了完整
HTML，顺手数一遍写进缓存；写库时从缓存取。没抓正文的源（RSS 摘要型 /
特殊分支）不为了计数再下载整页，直接记 0 —— 那些条目本来就没有可靠正文。
"""

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

# key 为原文 URL，value 为正文内容图数量。
IMAGE_COUNT_CACHE: dict[str, int] = {}

# 大页面截断，避免为一篇长文建几十 MB 的 DOM 树。正文图一般都在前半部分。
_MAX_HTML = 3_000_000
_MAX_COUNT = 99

# 拆掉和正文无关的区域，口径与 jiclaw_core.py 的正文提取保持一致。
_NOISE_TAGS = (
    "script", "style", "nav", "header", "footer", "aside", "noscript",
    "form", "button", "svg", "iframe",
)

# 语义容器按优先级排列：命中「有文字 + 有图」的第一个就用，不再往外扩，
# 否则 main / body 会把侧栏推荐位、相关文章缩略图一起算进来。
_CONTAINER_SELECTORS = (
    "[itemprop='articleBody']",
    "article .article-body",
    "article .entry-content",
    "article .post-content",
    "article",
    ".article-content",
    ".article-body",
    ".articleBody",
    ".post-content",
    ".entry-content",
    "#article-content",
    "#articleContent",
    "main",
)

# 懒加载站点把真图藏在 data-* 里，src 往往是 1x1 占位图。
_SRC_ATTRS = (
    "data-original",
    "data-original-src",
    "data-src",
    "data-lazy-src",
    "data-url",
    "src",
)

# 图标 / 头像 / 广告 / 追踪像素 / 站点装饰，一律不算「正文配图」。
_JUNK = re.compile(
    r"icon|logo|avatar|emoji|spacer|pixel|blank|placeholder|tracking|beacon|"
    r"qrcode|qr_code|advert|banner|gravatar|spinner|loading|1x1|sprite|"
    r"subscribe|newsletter|promo|widget|sidebar|related|recommend|copyright|"
    r"watermark|node-list-|(?<!\w)look(?!\w)|view[-_]?count|hit[-_]?count|"
    r"(?:^|[/_\-.])ad(?:s)?(?=[/_\-.\d]|$)",
    re.I,
)


def _int_attr(img, name: str):
    raw = img.get(name)
    if raw is None:
        return None
    match = re.match(r"\s*(\d+)", str(raw))
    return int(match.group(1)) if match else None


def _is_junk(img, src: str) -> bool:
    width = _int_attr(img, "width")
    height = _int_attr(img, "height")
    if width is not None and height is not None and width <= 100 and height <= 100:
        return True

    # 文件名 + class + alt 一起看：很多站点只靠 class="article-icon" 标装饰图。
    classes = img.get("class") or []
    if isinstance(classes, str):
        classes = classes.split()
    haystack = " ".join([
        str(img.get("alt") or ""),
        str(img.get("id") or ""),
        " ".join(str(c) for c in classes),
        src[:220],
    ])
    return bool(_JUNK.search(haystack))


def _real_src(img) -> str:
    """取懒加载真图，跳过 data: / blob: 占位。"""
    for attr in _SRC_ATTRS:
        raw = (img.get(attr) or "").strip()
        if not raw:
            continue
        low = raw.lower()
        if low.startswith(("data:", "blob:", "javascript:", "about:")):
            continue
        if _is_junk(img, raw):
            continue
        return raw
    # 只有 srcset 的响应式图：取第一项。
    for attr in ("data-srcset", "srcset"):
        raw = (img.get(attr) or "").strip()
        if not raw:
            continue
        first = raw.split(",")[0].strip().split(" ")[0]
        if first and not first.lower().startswith(("data:", "blob:")):
            if not _is_junk(img, first):
                return first
    return ""


def _pick_container(soup):
    for selector in _CONTAINER_SELECTORS:
        for node in soup.select(selector):
            if node.find("img") and len(node.get_text(strip=True)) >= 120:
                return node
    # 兜底用 body（噪声标签已经拆掉），总比为了数图再猜一次 DOM 稳。
    return soup.body or soup


def count_content_images(html: str, base_url: str = "") -> int:
    """从完整 HTML 里数正文内容图；同一张图重复出现只算一次。"""
    if not html:
        return 0
    try:
        soup = BeautifulSoup(html[:_MAX_HTML], "html.parser")
        for tag in soup(list(_NOISE_TAGS)):
            tag.decompose()
        container = _pick_container(soup)

        seen: set[str] = set()
        for img in container.find_all("img"):
            raw = _real_src(img)
            if not raw:
                continue
            key = urljoin(base_url, raw).split("#")[0] if base_url else raw.split("#")[0]
            if key:
                seen.add(key)
        return min(len(seen), _MAX_COUNT)
    except Exception as e:
        print(f"统计正文配图失败（不影响正文与入库）：{str(e)[:100]}")
        return 0


def remember_image_count(url: str, html: str) -> int:
    """正文抓取时顺手记下配图数量；返回 0 表示没抓到或没有配图。"""
    if not url:
        return 0
    value = count_content_images(html, url)
    IMAGE_COUNT_CACHE[url] = value
    return value


def fetch_image_count(url: str) -> int:
    """写库前取配图数量。没有缓存就是没抓过正文，不再额外请求原站。"""
    if not url:
        return 0
    return IMAGE_COUNT_CACHE.get(url, 0)

"""
jiclaw_core.py - 核心功能模块
包含 RSS 抓取、内容提取、AI 摘要生成、Notion 写入等公共功能
"""

import os
import json
import re
import hashlib
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from jiclaw_scraper import scrape_site
from scraper_config import SCRAPER_CONFIG
from jiclaw_twitter import fetch_twitter_tweets
from jiclaw_telegram import send_to_telegram
from jiclaw_discord import send_to_discord


# 直接使用 RSS 摘要的 feed 名单
# 在这个名单中的 feed 直接使用 RSS 提供的摘要，不抓取网页正文
# 不在名单中的 feed 都会抓取网页正文
USE_RSS_SUMMARY_FEEDS = [
    "semiwiki.com",
    # 可以添加更多直接使用 RSS 摘要的 feed 域名
]


def _should_fetch_content(feed_url: str) -> bool:
    """判断是否需要抓取网页正文内容
    
    Returns:
        True: 需要抓取正文
        False: 使用 RSS 摘要
    """
    # 在名单中的 feed 不抓取正文，直接使用 RSS 摘要
    for domain in USE_RSS_SUMMARY_FEEDS:
        if domain in feed_url:
            return False
    # 不在名单中的 feed 都抓取正文
    return True


def get_latest_item(feed_url: str):
    """从 RSS 源中获取最新一篇文章的标题、链接和摘要。"""
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        print("该 RSS 源没有找到任何条目。")
        return None

    latest = feed.entries[0]

    title = latest.get("title", "无标题")
    link = latest.get("link", "无链接")
    summary = latest.get("summary", "")

    published = latest.get("published") or latest.get("updated") or ""
    published_parsed = latest.get("published_parsed") or latest.get("updated_parsed")
    published_date = None
    if published_parsed:
        try:
            dt = datetime(*published_parsed[:6])
            published_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception:
            published_date = None

    print("最新文章标题：", title)
    print("最新文章链接：", link)

    return {
        "title": title,
        "link": link,
        "summary": summary,
        "published": published,
        "published_date": published_date,
    }


def get_feed_items(feed_url: str, limit: int = 10) -> list[dict]:
    """从 RSS 源中获取最近若干篇文章的信息。"""
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        print("该 RSS 源没有找到任何条目。")
        return []

    items: list[dict] = []

    for entry in feed.entries[:limit]:
        title = entry.get("title", "无标题")
        link = entry.get("link", "无链接")
        summary = entry.get("summary", "")

        published = entry.get("published") or entry.get("updated") or ""
        published_parsed = entry.get("published_parsed") or entry.get(
            "updated_parsed"
        )
        published_date = None
        if published_parsed:
            try:
                dt = datetime(*published_parsed[:6])
                # feedparser 解析的时间已经是本地时间，需要根据时区转换为 UTC
                # 检查是否有时间偏移信息
                if hasattr(entry, 'published_parsed') and len(entry.published_parsed) > 6:
                    # 有时区偏移（如 +0900 表示韩国时间）
                    tz_offset = entry.published_parsed[6]  # 单位为秒
                    from datetime import timedelta
                    dt = dt - timedelta(seconds=tz_offset)
                
                published_date = dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except Exception:
                published_date = None

        items.append(
            {
                "title": title,
                "link": link,
                "summary": summary,
                "published": published,
                "published_date": published_date,
            }
        )

    return items


def clean_businesswire_url(url: str) -> str:
    """
    清洗 Business Wire 的 URL，移除追踪参数并规范化格式
    
    处理规则：
    1. http:// → https://
    2. 移除 ?feedref=xxx 追踪参数
    3. URL 解码并 ASCII 化标题（移除特殊字符如 '）
    4. 确保末尾无 '/'
    """
    from urllib.parse import unquote
    
    # 1. 协议升级
    url = url.replace("http://", "https://")
    
    # 2. 移除追踪参数
    if "?feedref=" in url:
        url = url.split("?feedref=")[0]
    elif "?" in url:
        # 移除其他查询参数
        url = url.split("?")[0]
    
    # 3. URL 解码（将 %E2%80%99 等编码字符还原）
    url = unquote(url)

    # 4. 替换特殊字符为 ASCII（删除左右单引号、双引号等）
    # 左单引号 U+2018, 右单引号 U+2019, 左双引号 U+201C, 右双引号 U+201D
    url = (
        url.replace("\u2018", "").replace("\u2019", "")
        .replace("\u201C", "").replace("\u201D", "")
        .replace("'", "").replace('"', '')
    )

    # 5. 移除末尾的 '/'
    url = url.rstrip("/")
    
    return url


def fetch_article_content(url: str) -> str:
    """抓取网页正文内容，返回纯文本。

    优化策略：
    1. 针对 JS 动态加载网站使用 Playwright
    2. 针对静态网站使用 requests（更快）
    3. 智能正文提取：基于文本密度、链接密度等
    4. 多选择器尝试，提高成功率
    5. 添加重试机制
    6. Business Wire 专用 URL 清洗
    """

    # Business Wire 专用 URL 清洗
    if "businesswire.com" in url:
        url = clean_businesswire_url(url)

    # 需要 Playwright 处理的 JS 动态加载网站列表
    playwright_sites = [
        "broadcom.com",
        "coherent.com",
        "developer.nvidia.com",
        "fiercesensors.com",
        "tweaktown.com",
    ]

    # 检查是否需要 Playwright
    use_playwright = any(site in url for site in playwright_sites)

    if use_playwright:
        return fetch_article_content_playwright(url)

    # 静态网站使用 requests
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        )
    }

    # 重试机制
    max_retries = 2
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=headers, timeout=15)
            resp.raise_for_status()
            html = resp.text
            break
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"抓取网页失败：{e}")
                return ""
            continue

    soup = BeautifulSoup(html, "html.parser")
    
    # 移除无关元素（脚本、样式、导航等）
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()

    # 针对特定网站的特殊处理
    text = fetch_site_specific_content(url, soup)
    if text and len(text) > 200:
        return text

    # 智能正文提取
    text = extract_main_content(soup)
    if text and len(text) > 200:
        return text

    # 兜底：直接取整个 body 文本
    body = soup.body.get_text(separator="\n", strip=True) if soup.body else ""
    return body if len(body) > 100 else ""


def fetch_site_specific_content(url: str, soup) -> str:
    """针对特定网站的正文提取规则"""

    # thelec.net
    if "thelec.net" in url:
        node = soup.select_one("section#user-container")
        if node:
            return node.get_text(separator="\n", strip=True)

    # tomshardware.com
    if "tomshardware.com" in url:
        node = soup.select_one("div#widgetArea16")
        if node:
            return node.get_text(separator="\n", strip=True)

    # Nvidia News
    if "nvidianews.nvidia.com" in url:
        node = soup.select_one("div.nv-content-wrap")
        if node:
            return node.get_text(separator="\n", strip=True)

    # Intel Newsroom
    if "newsroom.intel.com" in url:
        node = soup.select_one("div.article-body, div.content")
        if node:
            return node.get_text(separator="\n", strip=True)

    # Lam Research
    if "lamresearch.com" in url:
        node = soup.select_one("div.article-content, main")
        if node:
            return node.get_text(separator="\n", strip=True)

    # Tweaktown
    if "tweaktown.com" in url:
        # 文章正文在 div.article-content 中
        node = soup.select_one("div.article-content")
        if not node:
            node = soup.select_one("div.content")
        if not node:
            node = soup.select_one("article")
        if node:
            return node.get_text(separator="\n", strip=True)

    return ""


def extract_main_content(soup) -> str:
    """
    智能提取网页正文内容
    基于文本密度、链接密度、节点深度等特征
    """
    # 常见正文容器选择器（按优先级排序）
    candidates = [
        "article",
        "main",
        "div#content",
        "div.content",
        "div#article",
        "div.article",
        "div.post-content",
        "div.entry-content",
        "div.article-content",
        "div.post-body",
        "div.story-content",
        "div.body",
        "section.article",
        "div[class*='article']",
        "div[class*='content']",
        "div[class*='post']",
        "div[class*='story']",
    ]
    
    best_node = None
    best_score = 0
    
    for selector in candidates:
        nodes = soup.select(selector)
        for node in nodes:
            score = calculate_content_score(node)
            if score > best_score:
                best_score = score
                best_node = node
    
    if best_node and best_score > 50:
        text = best_node.get_text(separator="\n", strip=True)
        if len(text) > 200:
            return clean_article_content(text)
    
    # 如果没有找到合适的节点，尝试查找文本最密集的 div
    divs = soup.find_all("div")
    best_div = None
    best_div_score = 0
    
    for div in divs:
        score = calculate_content_score(div)
        if score > best_div_score:
            best_div_score = score
            best_div = div
    
    if best_div and best_div_score > 30:
        text = best_div.get_text(separator="\n", strip=True)
        if len(text) > 200:
            return clean_article_content(text)
    
    return ""


def clean_article_content(text: str) -> str:
    """
    清理文章内容，过滤无用信息

    过滤内容：
    1. XenForo/XPress 调试输出
    2. PHP 对象结构
    3. 堆栈跟踪
    4. 其他框架调试信息
    """
    import re

    lines = text.split('\n')
    cleaned_lines = []

    # 标记是否在处理调试块
    in_debug_block = False
    debug_block_depth = 0

    # 调试信息特征
    debug_keywords = [
        'XF\\Mvc\\Entity',
        'XF\\M\\',
        'ThemeHouse\\',
        'ArrayCollection',
        '=> Array',
        '=> Object',
        '[entities:protected]',
        '[rootClass:protected]',
        '[_uniqueEntityId',
        '[_useReplaceInto',
        '[_newValues:protected]',
        '[_error:protected]',
        '[private]',
        '[protected]',
        'Threads',  # XenForo 调试输出开头
        '[node_name]',  # XenForo 节点信息
        '[_deleted:protected]',
        '[_readOnly:protected]',
        '[_writePending:protected]',
        'Nodes',  # XenForo Nodes 调试输出
    ]

    for line in lines:
        stripped = line.strip()

        # 检测调试块开始
        if 'ArrayCollection' in stripped or 'XF\\Mvc\\Entity' in stripped or 'Nodes' in stripped:
            in_debug_block = True
            debug_block_depth = 0
            continue

        # 过滤 PHP 数组/对象格式的行（如 "[0] => 2" 或 "[node_name] => xxx"）
        if re.match(r'^\[[\w_:]+\]\s*=>\s*.*$', stripped):
            continue
        
        # 过滤保护属性行（如 "[_writeRunning:protected] =>"）
        if re.match(r'^\[_\w+:[\w:]+\]\s*=>\s*.*$', stripped):
            continue

        if in_debug_block:
            # 计算括号深度
            debug_block_depth += stripped.count('(') - stripped.count(')')

            # 检查是否是调试信息行
            is_debug = False
            for keyword in debug_keywords:
                if keyword in stripped:
                    is_debug = True
                    break

            # 检查是否是括号行
            if stripped in ['(', ')', 'Array', 'Object', '']:
                is_debug = True

            # 如果深度回到 0 且有正常内容，退出调试块
            if debug_block_depth <= 0 and not is_debug and len(stripped) > 10:
                in_debug_block = False
            elif is_debug or debug_block_depth > 0:
                continue

        # 过滤单独的括号和空行
        if stripped in ['(', ')', 'Array', 'Object']:
            continue

        # 过滤包含调试关键词的行
        is_debug_line = False
        for keyword in debug_keywords:
            if keyword in stripped:
                is_debug_line = True
                break

        if not is_debug_line and stripped:
            cleaned_lines.append(stripped)

    # 重新组合，去除多余空行
    result = '\n'.join(cleaned_lines)
    result = re.sub(r'\n{3,}', '\n\n', result)  # 多个空行变一个
    result = result.strip()

    return result


def calculate_content_score(node) -> float:
    """
    计算节点的内容质量分数
    基于：文本长度、链接密度、文本密度等
    """
    text = node.get_text(separator=" ", strip=True)
    text_len = len(text)
    
    if text_len < 50:
        return 0
    
    # 计算链接文本比例
    links_text = ""
    for a in node.find_all("a"):
        links_text += a.get_text(" ", strip=True)
    link_ratio = len(links_text) / text_len if text_len > 0 else 0
    
    # 链接比例过高可能是导航或列表
    if link_ratio > 0.5:
        return 0
    
    # 计算文本密度（文本长度/HTML 长度）
    html_len = len(str(node))
    text_density = text_len / html_len if html_len > 0 else 0
    
    # 计算节点深度（不要太深也不要太浅）
    depth = 0
    parent = node.parent
    while parent:
        depth += 1
        parent = parent.parent
    
    # 分数计算
    score = text_len * 0.5  # 基础分
    score += text_density * 100  # 文本密度加分
    score -= link_ratio * 50  # 链接比例扣分
    
    # 深度适中加分
    if 3 <= depth <= 10:
        score += 20
    
    return score


def fetch_article_content_playwright(url: str) -> str:
    """使用 Playwright 抓取 JS 动态加载的网页内容（带 stealth 绕过 Cloudflare）"""
    from playwright.sync_api import sync_playwright

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-accelerated-2d-canvas',
                    '--disable-gpu',
                    '--window-size=1920,1080',
                ]
            )

            context = browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
            )
            page = context.new_page()

            # stealth 设置
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
                Object.defineProperty(document, 'hidden', {get: () => false});
                Object.defineProperty(document, 'visibilityState', {get: () => 'visible'});
            """)

            page.goto(url, wait_until="domcontentloaded", timeout=120000)

            # 等待内容加载
            page.wait_for_timeout(10000)

            # 尝试滚动触发懒加载
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)
            except:
                pass

            html_content = page.content()
            browser.close()

        soup = BeautifulSoup(html_content, "html.parser")

        # 移除无关元素
        for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
            tag.decompose()

        # 针对特定网站的处理
        text = fetch_site_specific_content(url, soup)
        if text and len(text) > 200:
            return text

        # 智能正文提取
        text = extract_main_content(soup)
        if text and len(text) > 200:
            return text

        # 兜底
        body = soup.body.get_text(separator="\n", strip=True) if soup.body else ""
        return body if len(body) > 100 else ""
        
    except Exception as e:
        print(f"Playwright 抓取失败：{e}")
        return ""


def fetch_broadcom_blog_content(url: str) -> str:
    """使用 Playwright 抓取 Broadcom Blog 文章内容（JS 动态加载）"""
    # 已整合到 fetch_article_content_playwright
    return fetch_article_content_playwright(url)


def summarize_with_ai(
    title: str, summary: str, content: str, model: str = "glm-4-flash"
) -> dict:
    """调用大模型，对文章做中英文摘要与标签提取，返回 JSON 字典。"""
    api_key = os.environ.get("ZHIPU_API_KEY")
    if not api_key:
        raise RuntimeError("环境变量 ZHIPU_API_KEY 未设置")

    client = OpenAI(
        api_key=api_key,
        base_url="https://open.bigmodel.cn/api/paas/v4/",
    )

    user_content = (
        f"Title: {title}\n\n"
        f"RSS Summary: {summary}\n\n"
        f"Full Article Content:\n{content}"
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": '''
术语翻译规范 (Strict):
'Agile Analog' : 'Agile Analog'(不翻译)
"Silicon Catalyst" : "Silicon Catalyst"(不翻译)
"DeepCool" : "九州风神 DeepCool"
"Noctua" : "奥地利猫头鹰 Noctua"
"Microchip" : "Microchip"(不翻译)
"Micron" : "美光科技(Micron)"
"X-Epic" : "芯华章"
'Made by Google' : 'Made by Google'(不翻译)
'Lam Research' : '泛林集团'
"Silicon Motion" : "慧荣科技(SMI)"
"ZEPHYR": "西风泽弗(ZEPHYR)" 
"MSI": "微星(MSI)"
---
你将看到一篇科技/半导体/硬件等方向的文章信息（标题 + RSS 摘要 + 抓取到的正文）。
请基于完整正文内容完成以下任务，并严格只输出 JSON：

1. **Tags 提取**: 
   - 从下方的 [Candidate Tags] 列表中选择 **1-3 个** 最相关的标签。
   - **禁止原则**: 严禁选择正文中未提及的公司或技术。如果列表中没有合适的，请优先提取正文中的公司名。
   - [Candidate Tags]: 'Gaming', 'NVIDIA', 'SK Hynix', 'DRAM', '3D IC', 'GPU', 'CPU', 'AI', 'AI PC', 'HBM', 'NPU', 'SSD', 'Chiplet', 'EUV', 'EMIB', 'EDA', 'HPC', 'AMD', 'Dell', 'Linux', '3nm', 'laptop', 'Raspberry Pi', 'Switch', 'PCIe', 'GDDR', '2nm', 'Semiconductor', 'TI', 'ARM', 'memory', 'Monitor', 'automotive', 'Laptop', 'Cybersecurity', 'Privacy', 'Microchip', 'Asus', 'Infineon', 'AI chip', 'Software', 'GaN', 'iOS', 'Cooling'.
   - [Prohibited Tags]: 'Reviews', 'Featured Tech News', 'Tech News', 'technology', 'Tech Industry', 'Hardware', 'semiconductor', 'Industry', 'electronics', 'Manufacturing', 'Sales'.
2. 将英文标题翻译为简体中文（title_cn）。

3. 要点摘要：基于正文内容，提取正文中的硬核技术/商业信息。：
   - summary_en：用英文输出 3-8 个要点，使用 HTML 段落标签包装，
     例如："<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>"
   - summary_cn：用简体中文输出 3-8 个要点，格式同上。

只输出严格 JSON 格式，不要任何解释或额外文本：
{
  "tags": ["aaa", "bbb", "ccc"],
  "title_cn": "被翻译的中文标题",
  "summary_en": "<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>",
  "summary_cn": "<p>➀ xxx；</p><p>➁ xxx；</p><p>➂ xxx</p>"
}
请最后检查输出是否符合要求，不要出现任何错误，比如翻译要准确，标签要完全匹配等，JSON格式要正确。
                ''',
            },
            {
                "role": "user",
                "content": user_content,
            },
        ],
        stream=False,
    )

    result = response.choices[0].message.content

    pattern = re.compile(r"\{.*?\}", re.DOTALL)
    match = pattern.search(result)
    if not match:
        raise ValueError("模型返回中未找到 JSON")

    data = json.loads(match.group())
    return data


def get_website_name(feed_url: str) -> str:
    """根据 RSS URL 提取网站名称。"""
    parsed = urlparse(feed_url)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()

    if domain.startswith("www."):
        domain = domain[4:]

    domain_map = {
        "trendfore.com": "trendfore",
        "thelec.net": "thelec",
        "tomshardware.com": "tomshardware",
        "newsroom.lamresearch.com": "Lam Research",
        "ir.appliedmaterials.com": "Applied Materials",
        "nvidianews.nvidia.com": "Nvidia News",
        "developer.nvidia.com": "Nvidia Developer",
        "newsroom.intel.com": "Intel Newsroom",
        "www.coherent.com": "Coherent",
        "coherent.com": "Coherent",
        "www.iccsz.com": "讯石光通讯",
        "iccsz.com": "讯石光通讯",
        "rss.etnews.com": "etnews",
        "feed.businesswire.com": "Business Wire",
        "news.mit.edu": "MIT News",
        "feeds.feedburner.com": "ServeTheHome",
        "entegrisinc.gcs-web.com": "Entegris",
        "azonano.com": "Azom Nano",
        "electronicsweekly.com": "Electronics Weekly",
        "semiwiki.com": "SemiWiki",
        "anysilicon.com": "AnySilicon",
        "idw-online.de": "IDW Online",
        "seekingalpha.com": "Seeking Alpha",
    }

    # Broadcom 特殊处理：根据路径区分 News 和 Blog
    if domain == "broadcom.com" or domain == "www.broadcom.com":
        if "/blog" in path:
            return "Broadcom Blog"
        else:
            return "Broadcom"

    return domain_map.get(domain, domain.split(".")[0])


def create_notion_page(
    notion_api_key: str,
    database_id: str,
    item: dict,
    ai_data: dict,
    post_id: str,
    llm_model: str,
    feed_url: str,
) -> None:
    """将 AI 处理后的结果写入 Notion 数据库。"""
    headers = {
        "Authorization": f"Bearer {notion_api_key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    title_en = item.get("title", "")
    link = item.get("link", "")
    published_date = item.get("published_date")

    title_cn = ai_data.get("title_cn") or title_en
    summary_en = ai_data.get("summary_en", "")
    summary_cn = ai_data.get("summary_cn", "")
    tags = ai_data.get("tags") or []
    website_name = get_website_name(feed_url)

    def truncate(text: str, max_len: int = 1800) -> str:
        return text if len(text) <= max_len else text[: max_len - 3] + "..."

    date_value = {"start": published_date} if published_date else None

    properties = {
        "Name": {
            "title": [
                {
                    "text": {
                        "content": truncate(title_en),
                    }
                }
            ]
        },
        "Date": {
            "date": date_value,
        },
        "Title_cn": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate(title_cn),
                    }
                }
            ]
        },
        "HotorNot": {
            "select": {"name": "Home"},
        },
        "Description": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate(summary_cn),
                    }
                }
            ]
        },
        "URL": {
            "url": link or None,
        },
        "Website": {
            "rich_text": [
                {
                    "text": {
                        "content": website_name,
                    }
                }
            ]
        },
        "tags": {
            "multi_select": [{"name": str(tag)} for tag in tags],
        },
        "summary": {
            "rich_text": [
                {
                    "text": {
                        "content": truncate(summary_en),
                    }
                }
            ]
        },
        "LLM": {
            "rich_text": [
                {
                    "text": {
                        "content": llm_model,
                    }
                }
            ]
        },
        "POSTID": {
            "rich_text": [
                {
                    "text": {
                        "content": post_id,
                    }
                }
            ]
        },
    }

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=headers,
        data=json.dumps(payload),
        timeout=10,
    )

    if resp.status_code >= 300:
        print("写入 Notion 失败：", resp.status_code, resp.text)
    else:
        print("已写入 Notion：", resp.json().get("id"))


def notion_page_exists(
    notion_api_key: str,
    database_id: str,
    post_id: str,
) -> bool:
    """检查给定 POSTID 是否已存在于 Notion 数据库中。"""
    headers = {
        "Authorization": f"Bearer {notion_api_key}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }

    payload = {
        "filter": {
            "property": "POSTID",
            "rich_text": {
                "equals": post_id,
            },
        }
    }

    resp = requests.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers=headers,
        data=json.dumps(payload),
        timeout=10,
    )

    if resp.status_code >= 300:
        print("查询 Notion 数据库失败：", resp.status_code, resp.text)
        return False

    data = resp.json()
    results = data.get("results", [])
    return len(results) > 0


def send_to_telegram_after_notion(
    title_cn: str,
    title_en: str,
    summary_cn: str,
    link: str,
    tags: list,
    website_name: str,
) -> None:
    """在 Notion 上传成功后发送到 Telegram 和 Discord"""
    # Telegram 发送
    try:
        send_to_telegram(
            title_cn=title_cn,
            title_en=title_en,
            summary_cn=summary_cn,
            link=link,
            tags=tags,
            website_name=website_name,
        )
    except Exception as e:
        # Telegram 发送失败不影响主流程
        print(f"发送到 Telegram 失败：{e}")

    # Discord 发送
    try:
        send_to_discord(
            title_cn=title_cn,
            title_en=title_en,
            summary_cn=summary_cn,
            link=link,
            tags=tags,
            website_name=website_name,
        )
    except Exception as e:
        # Discord 发送失败不影响主流程
        print(f"发送到 Discord 失败：{e}")


def process_feed(feed_url: str, model: str = "glm-4-flash", limit: int = 10) -> None:
    """抓取单个 RSS 源的多篇文章并写入 Notion（如已配置）。"""
    # 编码安全输出
    print(f"\n====== 开始处理 RSS 源 ======")

    items = get_feed_items(feed_url, limit=limit)
    if not items:
        print("该 RSS 源没有可处理的条目。")
        return

    _process_items(items, feed_url, model)


def process_scraper(site_name: str, model: str = "glm-4-flash", limit: int = 10, use_proxy: bool = False) -> None:
    """爬取无 RSS 源网站的多篇文章并写入 Notion（如已配置）。"""
    from scraper_config import SCRAPER_CONFIG

    config = SCRAPER_CONFIG.get(site_name)
    if not config:
        print(f"未找到网站配置：{site_name}")
        return

    # 编码安全输出
    print(f"\n====== 开始爬取网站 ======")

    items = scrape_site(site_name, limit=limit, use_proxy=use_proxy)
    if not items:
        print("该网站没有可处理的条目。")
        return

    _process_items(items, site_name, model)


def process_twitter(username: str, model: str = "glm-4-flash", limit: int = 5) -> None:
    """抓取 Twitter 账号的推文并写入 Notion（如已配置）。"""
    from twitter_config import TWITTER_ACCOUNTS

    if username not in TWITTER_ACCOUNTS:
        print("未找到 Twitter 账号配置")
        return

    # 编码安全输出
    print(f"\n====== 开始抓取 Twitter: @{username} ======")

    items = fetch_twitter_tweets(username, limit=limit)
    if not items:
        print("该 Twitter 账号没有可处理的推文。")
        return

    _process_items(items, f"twitter:{username}", model)


def _process_items(
    items: list[dict],
    source: str,
    model: str,
    notion_api_key: str = None,
    notion_db_id: str = None,
) -> None:
    """处理文章列表的公共逻辑（RSS 和爬虫共用）。"""

    if notion_api_key is None:
        notion_api_key = os.environ.get("NOTION_API_KEY")
    if notion_db_id is None:
        notion_db_id = os.environ.get("NOTION_DATABASE_ID")

    # 判断是否需要抓取正文（只针对 RSS feed，爬虫和 Twitter 始终抓取）
    fetch_content = True  # 默认抓取
    if source.startswith("http"):
        # RSS feed，根据名单决定是否抓取
        fetch_content = _should_fetch_content(source)

    for item in items:
        title = item["title"]
        link = item["link"]
        summary = item.get("summary", "")

        print("\n------------------------------")
        # 编码安全输出：截断并转义标题中的特殊字符
        title_safe = title.encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')[:50]
        print(f"处理文章：{title_safe}...")
        print(f"链接：{link}")

        post_id = hashlib.sha1(link.encode("utf-8")).hexdigest()[:10]

        if notion_api_key and notion_db_id:
            if notion_page_exists(notion_api_key, notion_db_id, post_id):
                print("该文章已存在于 Notion（POSTID 重复），跳过。")
                continue
        else:
            print("未配置 NOTION_API_KEY 或 NOTION_DATABASE_ID，仅在终端输出结果。")

        print("正在抓取正文...")
        try:
            content = fetch_article_content(link)
        except Exception as e:
            print("抓取正文失败，将仅基于 RSS 摘要进行总结：", e)
            content = summary

        if not content:
            content = summary

        # --- Debug 代码开始 ---
        try:
            with open("debug_content.txt", "w", encoding="utf-8") as f:
                f.write(f"URL: {link}\n") # 记录来源方便对照
                f.write("-" * 20 + "\n")
                f.write(content)
            print("Debug: 内容已成功写入 debug_content.txt")
        except Exception as debug_e:
            print(f"Debug 写入失败: {debug_e}")
        # --- Debug 代码结束 ---

        print("正在调用大模型生成摘要...")

        data = summarize_with_ai(title, summary, content, model=model)

        print("AI 结果 JSON：")
        # 使用 ensure_ascii=True 避免编码问题
        print(json.dumps(data, ensure_ascii=True, indent=2))

        if notion_api_key and notion_db_id:
            print("正在写入 Notion 数据库...")
            # 获取 source URL
            if source.startswith("http"):
                source_url = source
            elif source in SCRAPER_CONFIG:
                source_url = SCRAPER_CONFIG[source]["url"]
            elif source.startswith("twitter:"):
                username = source.replace("twitter:", "")
                source_url = f"https://twitter.com/{username}"
            else:
                source_url = ""

            create_notion_page(
                notion_api_key,
                notion_db_id,
                item,
                data,
                post_id,
                model,
                source_url,
            )

            # Notion 上传成功后发送到 Telegram
            print("正在发送到 Telegram...")
            send_to_telegram_after_notion(
                title_cn=data.get("title_cn", title),
                title_en=title,
                summary_cn=data.get("summary_cn", ""),
                link=link,
                tags=data.get("tags", []),
                website_name=get_website_name(source_url),
            )

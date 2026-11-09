"""
jiclaw_core.py - 核心功能模块
包含 RSS 抓取、内容提取、AI 摘要生成、Notion 写入等公共功能
"""

import os
import json
import re
import hashlib
import time
from datetime import datetime
from urllib.parse import urlparse

import feedparser
import requests
from bs4 import BeautifulSoup
from openai import OpenAI

from jiclaw_scraper import scrape_site
from scraper_config import SCRAPER_CONFIG
from jiclaw_twitter import fetch_twitter_tweets
from jiclaw_telegram import (
    send_to_telegram,
    resolve_telegram_chat_id,
    is_chinese_original_text,
)
from jiclaw_discord import send_to_discord


# 直接使用 RSS 摘要的 feed 名单
# 在这个名单中的 feed 直接使用 RSS 提供的摘要，不抓取网页正文
# 不在名单中的 feed 都会抓取网页正文
USE_RSS_SUMMARY_FEEDS = [
    # semiwiki.com 也曾列在这里，但历史上这段名单没生效（一直抓的正文）。
    # 它的 feed 摘要只有 450 字符左右，抓正文质量更好，故保持抓正文；
    # 若想改用摘要，取消下面这行注释即可。
    # "semiwiki.com",

    # The Information：文章页被 Cloudflare 拦截（403 "Just a moment"）
    # 且正文在付费墙后，feed 的 content 就是官方导语（平均 900+ 字符），直接用它
    "theinformation.com",
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


def _entry_summary(entry) -> str:
    """取条目摘要：优先 <summary>，没有则退回 Atom <content>。

    The Information 这类站点的 Atom feed 只提供 <content>（官方导语），
    没有 <summary>，不做兜底拿到的就是空字符串，摘要只能靠标题瞎编。
    """
    summary = entry.get("summary") or ""
    if summary:
        return summary

    for block in entry.get("content") or []:
        value = block.get("value") if isinstance(block, dict) else None
        if value:
            return value

    detail = entry.get("summary_detail") or {}
    return detail.get("value", "") if isinstance(detail, dict) else ""


def get_latest_item(feed_url: str):
    """从 RSS 源中获取最新一篇文章的标题、链接和摘要。"""
    feed = feedparser.parse(feed_url)

    if not feed.entries:
        print("该 RSS 源没有找到任何条目。")
        return None

    latest = feed.entries[0]

    title = latest.get("title", "无标题")
    link = latest.get("link", "无链接")
    summary = _entry_summary(latest)

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
        summary = _entry_summary(entry)

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

    # Qualcomm 文章页是 AEM + React SPA，静态 HTML 只有空壳，
    # 用专门的紧凑 Playwright 流程（通用流程固定等待太久，单篇要 2 分钟）
    if "qualcomm.com" in url:
        return fetch_qualcomm_content(url)

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


def fetch_qualcomm_content(url: str) -> str:
    """抓取 Qualcomm 文章正文（AEM + React SPA，只能靠渲染）。

    通用 Playwright 流程里有多段固定等待（10s + 3s + 2s），一篇文章要 2 分钟以上，
    批量抓 10 篇就把整轮拖到一个小时。这里用紧凑版本：
    页面 glyph 出现后直接取 main 的 innerText，单篇 8–15 秒。
    """
    from playwright.sync_api import sync_playwright

    text = ""

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1400, "height": 900},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                locale="en-US",
            )
            page = context.new_page()
            page.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            )

            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_selector("main, article", timeout=20000)
            except Exception:
                pass
            page.wait_for_timeout(1500)

            text = page.evaluate(
                """() => {
                    const el = document.querySelector('main') || document.body;
                    return el ? (el.innerText || '') : '';
                }"""
            )
            browser.close()

    except Exception as e:
        print(f"Qualcomm 正文抓取失败：{e}")

    if text and len(text) > 400:
        return clean_article_content(text)

    # 兜底：至少拿到 og:title / og:description（通常是标题 + 首段导语）
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
            timeout=20,
        )
        parts = []
        for prop in ("og:title", "og:description"):
            m = re.search(
                r'<meta[^>]+property="%s"[^>]+content="([^"]+)"' % prop, resp.text
            )
            if m:
                parts.append(m.group(1).strip())
        fallback = "\n".join(p for p in parts if p)
        if fallback:
            print("  Qualcomm 正文渲染失败，退回 og:title/og:description")
        return fallback
    except Exception:
        return ""


def summarize_with_ai(
    title: str, summary: str, content: str, model: str = "GLM-4.7-Flash",
    published_date: str = None,
) -> dict:
    """调用大模型，对文章做中英文摘要与标签提取，返回 JSON 字典。

    实际使用的模型由环境变量决定（见下方「多模型 Provider」），
    ``model`` 参数作为智谱模型的默认值保留，兼容原有调用方式。
    ``published_date`` 只作为日期锚点喂给模型：中文新闻常省略年份（"11月5日"），
    不给出参考日期时模型会瞎猜年份（实测把 2026 的事件写成了 2024）。
    返回值中会额外带一个 ``_llm_model`` 字段记录本次真正用的模型，
    调用方 pop 出来写日志 / Notion，不入库。
    """
    user_content = (
        f"Title: {title}\n\n"
        f"RSS Summary: {summary}\n\n"
        f"Full Article Content:\n{content}"
    )
    if published_date:
        user_content = f"发布日期: {published_date}\n\n" + user_content

    messages = [
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

1. **行业分类 (category)**：只允许输出 "semiconductor" 或 "other"。必须先做这一步，再写其他字段。
   - 判为 "semiconductor"：文章**核心主题**是芯片/集成电路产业本身 —— IC 设计与销售、晶圆制造与代工、
     半导体设备与材料、存储（DRAM/NAND/HBM）、先进封装与测试、EDA/IP、光刻、功率/模拟/射频芯片、
     传感器芯片、光通讯器件等上游元器件，以及半导体公司的财报/并购/产能/技术路线/供应链。
   - 判为 "other"：文章核心主题不是芯片产业本身 —— 家电、消费电子整机（手机/笔记本/外设/显示器/
     机箱/散热 的评测、导购、上架）、服务器与存储系统整机方案、泛科技/互联网/软件/大模型应用、
     游戏、云计算与运营商商务、汽车整车、纯财经宏观、企业公告与社会类新闻。
   - 判定规则：
     * 只能依据正文中与芯片产业直接相关的实质内容；
     * 标题出现芯片公司名、或正文顺带提到某颗芯片，都**不算**半导体；
     * 若文章同时涉及，只有核心主题是芯片产业时才判 "semiconductor"；
     * **拿不准就判 "other"**，禁止为了保险而默认判成 "semiconductor"。

2. **Tags 提取**:
   - 从下方的 [Candidate Tags] 列表中选择 **1-3 个** 最相关的标签。
   - **禁止原则**: 严禁选择正文中未提及的公司或技术。如果列表中没有合适的，请优先提取正文中的公司名。
   - [Candidate Tags]: 'Gaming', 'NVIDIA', 'SK Hynix', 'DRAM', '3D IC', 'GPU', 'CPU', 'AI', 'AI PC', 'HBM', 'NPU', 'SSD', 'Chiplet', 'EUV', 'EMIB', 'EDA', 'HPC', 'AMD', 'Dell', 'Linux', '3nm', 'laptop', 'Raspberry Pi', 'Switch', 'PCIe', 'GDDR', '2nm', 'Semiconductor', 'TI', 'ARM', 'memory', 'Monitor', 'automotive', 'Laptop', 'Cybersecurity', 'Privacy', 'Microchip', 'Asus', 'Infineon', 'AI chip', 'Software', 'GaN', 'iOS', 'Cooling'.
   - [Prohibited Tags]: 'Reviews', 'Featured Tech News', 'Tech News', 'technology', 'Tech Industry', 'Hardware', 'semiconductor', 'Industry', 'electronics', 'Manufacturing', 'Sales'.
3. 将英文标题翻译为简体中文（title_cn）。

4. 要点摘要：基于正文内容，提取正文中的硬核技术/商业信息。：
   - summary_en：用英文输出 3-8 个要点，使用 HTML 段落标签包装，
     例如："<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>"
   - summary_cn：用简体中文输出 3-8 个要点，格式同上。

5. **风险事件抽取 (risk_event)**：判断本文是否描述了「会影响半导体供应链 / 产能 / 经营连续性」的具体**事件**，
   并把该事件结构化成固定字段。这一步是给下游风险监控平台入库用的，枚举值必须严格照抄，不要自创。
   - is_risk_event：布尔值。文章只是讲技术原理、产品参数、评测导购、行业观点、单纯罗列财报数字，
     而没有发生一个具体事件时，必须为 false。
   - event_type（枚举）：labor_dispute 劳资纠纷/罢工、natural_disaster 地震台风洪水等、power_outage 停电限电、
     fire_explosion 火灾爆炸、water_shortage 缺水、material_shortage 原材料短缺、export_control 出口管制/制裁、
     litigation 诉讼调查、capacity_disruption 停产减产/扩产延期、mna 并购、earnings 财报预警、
     technology_release 技术制程或产品发布、supply_shortage 缺货涨价、geopolitical 地缘政治、other 其他。
     注意 earnings 只用于**坏消息**（下调指引、业绩不及预期、亏损扩大、裁员重组）；
     营收增长、利润创新高、符合预期这类好消息不算风险事件，is_risk_event 给 false，event_type 给 null。
     technology_release 同理：常规的制程量产、新品发布属于日常经营，只要没有明确造成产能中断、订单流失、
     客户转单或供应链冲击，is_risk_event 一律给 false。
     厂区发生火灾、爆炸、起火等安全事故时一律判 fire_explosion；即使报道重点在停产、缺货或
     供应链冲击，也把后果写进 affected_products / severity，不要改判 capacity_disruption 或
     supply_shortage。
   - hazard_class（枚举）：operational 运营、natural 自然、regulatory 监管合规、financial 财务、
     technology 技术、market 市场、other。
   - entity：事件主体公司或机构的标准名称；多个用英文逗号分隔。统一用中文通用名，首次出现时附英文原名，
     例如 "美光科技(Micron)"、"台积电(TSMC)"、"SK海力士(SK Hynix)"。不要只写英文，也不要写成 "Micron Technology Inc."。
     必须是正文里能对应到具体公司的主体；禁止写 "存储巨头"、"某晶圆厂"、"业内人士"、"三星与SK海力士" 这类泛称或泛指集合，
     遇到泛称请回正文找出对应的真实公司名；确实找不出具体主体时填 null。
   - site：具体厂区或地点描述，统一用中文，例如 "桃园厂"、"Fab 18"、"熊本二厂"；没有明确厂区时填国家或城市。
     不要写 "Taoyuan Factory" 这类英文。
   - country_region：国家/地区中文名，例如 "中国台湾"、"美国"、"日本"、"韩国"。
     台湾、香港、澳门一律写作 "中国台湾"、"中国香港"、"中国澳门"，不得当作国家。
   - lat / lon：事件发生厂区或地点的 WGS84 十进制度坐标（纬度 -90~90，经度 -180~180，保留 4-6 位小数）。
     仅在正文明确给出地址/园区、或该厂区位置公开且唯一确定时才填；只知城市、不确定具体厂区、
     或正文没有地点信息时一律 null。**禁止凭印象或按城市中心估算**：错坐标会让下游把影响
     算到错误的晶圆厂上，宁可给 null 也不要猜。
   - severity：0.0-1.0 的浮点数。0.8-1.0 = 全线停产/重大灾害/直接禁运；0.4-0.7 = 部分产线受影响或工期延误；
     0.1-0.3 = 局部摩擦、尚未影响生产；纯技术发布给 0.0。
     涉及十亿美元以上投资、主力产能或先进制程（HBM/DRAM 等紧俏品类）的项目停摆，至少给 0.6。
   - affected_products：受影响品类数组，例如 ["DRAM","NAND","HBM","成熟制程"]；无法判断给 []。
   - occurred_at：事件发生日期 "YYYY-MM-DD"；只知年月写 "YYYY-MM"；不确定填 null（不要用文章发布时间凑）。
     若正文只写了「11月5日」这类省略年份的日期，年份一律以开头给出的「发布日期」为准，不要自行推断其他年份。

只输出严格 JSON 格式，不要任何解释或额外文本（示例中的取值仅示意格式，必须按实际内容判断）：
{
  "category": "other",
  "tags": ["aaa", "bbb", "ccc"],
  "title_cn": "被翻译的中文标题",
  "summary_en": "<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>",
  "summary_cn": "<p>➀ xxx；</p><p>➁ xxx；</p><p>➂ xxx</p>",
  "risk_event": {
    "is_risk_event": false,
    "event_type": null,
    "hazard_class": null,
    "entity": null,
    "site": null,
    "country_region": null,
    "lat": null,
    "lon": null,
    "severity": 0.0,
    "affected_products": [],
    "occurred_at": null
  }
}
请最后检查输出是否符合要求，不要出现任何错误，比如翻译要准确，标签要完全匹配等，JSON格式要正确。
category 只能是 "semiconductor" 或 "other" 两个值之一。
risk_event 必须存在；当 is_risk_event 为 false 时，event_type / hazard_class / entity / site / country_region / occurred_at 全部为 null，
affected_products 为 []；event_type 与 hazard_class 的取值必须来自上面给出的枚举，不能写中文或自造词。
                ''',
            },
            {
                "role": "user",
                "content": user_content,
            },
    ]

    response, provider, used_model = _chat_with_failover(messages, zhipu_model=model)

    result = response.choices[0].message.content
    data = _extract_json_object(result)
    if not isinstance(data, dict):
        raise ValueError("模型返回的 JSON 不是对象")
    _normalize_risk_event(data, published_date)
    data["_llm_model"] = f"{provider}:{used_model}"
    return data


# ---------------------------------------------------------------------------
# 多模型 Provider
#
# 免费档模型都有 RPM / RPD 限制，单靠一家很容易在批量抓取时撞 429。
# 这里把所有可用模型抽象成 provider：
#   - fallback 模式（默认）：智谱优先，只有它失败才动用 Gemini（省额度、风险低）
#   - balance 模式：每篇选最久没用过的 provider，主动把请求摊到两家
# 某个 provider 报错即进入冷却，期间不再打扰它；冷却期内其他 provider 顶上。
# ---------------------------------------------------------------------------

_LLM_PROVIDERS = None
_PROVIDER_STATE = {}

# Gemini 免费层模型名换代快，且「新项目」不开放部分旧模型（gemini-2.5-flash 实测 404）。
# 2026-11 在 AI Studio 新项目上实测：
#   - gemini-flash-lite-latest 是滚动别名，延迟 0.6s / 65s / 超时都出现过，不适合当首选
#   - gemini-3.1-flash-lite 固定版本，实测完整跑通中文长 prompt 并输出严格 JSON，作为默认
#   - gemma-4-* 也可用但会输出 <thought> 思考标签，不适合直接产 JSON，故排最后
# 模型 404 / 503 / 超时时按序自动降级。
_GEMINI_MODEL_CANDIDATES = [
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.5-flash-lite",
    "gemini-flash-latest",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemma-4-31b-it",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]

_RETRYABLE_MARKERS = (
    "429", "rate limit", "ratelimit", "rate_limit", "quota",
    "resource_exhausted", "访问量过大", "500", "502", "503", "504",
    "timeout", "timed out", "超时", "connection", "连接",
)
_MODEL_MISSING_MARKERS = (
    "404", "not found", "not_found", "does not exist",
    "unsupported model", "model_not_found", "unknown model",
)


def _build_llm_providers(zhipu_default: str):
    """按环境变量构建 provider 列表，未配置 Key 的 provider 自动跳过。"""
    providers = []

    zhipu_key = os.environ.get("ZHIPU_API_KEY")
    if zhipu_key:
        providers.append({
            "name": "zhipu",
            "model": os.environ.get("ZHIPU_MODEL") or zhipu_default,
            "candidates": [],
            "client": OpenAI(
                api_key=zhipu_key,
                base_url="https://open.bigmodel.cn/api/paas/v4/",
                timeout=90,
                max_retries=0,
            ),
        })

    gemini_key = os.environ.get("GEMINI_API_KEY")
    if gemini_key:
        model = os.environ.get("GEMINI_MODEL") or _GEMINI_MODEL_CANDIDATES[0]
        providers.append({
            "name": "gemini",
            "model": model,
            # Gemini 提供 OpenAI 兼容端点，无需引入新 SDK
            "candidates": [model] + [m for m in _GEMINI_MODEL_CANDIDATES if m != model],
            "client": OpenAI(
                api_key=gemini_key,
                base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
                timeout=90,
                max_retries=0,
            ),
        })

    # 预留：Groq / 硅基流动 / OpenRouter / Cerebras 等任意 OpenAI 兼容服务
    compat_key = os.environ.get("OPENAI_COMPAT_API_KEY")
    compat_url = os.environ.get("OPENAI_COMPAT_BASE_URL")
    if compat_key and compat_url:
        providers.append({
            "name": "compat",
            "model": os.environ.get("OPENAI_COMPAT_MODEL") or "gpt-4o-mini",
            "candidates": [],
            "client": OpenAI(api_key=compat_key, base_url=compat_url, timeout=90, max_retries=0),
        })

    order = os.environ.get("LLM_PROVIDER_ORDER", "zhipu,gemini,compat")
    rank = {n.strip(): i for i, n in enumerate(order.split(",")) if n.strip()}
    providers.sort(key=lambda p: rank.get(p["name"], 99))
    return providers


def _get_llm_providers(zhipu_model: str = None):
    global _LLM_PROVIDERS
    if _LLM_PROVIDERS is None:
        _LLM_PROVIDERS = _build_llm_providers(zhipu_model or "GLM-4.7-Flash")
        if _LLM_PROVIDERS:
            print("可用模型：" + " / ".join(
                f"{p['name']}={p['model']}" for p in _LLM_PROVIDERS
            ) + f" | 模式={os.environ.get('LLM_MODE', 'fallback')}")
        else:
            print("警告：未配置任何模型 API Key（ZHIPU_API_KEY / GEMINI_API_KEY）")
    return _LLM_PROVIDERS


def _call_provider(p: dict, messages: list):
    """调用单个 provider；模型名失效时自动换用候选列表里的下一个模型。"""
    for _ in range(max(1, len(p["candidates"]))):
        model = p["model"]
        try:
            resp = p["client"].chat.completions.create(
                model=model, messages=messages, stream=False,
            )
            text = (resp.choices[0].message.content or "") if resp.choices else ""
            if not text.strip():
                raise RuntimeError("模型返回空内容（可能被安全策略拦截）")
            return resp, model
        except Exception as e:
            msg = str(e).lower()
            # 模型名失效、以及该模型过载/超时，换一个模型可能就好了；
            # 429 是整个项目的配额问题，换模型没意义，直接交给上层切 provider。
            switchable = any(k in msg for k in _MODEL_MISSING_MARKERS) or any(
                k in msg for k in ("503", "502", "504", "timeout", "timed out", "unavailable")
            )
            if switchable and p["candidates"] and model in p["candidates"]:
                nxt = p["candidates"].index(model) + 1
                if nxt < len(p["candidates"]):
                    p["model"] = p["candidates"][nxt]
                    print(f"  {p['name']} 模型 {model} 不可用，改用 {p['model']}")
                    continue
            raise


def _diagnose_gemini(p: dict) -> None:
    """Gemini 自检失败时展开诊断：列出该 Key 可见的模型，并逐个试调。

    Google 换代很快且新项目不一定开放旧模型（旧名字直接 404），
    光看异常是 404 还是 503 猜不出该用哪个，直接问 models.list 最准。
    """
    print("  --- Gemini 诊断 ---")
    key = os.environ.get("GEMINI_API_KEY", "")
    try:
        r = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            headers={"x-goog-api-key": key},
            params={"pageSize": 100},
            timeout=25,
        )
        print(f"  models.list: HTTP {r.status_code}")
        if r.ok:
            usable = [
                m.get("name", "").replace("models/", "")
                for m in r.json().get("models", [])
                if "generateContent" in (m.get("supportedGenerationMethods") or [])
            ]
            flash = [
                n for n in usable
                if "flash" in n.lower()
                and not any(x in n.lower() for x in ("tts", "image", "omni", "preview", "gemma"))
            ]
            flash.sort(reverse=True)  # 版本号大的优先
            print("  可见纯对话 flash 模型：" + (", ".join(flash[:20]) or "无"))
            candidates = list(dict.fromkeys(flash[:10] + list(_GEMINI_MODEL_CANDIDATES)))
        else:
            print("  models.list 响应：" + r.text[:200])
            candidates = list(_GEMINI_MODEL_CANDIDATES)
    except Exception as e:
        print(f"  models.list 失败：{str(e)[:150]}")
        candidates = list(_GEMINI_MODEL_CANDIDATES)

    print("  逐个试调候选模型：")
    for m in candidates:
        try:
            resp = p["client"].chat.completions.create(
                model=m,
                messages=[{"role": "user", "content": "Reply with exactly: OK"}],
                stream=False,
                timeout=20,
            )
            txt = (resp.choices[0].message.content or "").strip()[:30] if resp.choices else ""
            print(f"    [OK]   {m} | 回复={txt!r}")
        except Exception as e:
            print(f"    [FAIL] {m} | {str(e)[:160]}")


def check_llm_providers() -> dict:
    """启动时逐个探测已配置模型：端点是否可达、Key 是否有效、模型名是否存在。

    每个 provider 只发一条极短请求；结果打进日志，失败也不影响主流程。
    在 Actions 里排查「Gemini 通不通 / 模型名是否过期」时非常有用：

        python -c "from jiclaw_core import check_llm_providers; check_llm_providers()"

    注意：这里直接复用主流程的 client 配置，所以探测结果与实际抓取一致；
    若模型名 404，``_call_provider`` 会顺手把 p["model"] 改成可用的那个。
    """
    providers = _get_llm_providers()
    if not providers:
        print("LLM 自检：未配置任何模型 Key（ZHIPU_API_KEY / GEMINI_API_KEY）")
        return {}

    print(f"LLM 自检：共 {len(providers)} 个 provider")
    report = {}
    for p in providers:
        name, model = p["name"], p["model"]
        t0 = time.time()
        try:
            resp, used = _call_provider(
                p, [{"role": "user", "content": "Reply with exactly: OK"}]
            )
            text = (resp.choices[0].message.content or "").strip()[:40]
            cost = time.time() - t0
            report[name] = {"ok": True, "model": used, "latency": round(cost, 1)}
            print(f"  [OK]   {name}:{used} | {cost:.1f}s | 回复={text!r}")
        except Exception as e:
            report[name] = {"ok": False, "model": p["model"], "error": str(e)[:220]}
            print(f"  [FAIL] {name}:{p['model']} | {str(e)[:220]}")
            if name == "gemini":
                try:
                    _diagnose_gemini(p)
                except Exception as de:
                    print(f"  Gemini 诊断异常：{str(de)[:150]}")
    return report


def check_llm_generation() -> dict:
    """用一篇样例文章完整跑一次摘要，验证模型能否按 prompt 输出严格 JSON。

    连通性自检只证明「能回 OK」，但分类/摘要依赖模型听懂中文长 prompt 并输出
    五个字段；轻量模型很容易在这一步掉链子，所以单独测一次。
    """
    print("LLM 生成自检：用样例文章跑一次完整摘要 ...")
    data = summarize_with_ai(
        "TSMC starts volume production of 2nm chips at Fab 20",
        "TSMC said its 2nm process has entered mass production in Kaohsiung.",
        "TSMC announced today that its 2nm (N2) process has entered volume "
        "production at Fab 20 in Kaohsiung, Taiwan. The node uses gate-all-around "
        "nanosheet transistors and delivers roughly 15 percent higher performance "
        "at the same power, or 30 percent lower power at the same speed, compared "
        "with N3E. Initial customers are expected to tape out mobile and HPC chips. "
        "The company plans to ramp monthly capacity to 80,000 wafers by late 2027.",
    )
    used = data.pop("_llm_model", "?")
    print(f"  使用模型：{used}")
    print("  返回 JSON：" + json.dumps(data, ensure_ascii=False)[:400])
    required = ("category", "tags", "title_cn", "summary_en", "summary_cn", "risk_event")
    missing = [k for k in required if k not in data]
    print("  字段校验：" + ("通过" if not missing else "缺失 " + ",".join(missing)))
    risk = data.get("risk_event") or {}
    print("  " + format_risk_event(risk))
    return data


def _chat_with_failover(messages: list, zhipu_model: str = None):
    """轮询分摊 + 故障转移，返回 (response, provider_name, model_name)。"""
    providers = _get_llm_providers(zhipu_model)
    if not providers:
        raise RuntimeError("未配置任何模型 API Key：请设置 ZHIPU_API_KEY 或 GEMINI_API_KEY")

    mode = os.environ.get("LLM_MODE", "fallback").strip().lower()
    errors = []
    attempted_any = False

    for _ in range(2):
        now = time.time()
        available = [
            p for p in providers
            if _PROVIDER_STATE.get(p["name"], {}).get("until", 0) <= now
        ]
        if not available:
            # 本轮刚把所有 provider 试挂了就不再空等，交给上层跳过/熔断
            # （未写进 Notion 的文章下一轮会重新抓，等待太久反而拖垮整轮任务）
            if attempted_any:
                break
            wait = max(1, min(15, min(
                _PROVIDER_STATE[p["name"]]["until"] for p in providers
            ) - now))
            print(f"  所有模型均在限流冷却中，等待 {wait:.0f}s 后强制重试一次...")
            time.sleep(wait)
            available = list(providers)

        if mode == "balance":
            # 最久没用过的优先，形成天然轮询
            available.sort(key=lambda p: _PROVIDER_STATE.get(p["name"], {}).get("last", 0))

        for p in available:
            name = p["name"]
            attempted_any = True
            st = _PROVIDER_STATE.setdefault(name, {"until": 0.0, "last": 0.0})
            st["last"] = time.time()
            try:
                resp, model = _call_provider(p, messages)
                st["until"] = 0.0
                return resp, name, model
            except Exception as e:
                msg = str(e)
                low = msg.lower()
                if any(k in low for k in ("401", "403", "unauthorized", "invalid api key", "api key not valid", "permission")):
                    cooldown = 600  # Key 无效，别反复试
                elif any(k in low for k in _RETRYABLE_MARKERS):
                    cooldown = 60   # 限流 / 服务端错误，等过一个分钟窗口
                else:
                    cooldown = 20
                st["until"] = time.time() + cooldown
                errors.append(f"{name}: {msg[:110]}")
                print(f"  模型 {name} 调用失败，冷却 {cooldown}s，尝试下一个：{msg[:100]}")

    raise RuntimeError("所有模型均调用失败 -> " + " | ".join(errors[-4:]))


def _extract_json_object(text: str) -> dict:
    """从模型输出中提取第一个合法 JSON 对象。

    用 raw_decode 逐字符尝试，既能处理正文里出现花括号的情况，
    也不会被 JSON 前后的解释文字干扰（正则匹配两种写法都会翻车）。
    """
    if not text:
        raise ValueError("模型返回为空")

    decoder = json.JSONDecoder()
    for idx, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj

    raise ValueError("模型返回中未找到 JSON")


def _extract_category_value(ai_data: dict):
    """从 LLM 结果中取出分类字段的原始值（兼容多种键名）。"""
    for key in ("category", "Category", "行业分类", "category_cn", "is_semiconductor", "isSemiconductor"):
        if key in ai_data:
            return ai_data.get(key)
    return None


# 先判"否"，再判"是"：否则 "非半导体" / "other（非半导体）" 这类值
# 会因为包含"半导体"字样被误判成半导体
_CATEGORY_NEGATIVE_EXACT = {
    "other", "others", "no", "false", "0", "general", "否", "其他", "非半导体",
    "non-semiconductor", "nonsemiconductor", "non_semiconductor", "non-semi",
    "consumer", "consumer electronics", "hardware", "technology", "tech",
}
_CATEGORY_NEGATIVE_KEYWORDS = (
    "其他", "非半导体", "泛科技", "家电", "消费电子", "整机", "游戏",
    "软件", "宏观", "汽车", "互联网", "服务器整机", "评测",
    "other", "non-semi", "consumer", "general",
)
_CATEGORY_POSITIVE_EXACT = {
    "semiconductor", "semi", "chip", "chips", "ic", "yes", "true", "1", "是",
    "半导体", "芯片",
}
_CATEGORY_POSITIVE_KEYWORDS = ("半导体", "芯片", "semiconductor", "chip")


def classify_article_category(ai_data: dict):
    """返回 (is_semiconductor, 原始值, 说明)。

    设计原则：**失败时偏向"其他群组"**。
    主群组只收半导体文章，模型没说清楚时宁可丢到其他群组，
    也不要把非半导体文章灌进主群组。
    """
    raw = _extract_category_value(ai_data)

    if isinstance(raw, bool):
        return raw, raw, "布尔值"
    if raw is None:
        return False, None, "缺失 category 字段 → 归入其他群组"

    text = str(raw).strip().lower()
    if not text:
        return False, raw, "category 为空 → 归入其他群组"

    if text in _CATEGORY_NEGATIVE_EXACT:
        return False, raw, "精确命中非半导体"
    for kw in _CATEGORY_NEGATIVE_KEYWORDS:
        if kw in text:
            return False, raw, f"命中非半导体关键词「{kw}」"

    if text in _CATEGORY_POSITIVE_EXACT:
        return True, raw, "精确命中半导体"
    for kw in _CATEGORY_POSITIVE_KEYWORDS:
        if kw in text:
            return True, raw, f"命中半导体关键词「{kw}」"

    return False, raw, "无法识别的取值 → 归入其他群组"


def is_semiconductor_article(ai_data: dict) -> bool:
    """根据 LLM 返回的 category 字段判断是否为半导体行业文章。

    兼容多种取值（semiconductor/other、true/false、是/否 等）。
    字段缺失或无法识别时按**非半导体**处理，避免非半导体文章误入主群组。
    """
    return classify_article_category(ai_data)[0]


# ---------------------------------------------------------------------------
# 风险事件抽取（供 SEISMIQ 等下游风险平台消费）
#
# 免费档模型偶发会写中文枚举、拼错、或把 0-100 当 0-1，这里统一收敛成固定 schema：
# 枚举不认识就降级为 other，severity 夹到 0-1，缺字段补 null/[]，保证下游入库不会炸。
# ---------------------------------------------------------------------------

_RISK_EVENT_TYPES = {
    "labor_dispute", "natural_disaster", "power_outage", "fire_explosion",
    "water_shortage", "material_shortage", "export_control", "litigation",
    "capacity_disruption", "mna", "earnings", "technology_release",
    "supply_shortage", "geopolitical", "other",
}

_HAZARD_CLASSES = {
    "operational", "natural", "regulatory", "financial",
    "technology", "market", "other",
}

# 模型可能吐中文/别名，能做映射的先映射，映射不了的归 other
_RISK_EVENT_ALIASES = {
    "罢工": "labor_dispute", "劳资纠纷": "labor_dispute", "劳工": "labor_dispute", "strike": "labor_dispute",
    "地震": "natural_disaster", "台风": "natural_disaster", "洪水": "natural_disaster",
    "自然灾害": "natural_disaster", "earthquake": "natural_disaster", "typhoon": "natural_disaster",
    "停电": "power_outage", "限电": "power_outage", "power": "power_outage",
    "火灾": "fire_explosion", "爆炸": "fire_explosion", "fire": "fire_explosion",
    "缺水": "water_shortage", "water": "water_shortage",
    "原材料短缺": "material_shortage", "material": "material_shortage",
    "出口管制": "export_control", "制裁": "export_control", "sanction": "export_control",
    "诉讼": "litigation", "调查": "litigation", "lawsuit": "litigation",
    "停产": "capacity_disruption", "减产": "capacity_disruption", "扩产延期": "capacity_disruption",
    "并购": "mna", "收购": "mna", "merger": "mna", "acquisition": "mna",
    "财报": "earnings", "earnings_call": "earnings",
    "技术发布": "technology_release", "新品": "technology_release", "product": "technology_release",
    "缺货": "supply_shortage", "涨价": "supply_shortage", "shortage": "supply_shortage",
    "地缘政治": "geopolitical", "geopolitics": "geopolitical",
}

_HAZARD_ALIASES = {
    "运营": "operational", "operation": "operational",
    "自然": "natural",
    "监管": "regulatory", "合规": "regulatory", "regulation": "regulatory",
    "财务": "financial", "finance": "financial",
    "技术": "technology",
    "市场": "market",
}


def _empty_risk_event() -> dict:
    return {
        "is_risk_event": False,
        "event_type": None,
        "hazard_class": None,
        "entity": None,
        "site": None,
        "country_region": None,
        "lat": None,
        "lon": None,
        "severity": 0.0,
        "affected_products": [],
        "occurred_at": None,
    }


def _clean_str(value):
    """把模型返回值收敛成干净的字符串或 None。"""
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip().strip('"').strip("'").strip()
    if not value or value.lower() in {"null", "none", "n/a", "na", "unknown", "未知"}:
        return None
    return value[:200]


def _normalize_enum(value, allowed: set, aliases: dict):
    """枚举宽松匹配：大小写/连字符/空格归一，中文别名映射，兜底 other。"""
    raw = _clean_str(value)
    if not raw:
        return None
    key = raw.lower().replace("-", "_").replace(" ", "_")
    if key in allowed:
        return key
    if key in aliases:
        return aliases[key]
    if raw in aliases:
        return aliases[raw]
    return "other"


def _fix_event_year(occurred: str, reference_date: str):
    """校正「11月5日」被模型补错年份的情况。

    只在**同月**且年份偏早、且差距不超过 2 年时才改，避免把真的旧闻（如回顾 2019 年事件）改坏。
    """
    if not occurred or not reference_date:
        return occurred
    m_ev = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", occurred)
    m_ref = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", str(reference_date))
    if not (m_ev and m_ref):
        return occurred

    ev_y, ev_m, ev_d = m_ev.group(1), m_ev.group(2), m_ev.group(3)
    ref_y, ref_m, ref_d = m_ref.group(1), m_ref.group(2), m_ref.group(3)

    if ev_y == ref_y or ev_m != ref_m:
        return occurred
    if int(ev_y) > int(ref_y) or int(ref_y) - int(ev_y) > 2:
        return occurred

    print(f"日期校正：occurred_at {occurred} → 参考发布日期年份 {ref_y}（模型补错年份）")
    # 只换年份，日期粒度保持原样：只知年月就仍返回 "YYYY-MM"，不要替模型编出具体号数
    return "-".join([x for x in (ref_y, ev_m, ev_d) if x])


def _parse_coord(value, limit):
    """坐标只认合法数值；越界/非数字返回 None（下游靠 None 判断「不传坐标」）。"""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not (-limit <= v <= limit):
        return None
    return round(v, 6)


def _normalize_risk_event(ai_data: dict, reference_date: str = None) -> dict:
    """把 LLM 的 risk_event 字段规整成稳定 schema，并写回 ai_data。

    ``reference_date`` 为文章发布日期，用于校正模型猜错的年份：
    中文新闻惯用「11月5日」省略年份，实测模型会把 2026 的事件写成 2024。
    """
    risk = _empty_risk_event()
    raw = ai_data.get("risk_event") if isinstance(ai_data, dict) else None

    if isinstance(raw, list) and raw:
        raw = raw[0]
    if not isinstance(raw, dict):
        ai_data["risk_event"] = risk
        return risk

    flag = raw.get("is_risk_event")
    if isinstance(flag, str):
        flag = flag.strip().lower() in {"true", "yes", "y", "1", "是"}
    risk["is_risk_event"] = bool(flag)

    risk["event_type"] = _normalize_enum(
        raw.get("event_type"), _RISK_EVENT_TYPES, _RISK_EVENT_ALIASES)
    risk["hazard_class"] = _normalize_enum(
        raw.get("hazard_class"), _HAZARD_CLASSES, _HAZARD_ALIASES)

    risk["entity"] = _clean_str(raw.get("entity"))
    risk["site"] = _clean_str(raw.get("site"))
    risk["country_region"] = _clean_str(raw.get("country_region"))

    # 坐标：只接受合法数值；缺失/越界/占位 (0,0) 一律丢弃，绝不替模型补值
    lat = _parse_coord(raw.get("lat"), 90)
    lon = _parse_coord(raw.get("lon"), 180)
    if lat is not None and lon is not None and abs(lat) < 1e-6 and abs(lon) < 1e-6:
        lat = lon = None
    risk["lat"], risk["lon"] = lat, lon

    # severity：兼容 0-100 打分和 "0.7" 字符串；无法解析时给 0.0
    sev = raw.get("severity")
    try:
        if isinstance(sev, str):
            sev = sev.strip().rstrip("%")
        sev = float(sev)
        if sev > 1.0:
            sev = sev / 100.0 if sev <= 100.0 else 1.0
        sev = max(0.0, min(1.0, sev))
    except (TypeError, ValueError):
        sev = 0.0
    risk["severity"] = round(sev, 2)

    products = raw.get("affected_products")
    if isinstance(products, str):
        products = [p for p in products.replace("、", ",").split(",")]
    if not isinstance(products, list):
        products = []
    seen, cleaned = set(), []
    for p in products:
        p = _clean_str(p)
        if p and p not in seen:
            seen.add(p)
            cleaned.append(p[:40])
    risk["affected_products"] = cleaned[:6]

    occurred = _clean_str(raw.get("occurred_at"))
    if occurred:
        # 只保留规范形态，避免下游解析爆炸
        m = re.match(r"^(\d{4})-(\d{2})(?:-(\d{2}))?", occurred)
        occurred = "-".join([g for g in m.groups() if g]) if m else None
    risk["occurred_at"] = _fix_event_year(occurred, reference_date)

    ai_data["risk_event"] = risk
    return risk


def format_risk_event(risk: dict) -> str:
    """把风险事件压成一行日志，方便直接看抽取对不对。"""
    if not risk or not risk.get("is_risk_event"):
        return "风险事件：无"
    parts = [
        f"type={risk.get('event_type')}",
        f"hazard={risk.get('hazard_class')}",
        f"entity={risk.get('entity')}",
        f"site={risk.get('site')}",
        f"region={risk.get('country_region')}",
        f"severity={risk.get('severity')}",
    ]
    if risk.get("lat") is not None and risk.get("lon") is not None:
        parts.append(f"coord={risk['lat']},{risk['lon']}")
    if risk.get("affected_products"):
        parts.append("products=" + "/".join(risk["affected_products"]))
    if risk.get("occurred_at"):
        parts.append(f"at={risk['occurred_at']}")
    return "风险事件：" + " ".join(parts)


def append_risk_events(records: list, path: str = None) -> int:
    """把风险事件追加进 JSONL，供下游平台（SEISMIQ）按天拉取。

    只落 is_risk_event 为真的记录，文件按天分片：risk_events-YYYY-MM-DD.jsonl。
    返回实际写入条数。
    """
    if not records:
        return 0
    base = path or os.environ.get("RISK_EVENTS_DIR") or "risk_events"
    try:
        os.makedirs(base, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        fp = os.path.join(base, f"risk_events-{today}.jsonl")
        with open(fp, "a", encoding="utf-8") as f:
            for rec in records:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        print(f"风险事件已写入：{fp}（{len(records)} 条）")
        return len(records)
    except Exception as e:
        print(f"风险事件写入失败：{e}")
        return 0


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
        "imec-int.com": "imec",
        "qualcomm.com": "Qualcomm",
        "theinformation.com": "The Information",
    }

    # Broadcom 特殊处理：根据路径区分 News 和 Blog
    if domain == "broadcom.com" or domain == "www.broadcom.com":
        if "/blog" in path:
            return "Broadcom Blog"
        else:
            return "Broadcom"

    return domain_map.get(domain, domain.split(".")[0])


# ---------------------------------------------------------------------------
# HotorNot / 首页收录口径
# ---------------------------------------------------------------------------
# 部分 RSS 源会周期性推同一栏目（Seeking Alpha 的「政治与市场 11/8/26」即一例），
# 内容是宏观政治 / 市场评论，不是半导体产业新闻，不该占用首页。
# 判定同时供「新写入」和「历史批量修正」使用，避免两套口径漂移。
# Notion 数据库里已有的选项名是驼峰 noHome，写成 nohome 会凭空造出一个新选项
NOHOME_OPTION = "noHome"

# 整站口径：库里 kitguru(305 条游戏资讯) / electronicsforu(53 条招聘帖)
# 已被整体标为 noHome。固化到代码，避免以后新写入又回到首页。
NOHOME_WEBSITES = frozenset({"kitguru", "electronicsforu"})

# 日期 / 期号片段：把「政治与市场 11/8/26」和「政治与市场 10/26/26」归成同一系列
_DATE_RE = re.compile(
    r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"
    r"|\b(?:january|february|march|april|may|june|july|august|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}\b"
    r"|\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日"
    r"|\b(?:19|20)\d{2}\b",
    re.I,
)
# 周期性栏目的常见标记（单独不足以判定，还要 series_count >= 2 或显式栏目名）
_PERIODIC_RE = re.compile(
    r"\b(?:newsletter|digest|roundup|recap|briefing|snapshot|weekly|daily|monthly|wrap"
    r"|line-?up|issue|report|commentary|summary|review)\b"
    r"|通讯|杂志|猎头|简报|周报|日报|综述|总结|评论|回顾|快照|每周|每日|月度|最新一期|招聘",
    re.I,
)
# 显式栏目名：新写入时没有历史系列计数可用，靠这些识别已知的周期栏目。
# 以后发现新栏目，把栏目名加进来即可，不用改判定逻辑。
_NOHOME_COLUMN_PATTERNS = (
    re.compile(r"政治与市场|politics\s*(?:&|and|,|/|\+)\s*(?:the\s+)?markets?", re.I),
    re.compile(r"1[-\s]?minute\s+market\s+report|分钟市场(?:简报|报告)", re.I),
    re.compile(r"treasury\s+yields?\s+snapshot|国债收益率快照", re.I),
    re.compile(r"weekly\s+closed-?end\s+fund\s+roundup|每周封闭式基金综述", re.I),
    re.compile(r"valuengine|每周市场总结", re.I),
    re.compile(r"^(?:market|macro|politics)\s+(?:recap|brief|briefing|outlook|wrap|weekly|update|watch|review)", re.I),
    re.compile(r"(?:每周|每日|今日|周度)?(?:市场|宏观|政经)(?:观察|综述|评论|回顾|周报|日报|简报|速览|展望)"),
)
# 半导体产业主题词 / 标签 → 一律保留 Home，宁可少改也不错杀产业新闻。
# 英文用词边界，避免 "fabric" / "fabulous" 误命中 fab；中文直接子串匹配。
_SEMI_TOPIC_RE = re.compile(
    r"\b(?:chips?|semiconductors?|wafers?|foundry|foundries|fabs?|tsmc|nvidia|intel|amd"
    r"|dram|nand|hbm|euv|asics?|gpus?|cpus?|photonics?|eda|dsp|ssd|pcb|mocvd)\b"
    r"|芯片|半导体|晶圆|制程|光刻|封装|存储|代工|集成电路|显卡|光互连|光模块|闪存|内存"
    r"|固态硬盘|算力|湾芯|封测",
    re.I,
)
_SEMI_TAGS = frozenset({
    "semiconductor", "dram", "nand", "hbm", "3d ic", "gpu", "cpu", "ai chip",
    "chiplet", "euv", "emib", "eda", "hpc", "3nm", "2nm", "pcie", "gddr",
    "memory", "microchip", "infineon", "ti", "arm", "gan", "nvidia", "amd",
})


def title_series_key(title_en: str = "", title_cn: str = "") -> str:
    """把带日期 / 期号的周期栏目标题归一化成同一个「系列键」。

    "Politics And The Markets 11/8/26" 与 "Politics And The Markets 10/26/26"
    -> "politics and the markets"，据此可统计出「周期性重复」。
    """
    text = _DATE_RE.sub(" ", (title_en or "").lower())
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 4:  # 英文标题是韩文等非拉丁字符时，退回中文标题
        text = _DATE_RE.sub(" ", (title_cn or "").lower())
        text = re.sub(r"[^0-9\u4e00-\u9fff]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    return text


def is_nohome_article(
    title_en: str = "",
    title_cn: str = "",
    website_name: str = "",
    category: str = None,
    tags: list = None,
    series_count: int = 1,
) -> bool:
    """周期性重复的栏目型标题 + 非半导体内容 → 不上首页（HotorNot=noHome）。

    * 整站口径（kitguru / electronicsforu）：直接 noHome；
    * category == "semiconductor"、标题含半导体主题词或带半导体标签：保留 Home；
    * 命中已知周期栏目名：noHome；
    * 其余：同一源站里「系列键」出现 >= 2 次、且标题带日期/期号或栏目词，
      才算周期性栏目 → noHome。新写入时 series_count 缺省 1，只走前三条。
    """
    if str(website_name or "").strip().lower() in NOHOME_WEBSITES:
        return True
    text = f"{title_cn or ''} {title_en or ''}".lower()
    if (category == "semiconductor" or _SEMI_TOPIC_RE.search(text)
            or any(str(t).strip().lower() in _SEMI_TAGS for t in (tags or []))):
        return False
    if any(p.search(text) for p in _NOHOME_COLUMN_PATTERNS):
        return True
    raw = f"{title_cn or ''} {title_en or ''}"
    return series_count >= 2 and bool(_DATE_RE.search(raw) or _PERIODIC_RE.search(raw))


def resolve_hotornot(item: dict, ai_data: dict, website_name: str) -> str:
    """新写入 Notion 时决定 HotorNot 取值。"""
    return NOHOME_OPTION if is_nohome_article(
        title_en=item.get("title", ""),
        title_cn=ai_data.get("title_cn") or "",
        website_name=website_name,
        category=ai_data.get("category"),
        tags=ai_data.get("tags") or [],
    ) else "Home"


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
    # 清洗 LLM 标签：Notion multi_select 只接受非空字符串，且选项名有长度
    # 上限。旧版本这里有一行 tags = [] 把选好的标签全清空了，导致站点标签页、
    # 相关文章与标签云持续退化，不要再加回来。
    tags = [str(tag).strip()[:100] for tag in tags if str(tag).strip()]
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
            "select": {"name": resolve_hotornot(item, ai_data, website_name)},
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
    is_semiconductor: bool = False,
    is_chinese_original: bool = False,
) -> None:
    """在 Notion 上传成功后发送到 Telegram 和 Discord"""
    # Telegram 发送（先看原文语言，再看行业分类，路由到不同群组）
    chat_id, route_desc = resolve_telegram_chat_id(is_semiconductor, is_chinese_original)
    print(f"[Telegram] 路由：{route_desc}")
    if not chat_id:
        print("[Telegram] 跳过发送。")
    else:
        try:
            send_to_telegram(
                title_cn=title_cn,
                title_en=title_en,
                summary_cn=summary_cn,
                link=link,
                tags=tags,
                website_name=website_name,
                chat_id=chat_id,
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
    elif SCRAPER_CONFIG.get(source, {}).get("summary_only"):
        # 该爬虫源本身的条目没有独立正文页（如 usbdev 的更新日志表格），
        # 抓 link 只会把列表页整页当正文塞给模型，直接跳过
        fetch_content = False

    _ai_fail_streak = 0  # 连续调用模型失败计数（用于限流时提前收手）

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

        if fetch_content:
            print("正在抓取正文...")
            try:
                content = fetch_article_content(link)
            except Exception as e:
                print("抓取正文失败，将仅基于 RSS 摘要进行总结：", e)
                content = summary
        else:
            print("该源使用 RSS 摘要，跳过正文抓取。")
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

        # 主动限速：免费档模型都有 RPM 限制，多 provider 分摊后可略微加快
        time.sleep(float(os.environ.get("LLM_REQUEST_INTERVAL", "1")))

        try:
            data = summarize_with_ai(
                title, summary, content, model=model,
                published_date=item.get("published_date"),
            )
            used_model = data.pop("_llm_model", model)
            print(f"使用模型：{used_model}")
            _ai_fail_streak = 0
        except Exception as e:
            # 单篇失败不拖垮整个源，继续处理后面的文章
            _ai_fail_streak += 1
            print(f"AI 摘要失败（连续第 {_ai_fail_streak} 次），跳过该文章：{str(e)[:120]}")
            if _ai_fail_streak >= 5:
                print("连续 5 篇失败，判定为模型限流/故障，结束该源的处理。")
                break
            continue

        print("AI 结果 JSON：")
        # 使用 ensure_ascii=True 避免编码问题
        print(json.dumps(data, ensure_ascii=True, indent=2))

        # 风险事件落盘：下游（SEISMIQ）按天拉 JSONL 即可，不依赖 Notion 结构
        risk = data.get("risk_event") or {}
        print(format_risk_event(risk))
        if risk.get("is_risk_event"):
            # 同一事件常被多家媒体同时报道（如美光停建被两个源各抽一条），
            # 给下游一个软去重键：同主体+同类型+同日期视为同一事件簇。
            # 注意它不是权威 id，实体名写法不同（"美光"/"美光科技(Micron)"）会算出不同的键。
            dedup_src = f"{risk.get('entity')}|{risk.get('event_type')}|{risk.get('occurred_at')}"
            append_risk_events([{
                "post_id": post_id,
                "dedup_key": hashlib.sha1(dedup_src.encode("utf-8")).hexdigest()[:12],
                "source": source,
                "url": link,
                "title": title,
                "title_cn": data.get("title_cn"),
                "published_at": item.get("published_date"),
                "llm": used_model,
                "ingested_at": datetime.now().isoformat(timespec="seconds"),
                **risk,
            }])

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
                used_model,
                source_url,
            )

            # Notion 上传成功后发送到 Telegram
            semiconductor, category_raw, category_reason = classify_article_category(data)
            print(
                "行业分类："
                + ("半导体" if semiconductor else "其他（非半导体）")
                + f" | category={category_raw!r} | {category_reason}"
                + "，正在发送到 Telegram..."
            )
            send_to_telegram_after_notion(
                title_cn=data.get("title_cn", title),
                title_en=title,
                summary_cn=data.get("summary_cn", ""),
                link=link,
                tags=data.get("tags", []),
                website_name=get_website_name(source_url),
                is_semiconductor=semiconductor,
                # 原文语言只认抓取到的原始标题/正文/RSS 摘要，不能用模型生成的中文标题
                is_chinese_original=is_chinese_original_text(title, content, summary),
            )

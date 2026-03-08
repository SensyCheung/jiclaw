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


def fetch_article_content(url: str) -> str:
    """抓取网页正文内容，返回纯文本。"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0 Safari/537.36"
        )
    }

    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    html = resp.text

    soup = BeautifulSoup(html, "html.parser")

    # 针对 thelec.net 的特殊处理：获取 section#user-container 内容
    if "thelec.net" in url:
        node = soup.select_one("section#user-container")
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    # 针对 tomshardware.com 的特殊处理：获取 div#widgetArea16 内容
    if "tomshardware.com" in url:
        node = soup.select_one("div#widgetArea16")
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    # 常见正文容器，可按目标网站结构自行调整
    candidates = [
        "article",
        "main",
        "div#content",
        "div.post-content",
        "div.entry-content",
        "div.article-content",
    ]

    for selector in candidates:
        node = soup.select_one(selector)
        if node:
            text = node.get_text(separator="\n", strip=True)
            if len(text) > 200:
                return text

    # 兜底：直接取整个 body 文本
    body = soup.body.get_text(separator="\n", strip=True) if soup.body else ""
    return body


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
Proper Noun Translation:
"Silicon Motion" : "慧荣科技" //注意：不要什么都翻译成这个，不知道怎么翻译的就保留原始英文名不翻译。
"Silicon Catalyst" : "Silicon Catalyst"(不翻译)
"DeepCool" : "九州风神 DeepCool"
"Noctua" : "奥地利猫头鹰 Noctua"
"Microchip" : "Microchip"(不翻译)
"X-Epic" : "芯华章"
'Made by Google' : 'Made by Google'(不翻译)
'Lam Research' : '泛林集团'
'Agile Analog' : 'Agile Analog'(不翻译)
---
你将看到一篇科技/半导体/硬件等方向的文章信息（标题 + RSS 摘要 + 抓取到的正文）。
请基于完整正文内容完成以下任务，并严格只输出 JSON：

1. 提取 <3 reaction theme Tags，优先提取公司名称或技术关键词。
   Suggest Tags: ['Gaming', 'NVIDIA', 'SK Hynix', 'DRAM', '3D IC', 'GPU', 'CPU', 'AI', 'AI PC', 'HBM', 'NPU', 'SSD', 'Chiplet', 'DRAM', 'EUV', 'EMIB', 'EDA', 'HPC', 'AMD', 'Dell', 'Linux', '3nm', 'laptop', 'Raspberry Pi', 'Switch', 'PCIe', 'GDDR', '2nm', 'Semiconductor', 'TI', 'ARM', 'memory', 'Monitor', 'automotive', 'Laptop', 'Cybersecurity', 'Privacy', 'Microchip', 'Asus', 'Infineon', 'HPC', 'AI chip', 'Software', 'GaN', 'iOS', 'PCIe', 'Cooling']
   Prohibited tags: ['Reviews', 'Featured Tech News', 'Tech News', 'technology', 'Tech Industry', 'Hardware', 'semiconductor', 'Industry', 'electronics', 'Manufacturing', 'Sales']

2. 将英文标题翻译为简体中文（title_cn）。

3. 基于正文内容，生成"要点式"的摘要：
   - summary_en：用英文输出 2-4 个要点，使用 HTML 段落标签包装，
     例如："<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>"
   - summary_cn：用简体中文输出 2-4 个要点，格式同上。

只输出严格 JSON 格式，不要任何解释或额外文本：
{
  "tags": ["aaa", "bbb", "ccc"],
  "title_cn": "被翻译的中文标题",
  "summary_en": "<p>➀ xxx; </p><p>➁ xxx; </p><p>➂ xxx</p>",
  "summary_cn": "<p>➀ xxx；</p><p>➁ xxx；</p><p>➂ xxx</p>"
}
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

    if domain.startswith("www."):
        domain = domain[4:]

    domain_map = {
        "trendfore.com": "trendfore",
        "thelec.net": "thelec",
        "tomshardware.com": "tomshardware",
    }

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


def process_feed(feed_url: str, model: str = "glm-4-flash", limit: int = 10) -> None:
    """抓取单个 RSS 源的多篇文章并写入 Notion（如已配置）。"""
    # 编码安全输出
    print(f"\n====== 开始处理 RSS 源 ======")

    items = get_feed_items(feed_url, limit=limit)
    if not items:
        print("该 RSS 源没有可处理的条目。")
        return

    _process_items(items, feed_url, model)


def process_scraper(site_name: str, model: str = "glm-4-flash", limit: int = 10) -> None:
    """爬取无 RSS 源网站的多篇文章并写入 Notion（如已配置）。"""
    from scraper_config import SCRAPER_CONFIG

    config = SCRAPER_CONFIG.get(site_name)
    if not config:
        print(f"未找到网站配置：{site_name}")
        return

    # 编码安全输出
    print(f"\n====== 开始爬取网站 ======")

    items = scrape_site(site_name, limit=limit)
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

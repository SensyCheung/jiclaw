"""
jiclaw_scraper.py - 无 RSS 网站的爬虫模块
通过网页爬虫获取文章列表
"""

import re
import os
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from scraper_config import SCRAPER_CONFIG


# 代理配置：从环境变量读取，例如：HTTP_PROXY="http://127.0.0.1:7890"
HTTP_PROXY = os.environ.get("HTTP_PROXY") or os.environ.get("http_proxy")
HTTPS_PROXY = os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")

PROXIES = None
if HTTP_PROXY or HTTPS_PROXY:
    PROXIES = {
        "http": HTTP_PROXY,
        "https": HTTPS_PROXY,
    }


def get_proxies() -> dict:
    """获取代理配置"""
    return PROXIES


def normalize_date(date_str: str, date_format: str = None) -> str:
    """
    将各种日期格式转换为 ISO 8601 格式 (YYYY-MM-DDTHH:MM:SS.000Z)
    会自动处理时区转换
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # 如果是 ISO 8601 格式（如 2026-03-08T16:00:43+08:00），转换为 UTC
    if "T" in date_str:
        try:
            # 处理时区
            if "+" in date_str:
                # 分离日期时间和时区
                dt_part, tz_part = date_str.split("+")
                dt = datetime.fromisoformat(dt_part)
                # 解析时区偏移
                if ":" in tz_part:
                    tz_hours, tz_mins = map(int, tz_part.split(":"))
                else:
                    tz_hours = int(tz_part[:2])
                    tz_mins = int(tz_part[2:]) if len(tz_part) > 2 else 0
                # 转换为 UTC（减去时区偏移）
                from datetime import timedelta
                utc_dt = dt - timedelta(hours=tz_hours, minutes=tz_mins)
                return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            elif date_str.endswith("Z"):
                # 已经是 UTC
                dt = datetime.fromisoformat(date_str.replace("Z", ""))
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            else:
                # 没有时区信息，假设为 UTC
                dt = datetime.fromisoformat(date_str)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except Exception as e:
            print(f"日期解析失败：{date_str}, 错误：{e}")
            pass

    # 处理相对时间（如 "2h", "1m", "3d"）
    if date_str.endswith('h') or date_str.endswith('m') or date_str.endswith('d'):
        now = datetime.now()
        return now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # 尝试配置的格式（假设为北京时间）
    if date_format:
        try:
            dt = datetime.strptime(date_str, date_format)
            # 转换为 UTC（减去 8 小时）
            from datetime import timedelta
            utc_dt = dt - timedelta(hours=8)
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            pass

    # 尝试常见格式（假设为北京时间）
    formats = [
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y年%m月%d日",
    ]

    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            # 转换为 UTC（减去 8 小时）
            from datetime import timedelta
            utc_dt = dt - timedelta(hours=8)
            return utc_dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue

    # 如果都失败，返回 None
    return None


def scrape_semi_insights(url: str, limit: int = 10) -> list[dict]:
    """
    爬取半导体产业观察 (semi-insights.com) 的文章列表
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    results = []

    try:
        # 禁用系统默认代理，使用自定义代理配置
        response = requests.get(
            url,
            headers=headers,
            timeout=10,
            proxies={"http": None, "https": None}  # 禁用代理直连
        )
        response.encoding = "utf-8"

        if response.status_code != 200:
            print(f"请求失败，状态码：{response.status_code}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # 定位到包含新闻列表的 ul
        news_list = soup.select("ul.info-news-c li.clearfix.bor")
        
        if not news_list:
            print("未找到新闻列表，尝试其他选择器...")
            # 备用选择器
            news_list = soup.select("ul.info-news-c li")

        for item in news_list[:limit]:
            # 提取 Title 和 URL (在 h5 标签下的 a 标签)
            title_tag = item.select_one("h5.h5 a")
            if title_tag:
                title = title_tag.get_text(strip=True)
                link = title_tag.get("href")

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取发布时间 (尝试多种选择器)
                date_str = ""
                for selector in ["span.date", ".date", ".time", "span.time"]:
                    date_tag = item.select_one(selector)
                    if date_tag:
                        date_str = date_tag.get_text(strip=True)
                        break
                
                # 从配置获取日期格式
                date_format = "%Y.%m.%d"  # semi-insights 的日期格式
                published_date = normalize_date(date_str, date_format)

                results.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": "",  # 爬虫初始无摘要
                        "published": date_str,
                        "published_date": published_date,
                    }
                )

        return results

    except Exception as e:
        print(f"爬取 semi-insights 失败：{e}")
        return []


def scrape_icsmart(url: str, limit: int = 10) -> list[dict]:
    """
    爬取半导体行业观察 (icsmart.cn) 的文章列表
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }

    results = []

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=10,
            proxies={"http": None, "https": None}
        )
        response.encoding = "utf-8"

        if response.status_code != 200:
            print(f"请求失败，状态码：{response.status_code}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # 查找文章列表：div.entries -> article
        articles = soup.select("div.entries article")

        for article in articles[:limit]:
            # 提取标题和链接：h2.entry-title -> a
            title_tag = article.select_one("h2.entry-title a")
            if title_tag:
                title = title_tag.get_text(strip=True)
                link = title_tag.get("href")

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取日期：time.ct-meta-element-date datetime 属性
                date_tag = article.select_one("time.ct-meta-element-date")
                date_str = ""
                if date_tag:
                    # 优先使用 datetime 属性
                    date_str = date_tag.get("datetime", "")
                    if not date_str:
                        # 使用文本内容
                        date_str = date_tag.get_text(strip=True)

                # 标准化日期
                published_date = normalize_date(date_str, "%Y年%m月%d日")

                results.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": "",
                        "published": date_str,
                        "published_date": published_date,
                    }
                )

        return results

    except Exception as e:
        print(f"爬取 icsmart 失败：{e}")
        return []


# 爬虫函数映射表
SCRAPER_FUNCTIONS = {
    "semi-insights": scrape_semi_insights,
    "icsmart": scrape_icsmart,
}


def scrape_site(site_name: str, limit: int = 10) -> list[dict]:
    """
    根据网站名称调用对应的爬虫函数

    Args:
        site_name: 网站名称（如 "semi-insights"）
        limit: 获取文章数量上限

    Returns:
        文章列表，每项包含 title, link, summary, published_date
    """
    if site_name not in SCRAPER_CONFIG:
        print(f"未找到网站配置：{site_name}")
        return []

    config = SCRAPER_CONFIG[site_name]
    url = config["url"]

    print(f"开始爬取网站：{config['name']} ({url})")

    # 调用对应的爬虫函数
    scraper_func = SCRAPER_FUNCTIONS.get(site_name)
    if scraper_func:
        return scraper_func(url, limit)

    # 如果没有专用爬虫，返回空列表
    print(f"未找到 {site_name} 的爬虫实现")
    return []


def get_available_sites() -> list[str]:
    """获取所有可用的爬虫网站名称"""
    return list(SCRAPER_CONFIG.keys())

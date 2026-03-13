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

    注意：
    - 在本地运行时（北京时间），需要减去 8 小时转换为 UTC
    - 在 GitHub Action 中运行时（已是 UTC），不需要转换
    - 通过 TIMEZONE_OFFSET 环境变量控制（小时数），默认为 0（UTC）
    - 本地运行时设置 TIMEZONE_OFFSET=8
    - 如果日期只有日期部分（没有时分秒），则使用当前抓取时间的时分秒
    """
    if not date_str:
        return None

    date_str = date_str.strip()

    # 获取时区偏移（小时数），默认为 0（UTC）
    # 本地运行时设置 TIMEZONE_OFFSET=8（北京时间）
    timezone_offset = int(os.environ.get("TIMEZONE_OFFSET", "0"))

    # 获取当前时间（用于补充时分秒）
    now = datetime.now()

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

    # 处理相对时间（如 "3 分钟前", "2 小时前", "1 天前"）
    if "分钟前" in date_str or "小时前" in date_str or "天前" in date_str or "秒前" in date_str:
        from datetime import timedelta

        if "分钟前" in date_str:
            try:
                minutes = int(date_str.replace("分钟前", "").strip())
                dt = now - timedelta(minutes=minutes)
                # 如果设置了时区偏移，转换为 UTC
                if timezone_offset > 0:
                    dt = dt - timedelta(hours=timezone_offset)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except:
                pass
        elif "小时前" in date_str:
            try:
                hours = int(date_str.replace("小时前", "").strip())
                dt = now - timedelta(hours=hours)
                # 如果设置了时区偏移，转换为 UTC
                if timezone_offset > 0:
                    dt = dt - timedelta(hours=timezone_offset)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except:
                pass
        elif "天前" in date_str:
            try:
                days = int(date_str.replace("天前", "").strip())
                dt = now - timedelta(days=days)
                # 如果设置了时区偏移，转换为 UTC
                if timezone_offset > 0:
                    dt = dt - timedelta(hours=timezone_offset)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except:
                pass
        elif "秒前" in date_str:
            try:
                seconds = int(date_str.replace("秒前", "").strip())
                dt = now - timedelta(seconds=seconds)
                # 如果设置了时区偏移，转换为 UTC
                if timezone_offset > 0:
                    dt = dt - timedelta(hours=timezone_offset)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except:
                pass

        # 默认返回当前时间（根据时区偏移调整）
        if timezone_offset > 0:
            return (now - timedelta(hours=timezone_offset)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        return now.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    # 尝试配置的格式
    if date_format:
        try:
            dt = datetime.strptime(date_str, date_format)
            # 如果只有日期部分（没有时分秒），使用当前时间的时分秒
            if date_format.count('H') == 0:  # 格式中没有时分秒
                dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second)
            # 如果设置了时区偏移，转换为 UTC
            if timezone_offset > 0:
                from datetime import timedelta
                dt = dt - timedelta(hours=timezone_offset)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            pass

        # 尝试备用格式（英文月份缩写）
        alternate_formats = [
            "%b %d, %Y",  # Mar 10, 2026
            "%B %d, %Y",  # March 10, 2026
            "%d %B, %Y",  # 10 March, 2026
            "%d %b, %Y",  # 10 Mar, 2026
        ]
        for alt_fmt in alternate_formats:
            try:
                dt = datetime.strptime(date_str, alt_fmt)
                # 如果只有日期部分（没有时分秒），使用当前时间的时分秒
                if alt_fmt.count('H') == 0:
                    dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second)
                if timezone_offset > 0:
                    from datetime import timedelta
                    dt = dt - timedelta(hours=timezone_offset)
                return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
            except ValueError:
                continue

    # 尝试常见格式
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
            # 如果只有日期部分（没有时分秒），使用当前时间的时分秒
            if fmt.count('H') == 0:
                dt = dt.replace(hour=now.hour, minute=now.minute, second=now.second)
            # 如果设置了时区偏移，转换为 UTC
            if timezone_offset > 0:
                from datetime import timedelta
                dt = dt - timedelta(hours=timezone_offset)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
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


def scrape_lumentum(url: str, limit: int = 10, use_proxy: bool = False) -> list[dict]:
    """
    爬取 Lumentum (lumentum.com/en/newsroom/news-releases) 的文章列表
    使用 Playwright 处理 JavaScript 动态加载内容
    
    Args:
        url: 网站 URL
        limit: 获取文章数量上限
        use_proxy: 是否使用代理（默认 False，Lumentum 不需要代理）
    """
    from playwright.sync_api import sync_playwright
    
    results = []

    try:
        with sync_playwright() as p:
            # 启动浏览器
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )
            
            # Lumentum 不需要代理，明确禁用
            page = browser.new_page()
            page.set_viewport_size({"width": 1920, "height": 1080})
            
            # 隐藏 WebDriver 特征
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)
            
            print(f"  正在访问 {url}...")
            # 使用较宽松的加载策略
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            
            # 等待动态内容加载
            print("  等待动态内容加载...")
            page.wait_for_timeout(8000)
            
            # 尝试滚动页面触发更多内容加载
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)
            except:
                pass
            
            # 获取页面 HTML
            html_content = page.content()
            browser.close()
        
        print(f"  页面大小：{len(html_content)} 字节")
        
        soup = BeautifulSoup(html_content, "html.parser")
        
        # 调试：查找所有 class
        all_classes = set()
        for elem in soup.find_all(class_=True):
            for cls in elem['class']:
                all_classes.add(cls)
        
        # 查找可能包含新闻的 class
        news_classes = [c for c in all_classes if any(k in c.lower() for k in ['news', 'press', 'release', 'article', 'item', 'card'])]
        print(f"  找到的新闻相关 class: {news_classes[:20]}")
        
        # 查找文章列表 - 尝试多种选择器
        articles = []
        selectors = [
            "div.news-item",
            "article.news",
            "div.press-release",
            "li.news-item",
            "div.listing-item",
            "div.item",
            "div.card",
            "a[href*='/news/']",
        ]
        
        for selector in selectors:
            articles = soup.select(selector)
            if articles:
                print(f"  使用选择器 {selector} 找到 {len(articles)} 篇文章")
                break
        
        if not articles:
            print("  articles 为空，使用备用方案...")
            # 尝试查找所有包含新闻链接的元素
            news_links = soup.select("a[href*='/news/'], a[href*='/press/']")
            if news_links:
                print(f"  找到 {len(news_links)} 个新闻链接")
                for link in news_links[:limit]:
                    title = link.get_text(strip=True)
                    href = link.get("href", "")
                    if href:
                        href = urljoin(url, href)
                    
                    # 查找日期（向上查找父元素或兄弟元素）
                    date_str = ""
                    published_date = None
                    parent = link.find_parent()
                    
                    # 尝试在父元素、祖父元素中查找
                    for ancestor in [parent, parent.find_parent() if parent else None]:
                        if ancestor:
                            for selector in ["span.eyebrow", ".eyebrow", "time", ".date", "span.text-black"]:
                                date_tag = ancestor.select_one(selector)
                                if date_tag:
                                    date_str = date_tag.get_text(strip=True)
                                    # 检查是否是日期格式（包含月份名称）
                                    import re
                                    if re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)', date_str, re.IGNORECASE):
                                        print(f"    链接：{title[:30]}... | 日期：{date_str}")
                                        published_date = normalize_date(date_str, "%B %d, %Y")
                                        break
                            if date_str:
                                break
                    
                    if not date_str:
                        # 尝试从标题中提取日期
                        import re
                        date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}', title, re.IGNORECASE)
                        if date_match:
                            date_str = date_match.group()
                            print(f"    从标题提取日期：{date_str}")
                            published_date = normalize_date(date_str, "%B %d, %Y")
                            print(f"    转换后：{published_date}")
                            # 从标题中移除日期部分
                            title = re.sub(date_match.group(), '', title).strip()
                        else:
                            print(f"    链接：{title[:50]}... | 日期：未找到 (标题中未匹配到日期)")
                    else:
                        print(f"    链接：{title[:50]}... | 日期：{date_str} -> {published_date}")
                    
                    if title and len(title) > 5:
                        results.append({
                            "title": title[:100],
                            "link": href,
                            "summary": "",
                            "published": date_str,
                            "published_date": published_date,
                        })
                if results:
                    print(f"  成功获取 {len(results)} 篇文章")
                    return results
            
            # 备用方案：查找所有链接，过滤出新闻
            print("  尝试查找所有链接...")
            all_links = soup.select("a[href]")
            for link in all_links:
                href = link.get("href", "")
                title = link.get_text(strip=True)

                # 过滤新闻链接
                if '/news/' in href or '/press/' in href or 'news' in title.lower() or 'press' in title.lower():
                    if href:
                        href = urljoin(url, href)
                    
                    # 查找日期
                    date_str = ""
                    published_date = None
                    
                    # 尝试从标题中提取日期
                    import re
                    date_match = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}', title, re.IGNORECASE)
                    if date_match:
                        date_str = date_match.group()
                        print(f"    从标题提取日期：{date_str}")
                        published_date = normalize_date(date_str, "%B %d, %Y")
                        print(f"    转换后：{published_date}")
                        # 从标题中移除日期部分
                        title = re.sub(date_match.group(), '', title).strip()
                    
                    if title and len(title) > 5:
                        results.append({
                            "title": title[:100],
                            "link": href,
                            "summary": "",
                            "published": date_str,
                            "published_date": published_date,
                        })
            
            if results:
                print(f"  成功获取 {len(results)} 篇文章")
                return results
            
            print("  未找到文章")
            return []

        for article in articles[:limit]:
            print(f"  处理文章容器...")
            # 提取标题和链接
            title_tag = article.select_one("a")
            if title_tag:
                title = title_tag.get_text(strip=True)
                link = title_tag.get("href")

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取日期
                date_tag = article.select_one("time")
                if not date_tag:
                    date_tag = article.select_one(".date, .publish-date, span.date")
                if not date_tag:
                    # Lumentum 的日期格式：<span class="text-black block mb-6 mt-4 eyebrow">March 10, 2026</span>
                    date_tag = article.select_one("span.eyebrow")
                if not date_tag:
                    # 尝试在父元素或子元素中查找
                    date_tag = article.select_one(".eyebrow")
                if not date_tag:
                    # 尝试在整个 article 中查找
                    date_tag = article.select_one("span.text-black")

                date_str = ""
                if date_tag:
                    date_str = date_tag.get_text(strip=True)
                    print(f"    找到日期：{date_str}")
                    
                    # 尝试多种格式解析
                    published_date = normalize_date(date_str, "%B %d, %Y")
                    print(f"    转换后：{published_date}")
                else:
                    print(f"    未找到日期")

                if title and len(title) > 5:
                    results.append(
                        {
                            "title": title[:100],
                            "link": link,
                            "summary": "",
                            "published": date_str,
                            "published_date": published_date,
                        }
                    )

        print(f"  成功获取 {len(results)} 篇文章")
        return results

    except Exception as e:
        print(f"爬取 Lumentum 失败：{e}")
        return []


def scrape_lam_research(url: str, limit: int = 10, use_proxy: bool = False) -> list[dict]:
    """
    爬取 Lam Research (newsroom.lamresearch.com/blog) 的文章列表
    结构：ul.wd_list -> li.wd_item
    - 标题和链接：div.wd_title -> a
    - 发布时间：div.wd_date
    
    Args:
        url: 网站 URL
        limit: 获取文章数量上限
        use_proxy: 是否使用代理（默认 False）
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
        session = requests.Session()
        
        # 根据 use_proxy 参数决定是否使用代理
        if use_proxy:
            import urllib.request
            proxies = urllib.request.getproxies()
            if proxies.get('http') or proxies.get('https'):
                print(f"  使用系统代理：{proxies.get('https') or proxies.get('http')}")
                session.proxies.update(proxies)
            else:
                print("  未检测到系统代理")
        else:
            # 不使用代理，直接连接
            session.proxies = {'http': '', 'https': ''}
            print("  不使用代理，直接连接")
        
        print(f"  正在访问 {url}...")
        response = session.get(
            url,
            headers=headers,
            timeout=60
        )
        response.encoding = "utf-8"
        
        print(f"  响应状态码：{response.status_code}")
        print(f"  响应大小：{len(response.text)} 字节")

        if response.status_code != 200:
            print(f"请求失败，状态码：{response.status_code}")
            return []

        soup = BeautifulSoup(response.text, "html.parser")

        # 查找文章列表：ul.wd_item_list -> li.wd_item
        articles = soup.select("ul.wd_item_list li.wd_item")
        print(f"  找到 {len(articles)} 篇文章")

        for article in articles[:limit]:
            # 提取标题和链接：div.wd_title -> a
            title_tag = article.select_one("div.wd_title a")
            if title_tag:
                title = title_tag.get_text(strip=True)
                link = title_tag.get("href")

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取日期：div.wd_date
                date_tag = article.select_one("div.wd_date")
                date_str = ""
                if date_tag:
                    date_str = date_tag.get_text(strip=True)
                    print(f"  原始日期：{date_str}")

                # 标准化日期（英文月份格式：March 09, 2026）
                published_date = normalize_date(date_str, "%B %d, %Y")
                print(f"  转换后日期：{published_date}")

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
        print(f"爬取 Lam Research 失败：{e}")
        print("  提示：如果访问超时，请设置代理环境变量:")
        print("  set HTTP_PROXY=http://你的代理 IP:端口")
        print("  set HTTPS_PROXY=http://你的代理 IP:端口")
        return []


def scrape_aijiwei(url: str, limit: int = 10) -> list[dict]:
    """
    爬取爱集微 (laoyaoba.com/jwnews) 的文章列表
    内容在 div id=news-list 中的 li.card 元素
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

        # 查找新闻列表：div#news-list
        news_list = soup.find("div", id="news-list")
        if not news_list:
            print("未找到 news-list 容器")
            return []

        # 查找新闻项：li.card
        news_items = news_list.select("li.card")

        for item in news_items[:limit]:
            # 提取链接：data-href 属性
            link = item.get("data-href", "")
            if link:
                link = urljoin(url, link)
            
            # 提取标题：p.title
            title_tag = item.select_one("p.title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            # 提取时间：div.time
            time_tag = item.select_one("div.time")
            date_str = ""
            if time_tag:
                date_str = time_tag.get_text(strip=True)
            
            # 标准化日期（处理相对时间）
            published_date = normalize_date(date_str)

            if title and link:
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
        print(f"爬取爱集微失败：{e}")
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


def scrape_nvidia(url: str, limit: int = 10) -> list[dict]:
    """
    爬取 Nvidia News (nvidianews.nvidia.com) 的文章列表
    结构：div.tiles -> article.tiles-item
    - 标题和链接：h3.tiles-item-text-title -> a
    - 发布时间：div.tiles-item-text-date

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
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

        # 查找文章列表：div.tiles -> article.tiles-item
        articles = soup.select("div.tiles article.tiles-item")

        for article in articles[:limit]:
            # 提取标题和链接：h3.tiles-item-text-title -> a
            title_tag = article.select_one("h3.tiles-item-text-title a")
            if title_tag:
                title = title_tag.get_text(strip=True)
                link = title_tag.get("href")

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取日期：div.tiles-item-text-date
                date_tag = article.select_one("div.tiles-item-text-date")
                date_str = ""
                if date_tag:
                    date_str = date_tag.get_text(strip=True)

                # 标准化日期（英文月份格式：March 12, 2026）
                published_date = normalize_date(date_str, "%B %d, %Y")

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
        print(f"爬取 Nvidia 失败：{e}")
        return []


def scrape_nvidia_dev(url: str, limit: int = 10) -> list[dict]:
    """
    爬取 Nvidia Developer Blog (developer.nvidia.com/blog) 的文章列表
    结构：div.carousel-row__slide.js-post-card
    - 标题和链接：h3.carousel-row-slide__heading -> a 或父元素中的链接
    - 发布时间：span.post-published-date

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
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

        # 查找文章列表：div.carousel-row__slide.js-post-card
        articles = soup.select("div.carousel-row__slide.js-post-card")

        for article in articles[:limit]:
            # 提取标题和链接
            # 链接在 a.carousel-row-slide__link 中，标题在 h3.carousel-row-slide__heading 中
            link_tag = article.select_one("a.carousel-row-slide__link")
            title_tag = article.select_one("h3.carousel-row-slide__heading")

            if link_tag and title_tag:
                link = link_tag.get("href")
                title = title_tag.get_text(strip=True)

                # 处理相对链接
                if link:
                    link = urljoin(url, link)

                # 提取日期：span.post-published-date
                date_tag = article.select_one("span.post-published-date")
                date_str = ""
                if date_tag:
                    date_str = date_tag.get_text(strip=True)

                # 标准化日期（英文月份缩写格式：Mar 12, 2026）
                published_date = normalize_date(date_str, "%b %d, %Y")

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
        print(f"爬取 Nvidia Dev 失败：{e}")
        return []


def scrape_intel(url: str, limit: int = 10) -> list[dict]:
    """
    爬取 Intel Newsroom (newsroom.intel.com) 的文章列表
    结构：div.post-result-item-container
    - 标题：h2
    - 链接：a.post-result-item
    - 发布时间：p.item-post-date

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
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

        # 查找文章列表：div.post-result-item-container
        articles = soup.select("div.post-result-item-container")

        for article in articles[:limit]:
            # 提取标题：h2
            title_tag = article.select_one("h2")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # 提取链接：a.post-result-item
            link_tag = article.select_one("a.post-result-item")
            link = link_tag.get("href") if link_tag else ""

            # 处理相对链接
            if link:
                link = urljoin(url, link)

            # 提取日期：p.item-post-date
            date_tag = article.select_one("p.item-post-date")
            date_str = ""
            if date_tag:
                date_str = date_tag.get_text(strip=True)

            # 标准化日期（英文月份格式：March 12, 2026）
            published_date = normalize_date(date_str, "%B %d, %Y")

            if title and link:
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
        print(f"爬取 Intel 失败：{e}")
        return []


def scrape_coherent(url: str, limit: int = 10) -> list[dict]:
    """
    爬取 Coherent Press Releases (coherent.com/news/press-releases) 的文章列表
    使用 Playwright 处理 JavaScript 动态加载内容
    结构：li.ais-InfiniteHits-item
    - 标题：h3
    - 链接：a (包裹整个卡片)
    - 发布时间：p (日期格式：03/12/2026)

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
    """
    from playwright.sync_api import sync_playwright

    results = []

    try:
        with sync_playwright() as p:
            # 启动浏览器
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            page = browser.new_page()
            page.set_viewport_size({"width": 1920, "height": 1080})

            # 隐藏 WebDriver 特征
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            print(f"  正在访问 {url}...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # 等待动态内容加载
            print("  等待动态内容加载...")
            page.wait_for_timeout(8000)

            # 尝试滚动页面触发更多内容加载
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)
            except:
                pass

            # 获取页面 HTML
            html_content = page.content()
            browser.close()

        print(f"  页面大小：{len(html_content)} 字节")

        soup = BeautifulSoup(html_content, "html.parser")

        # 查找文章列表：li.ais-InfiniteHits-item
        articles = soup.select("li.ais-InfiniteHits-item")
        print(f"  找到 {len(articles)} 篇文章")

        for article in articles[:limit]:
            # 提取链接：a 标签
            link_tag = article.select_one("a")
            if not link_tag:
                continue

            link = link_tag.get("href", "")
            if link:
                link = urljoin(url, link)

            # 提取标题：h3
            title_tag = article.select_one("h3")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # 提取日期：p 标签 (第一个 p 标签通常是日期)
            # 在 card__content  div 中的第一个 p
            date_tag = article.select_one("div.card__content > p")
            date_str = ""
            if date_tag:
                date_str = date_tag.get_text(strip=True)

            # 标准化日期（格式：03/12/2026）
            published_date = normalize_date(date_str, "%m/%d/%Y")

            if title and link:
                results.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": "",
                        "published": date_str,
                        "published_date": published_date,
                    }
                )

        print(f"  成功获取 {len(results)} 篇文章")
        return results

    except Exception as e:
        print(f"爬取 Coherent 失败：{e}")
        return []


def scrape_iccsz(url: str, limit: int = 10) -> list[dict]:
    """
    爬取讯石光通讯 (iccsz.com) 的文章列表
    结构：ul.main_list li
    - 标题和链接：a
    - 发布时间：span.news_date (日期格式：(2026-03-13))

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
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

        # 查找文章列表：ul.main_list li
        articles = soup.select("ul.main_list li")

        for article in articles[:limit]:
            # 提取标题和链接：a
            title_tag = article.select_one("a")
            if not title_tag:
                continue

            title = title_tag.get_text(strip=True)
            link = title_tag.get("href", "")

            # 处理相对链接
            if link:
                link = urljoin(url, link)

            # 提取日期：span.news_date
            date_tag = article.select_one("span.news_date")
            date_str = ""
            if date_tag:
                date_str = date_tag.get_text(strip=True)
                # 去除括号，例如：(2026-03-13) -> 2026-03-13
                date_str = date_str.strip("() （）")

            # 标准化日期（格式：2026-03-13）
            published_date = normalize_date(date_str, "%Y-%m-%d")

            if title and link:
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
        print(f"爬取讯石光通讯失败：{e}")
        return []


def scrape_broadcom(url: str, limit: int = 10) -> list[dict]:
    """
    爬取 Broadcom News (broadcom.com/company/news) 的文章列表
    使用 Playwright 处理 JavaScript 动态加载内容
    结构：li
    - 标题和链接：a.lnk
    - 发布时间：span.news-date (日期格式：03/12/2026)

    Args:
        url: 网站 URL
        limit: 获取文章数量上限
    """
    from playwright.sync_api import sync_playwright

    results = []

    try:
        with sync_playwright() as p:
            # 启动浏览器
            browser = p.chromium.launch(
                headless=True,
                args=['--disable-blink-features=AutomationControlled', '--no-sandbox']
            )

            page = browser.new_page()
            page.set_viewport_size({"width": 1920, "height": 1080})

            # 隐藏 WebDriver 特征
            page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            """)

            print(f"  正在访问 {url}...")
            page.goto(url, wait_until="domcontentloaded", timeout=60000)

            # 等待动态内容加载
            print("  等待动态内容加载...")
            page.wait_for_timeout(8000)

            # 尝试滚动页面触发更多内容加载
            try:
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                page.wait_for_timeout(3000)
                page.evaluate("window.scrollTo(0, 0)")
                page.wait_for_timeout(2000)
            except:
                pass

            # 获取页面 HTML
            html_content = page.content()
            browser.close()

        print(f"  页面大小：{len(html_content)} 字节")

        soup = BeautifulSoup(html_content, "html.parser")

        # 查找文章列表：ul.news-list > li
        news_list = soup.select_one("ul.news-list")
        if not news_list:
            print("  未找到 ul.news-list")
            return []

        articles = news_list.select("li")
        print(f"  找到 {len(articles)} 篇新闻文章")

        for article in articles[:limit]:
            # 提取链接和标题：a.lnk
            link_tag = article.select_one("a.lnk")
            if not link_tag:
                continue

            link = link_tag.get("href", "")
            title = link_tag.get_text(strip=True)

            # 处理相对链接
            if link:
                link = urljoin(url, link)

            # 提取日期：span.news-date
            date_tag = article.select_one("span.news-date")
            date_str = ""
            if date_tag:
                date_str = date_tag.get_text(strip=True)

            # 标准化日期（格式：03/12/2026）
            published_date = normalize_date(date_str, "%m/%d/%Y")

            if title and link:
                results.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": "",
                        "published": date_str,
                        "published_date": published_date,
                    }
                )

        print(f"  成功获取 {len(results)} 篇文章")
        return results

    except Exception as e:
        print(f"爬取 Broadcom 失败：{e}")
        return []


# 爬虫函数映射表
SCRAPER_FUNCTIONS = {
    "semi-insights": scrape_semi_insights,
    "icsmart": scrape_icsmart,
    "aijiwei": scrape_aijiwei,
    "lam-research": scrape_lam_research,
    "lumentum": scrape_lumentum,
    "nvidia": scrape_nvidia,
    "nvidia-dev": scrape_nvidia_dev,
    "intel": scrape_intel,
    "coherent": scrape_coherent,
    "iccsz": scrape_iccsz,
    "broadcom": scrape_broadcom,
}


def scrape_site(site_name: str, limit: int = 10, use_proxy: bool = False) -> list[dict]:
    """
    根据网站名称调用对应的爬虫函数

    Args:
        site_name: 网站名称（如 "semi-insights"）
        limit: 获取文章数量上限
        use_proxy: 是否使用代理（默认 False）

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
        # 只有 lam-research 需要代理，lumentum 不需要
        if site_name == "lam-research":
            return scraper_func(url, limit, use_proxy=use_proxy)
        elif site_name == "lumentum":
            return scraper_func(url, limit, use_proxy=False)  # Lumentum 不使用代理
        else:
            return scraper_func(url, limit)

    # 如果没有专用爬虫，返回空列表
    print(f"未找到 {site_name} 的爬虫实现")
    return []


def get_available_sites() -> list[str]:
    """获取所有可用的爬虫网站名称"""
    return list(SCRAPER_CONFIG.keys())

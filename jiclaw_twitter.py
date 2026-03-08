"""
jiclaw_twitter.py - Twitter/X 爬虫模块
通过 twstalker.com 抓取 Twitter 推文

使用 Playwright + playwright-stealth 模拟真实浏览器访问
只获取作者发布的推文，不获取回复/评论
"""

import os
import random
import time
import tempfile
import shutil
from datetime import datetime

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from playwright_stealth import Stealth

from twitter_config import TWITTER_ACCOUNTS


# 浏览器请求头
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
]


def normalize_twitter_date(date_str: str) -> str:
    """将 Twitter 日期格式转换为 ISO 8601 格式"""
    if not date_str:
        return None
    date_str = date_str.strip()
    if date_str.endswith('h') or date_str.endswith('m') or date_str.endswith('d'):
        now = datetime.now()
        return now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    formats = ["%b %d, %Y · %I:%M %p %Z", "%b %d, %Y · %I:%M %p UTC", "%b %d, %Y"]
    for fmt in formats:
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        except ValueError:
            continue
    return None


def fetch_twitter_tweets(username: str, limit: int = 5) -> list[dict]:
    """通过 twstalker.com 抓取 Twitter 推文"""
    if username not in TWITTER_ACCOUNTS:
        print("未找到 Twitter 账号配置")
        return []

    print(f"开始抓取 Twitter: @{username}")

    # 使用 Playwright 抓取网页版
    try:
        results = fetch_from_webpage_playwright(username, limit)
    except Exception as e:
        print(f"  抓取失败：{str(e).encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')[:200]}")
        results = []
    
    if results:
        return results

    # 失败时返回占位结果
    return [{
        "title": f"@{username} 的 Twitter 动态",
        "link": f"https://twitter.com/{username}",
        "summary": f"查看 @{username} 的最新推文",
        "published": "recent",
        "published_date": datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }]


def fetch_from_webpage_playwright(username: str, limit: int = 5) -> list[dict]:
    """使用 Playwright + stealth 从 twstalker.com 抓取推文"""
    results = []
    
    # 使用临时用户数据目录
    user_data_dir = tempfile.mkdtemp(prefix="pw_")
    print(f"  使用临时用户数据目录")
    
    try:
        with sync_playwright() as p:
            try:
                # 启动浏览器
                browser = p.chromium.launch_persistent_context(
                    user_data_dir=user_data_dir,
                    headless=True,
                    user_agent=random.choice(USER_AGENTS),
                    viewport={"width": 1920, "height": 1080},
                    args=[
                        '--disable-blink-features=AutomationControlled',
                        '--no-sandbox',
                        '--disable-dev-shm-usage',
                        '--disable-gpu',
                    ],
                    ignore_default_args=['--enable-automation'],
                )
                
                page = browser.pages[0] if browser.pages else browser.new_page()
                
                # 设置视口大小
                page.set_viewport_size({"width": 1920, "height": 1080})
                
                # 使用 stealth 隐藏自动化特征
                stealth = Stealth()
                stealth.apply_stealth_sync(page)
                
                # 额外的反检测脚本
                page.add_init_script("""
                    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
                    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'en-US', 'en']});
                    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
                    Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
                    delete navigator.__proto__.webdriver;
                """)
                
                url = f"https://twstalker.com/{username}"
                print(f"  正在访问 {url}...")
                
                # 访问页面
                page.goto(url, wait_until="commit", timeout=30000)
                
                # 等待 Cloudflare 验证通过
                print("  等待 Cloudflare 验证...")
                max_wait = 30
                for i in range(max_wait):
                    time.sleep(2)
                    try:
                        title = page.title()
                        if title and "Just a moment" not in title and "Checking" not in title:
                            print(f"  Cloudflare 验证通过")
                            break
                        if i % 5 == 0:
                            print(f"  等待中... ({i*2}s)")
                        # 模拟人类行为：轻微滚动
                        if i % 3 == 0:
                            page.evaluate("window.scrollBy(0, 100)")
                    except Exception as e:
                        if i % 5 == 0:
                            print(f"  等待中... ({i*2}s)")
                
                # 等待页面加载
                print("  等待页面加载...")
                time.sleep(5)
                
                # 获取页面 HTML 内容
                html_content = page.content()
                
                # 解析推文
                tweets = parse_twstalker_tweets(html_content, username, limit)
                
                if tweets:
                    print(f"  成功获取 {len(tweets)} 条推文 (Playwright)")
                    results = tweets
                
                browser.close()
                
            except Exception as e:
                print(f"  Playwright 抓取失败：{e}")
    
    finally:
        # 清理临时目录
        try:
            shutil.rmtree(user_data_dir, ignore_errors=True)
        except:
            pass
    
    return results


def parse_twstalker_tweets(html_content: str, username: str, limit: int = 5) -> list[dict]:
    """
    解析 twstalker.com 页面的推文
    结构：
    - div#nav-tabContent
      - div.activity-posts
        - div.activity-group1 -> div.main-user-dts1 -> div.user-text3 -> span (链接和时间)
        - div.activity-descp -> p (推文正文)
    """
    soup = BeautifulSoup(html_content, 'html.parser')
    tweets = []
    
    # 查找 nav-tabContent 容器
    nav_tab_content = soup.find('div', id='nav-tabContent')
    
    if not nav_tab_content:
        print("  nav-tabContent 未找到")
        return []
    
    # 查找 activity-posts 容器
    activity_posts = nav_tab_content.select('div.activity-posts')
    
    if not activity_posts:
        print("  activity-posts 未找到")
        return []
    
    print(f"  找到 {len(activity_posts)} 个 activity-posts 容器")
    
    for post_container in activity_posts:
        if len(tweets) >= limit:
            break
        
        # 查找 activity-group1 (包含链接和时间)
        activity_groups = post_container.select('div.activity-group1')
        
        # 查找 activity-descp (包含推文正文)
        activity_descp = post_container.select('div.activity-descp')
        
        # 配对 activity-group1 和 activity-descp
        for i, group in enumerate(activity_groups):
            if len(tweets) >= limit:
                break
            
            # 检查是否是 retweet
            retweet_icon = group.select_one('i.fa-retweet')
            if retweet_icon:
                continue  # 跳过转推
            
            # 查找 main-user-dts1
            main_user_dts = group.select('div.main-user-dts1')
            
            for user_dt in main_user_dts:
                if len(tweets) >= limit:
                    break
                
                # 查找 user-text3
                user_text3 = user_dt.select_one('div.user-text3')
                if not user_text3:
                    continue
                
                # 提取时间和链接
                spans = user_text3.select('span')
                link_tag = user_text3.select_one('a[href*="/status/"]')
                
                if not link_tag:
                    continue
                
                href = link_tag['href']
                if href.startswith('http'):
                    tweet_url = href
                else:
                    tweet_url = f"https://twstalker.com{href}"
                
                # 提取时间（最后一个 span）
                date_str = "recent"
                if spans:
                    time_span = spans[-1]
                    date_str = time_span.get_text(strip=True) or "recent"
                
                # 获取推文正文 (从 activity-descp -> p)
                content = ""
                if i < len(activity_descp):
                    content_tag = activity_descp[i]
                    p_tags = content_tag.select('p')
                    if p_tags:
                        contents = [p.get_text(strip=True) for p in p_tags]
                        content = '\n'.join(contents)
                    else:
                        content = content_tag.get_text(strip=True)
                
                # 跳过空内容或太短的内容
                if not content or len(content) < 10:
                    continue
                
                # 过滤掉导航、侧边栏等非推文内容
                content_lower = content.lower()
                if any(x in content_lower for x in ['logo', 'search', 'subscribe']):
                    continue
                
                published_date = normalize_twitter_date(date_str) or datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
                
                # 生成标题
                short_title = content[:50] + "..." if len(content) > 50 else content
                
                tweets.append({
                    "title": short_title,
                    "link": tweet_url,
                    "summary": content,
                    "published": date_str,
                    "published_date": published_date,
                })
    
    print(f"  解析到 {len(tweets)} 条推文")
    return tweets


def get_available_accounts() -> list[str]:
    """获取所有可抓取的 Twitter 账号名称"""
    return list(TWITTER_ACCOUNTS.keys())

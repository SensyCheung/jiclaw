"""
jiclaw.py - 单次运行版本
只执行一轮 RSS 抓取，不循环

用法:
    python jiclaw.py <RSS_URL1> [RSS_URL2 ...]
    python jiclaw.py scraper:semi-insights
    python jiclaw.py twitter:elonmusk
    python jiclaw.py https://feed.com/rss.xml scraper:semi-insights twitter:elonmusk
"""

import sys

from jiclaw_core import process_feed, process_scraper, process_twitter
from jiclaw_scraper import get_available_sites
from jiclaw_twitter import get_available_accounts


def main():
    if len(sys.argv) < 2:
        print("用法：python jiclaw.py <RSS_URL1> [RSS_URL2 ...]")
        print("     或：python jiclaw.py scraper:<site_name>")
        print(f"     可用的爬虫站点：{', '.join(get_available_sites())}")
        print("     或：python jiclaw.py twitter:<username>")
        print(f"     可用的 Twitter 账号：{', '.join(get_available_accounts())}")
        sys.exit(1)

    args = sys.argv[1:]
    model = "glm-4-flash"

    for arg in args:
        if arg.startswith("scraper:"):
            # 爬虫站点（不使用代理）
            site_name = arg.replace("scraper:", "")
            try:
                process_scraper(site_name, model=model, limit=10, use_proxy=False)
            except Exception as e:
                # 编码安全输出
                print(f"处理爬虫站点时出错：{site_name} - {str(e).encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')}")
        elif arg.startswith("twitter:"):
            # Twitter 账号
            username = arg.replace("twitter:", "")
            try:
                process_twitter(username, model=model, limit=5)
            except Exception as e:
                # 编码安全输出
                print(f"处理 Twitter 账号时出错：{username} - {str(e).encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')}")
        else:
            # RSS URL
            try:
                process_feed(arg, model=model, limit=10)
            except Exception as e:
                # 编码安全输出
                print(f"处理 RSS 源时出错：{arg} - {str(e).encode('utf-8', errors='ignore').decode('utf-8', errors='ignore')}")

    print("\n抓取完成。")


if __name__ == "__main__":
    main()

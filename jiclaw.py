"""
jiclaw.py - 单次运行版本
只执行一轮 RSS 抓取，不循环

用法:
    python jiclaw.py <RSS_URL1> [RSS_URL2 ...]
    python jiclaw.py scraper:semi-insights
    python jiclaw.py https://feed.com/rss.xml scraper:semi-insights
"""

import sys

from jiclaw_core import process_feed, process_scraper
from jiclaw_scraper import get_available_sites


def main():
    if len(sys.argv) < 2:
        print("用法：python jiclaw.py <RSS_URL1> [RSS_URL2 ...]")
        print("     或：python jiclaw.py scraper:<site_name>")
        print(f"     可用的爬虫站点：{', '.join(get_available_sites())}")
        sys.exit(1)

    args = sys.argv[1:]
    model = "glm-4-flash"

    for arg in args:
        if arg.startswith("scraper:"):
            # 爬虫站点
            site_name = arg.replace("scraper:", "")
            try:
                process_scraper(site_name, model=model, limit=10)
            except Exception as e:
                print(f"处理爬虫站点时出错：{site_name} - {e}")
        else:
            # RSS URL
            try:
                process_feed(arg, model=model, limit=10)
            except Exception as e:
                print(f"处理 RSS 源时出错：{arg} - {e}")

    print("\n抓取完成。")


if __name__ == "__main__":
    main()

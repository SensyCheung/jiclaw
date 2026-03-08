"""
jiclaw_local.py - 本地循环运行版本
持续循环抓取 RSS，可配置间隔时间

用法:
    python jiclaw_local.py <RSS_URL1> [RSS_URL2 ...]
    python jiclaw_local.py scraper:semi-insights
    python jiclaw_local.py https://feed.com/rss.xml scraper:semi-insights
"""

import sys
import os
import time
from datetime import datetime

from jiclaw_core import process_feed, process_scraper
from jiclaw_scraper import get_available_sites


def main():
    if len(sys.argv) < 2:
        print("用法：python jiclaw_local.py <RSS_URL1> [RSS_URL2 ...]")
        print("     或：python jiclaw_local.py scraper:<site_name>")
        print(f"     可用的爬虫站点：{', '.join(get_available_sites())}")
        sys.exit(1)

    args = sys.argv[1:]

    # 解析参数，分离 RSS URL 和爬虫站点
    feed_urls = []
    scraper_sites = []

    for arg in args:
        if arg.startswith("scraper:"):
            site_name = arg.replace("scraper:", "")
            scraper_sites.append(site_name)
        else:
            feed_urls.append(arg)

    # 抓取间隔（秒），可通过环境变量覆盖，例如：900 = 15 分钟
    interval_str = os.environ.get("RSS_INTERVAL_SECONDS", "900")
    try:
        interval = int(interval_str)
    except ValueError:
        interval = 900

    model = "glm-4-flash"

    print(f"将持续运行，每 {interval} 秒抓取一次。可按 Ctrl+C 退出。")

    while True:
        print("\n========================================")
        print("新一轮抓取开始，时间：", datetime.utcnow().isoformat() + "Z")

        # 处理 RSS 源
        for feed_url in feed_urls:
            try:
                process_feed(feed_url, model=model, limit=10)
            except Exception as e:
                print(f"处理 RSS 源时出错：{feed_url} - {e}")

        # 处理爬虫站点
        for site_name in scraper_sites:
            try:
                process_scraper(site_name, model=model, limit=10)
            except Exception as e:
                print(f"处理爬虫站点时出错：{site_name} - {e}")

        print(f"\n本轮抓取完成，将休眠 {interval} 秒...\n")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("收到中断信号，程序退出。")
            break


if __name__ == "__main__":
    main()

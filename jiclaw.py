"""
jiclaw.py - 单次运行版本
只执行一轮 RSS 抓取，不循环
"""

import sys
from datetime import datetime

from jiclaw_core import process_feed


def main():
    if len(sys.argv) < 2:
        print("用法：python jiclaw.py <RSS_URL1> [RSS_URL2 ...]")
        sys.exit(1)

    feed_urls = sys.argv[1:]
    model = "glm-4-flash"

    print(f"执行单次 RSS 抓取。")

    for feed_url in feed_urls:
        try:
            process_feed(feed_url, model=model, limit=10)
        except Exception as e:
            print(f"处理 RSS 源时出错：{feed_url} - {e}")

    print("\n抓取完成。")


if __name__ == "__main__":
    main()

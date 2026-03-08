"""
jiclaw_local.py - 本地循环运行版本
持续循环抓取 RSS，可配置间隔时间
"""

import sys
import os
import time
from datetime import datetime

from jiclaw_core import process_feed


def main():
    if len(sys.argv) < 2:
        print("用法：python jiclaw_local.py <RSS_URL1> [RSS_URL2 ...]")
        sys.exit(1)

    feed_urls = sys.argv[1:]

    # 抓取间隔（秒），可通过环境变量覆盖，例如：900 = 15 分钟
    interval_str = os.environ.get("RSS_INTERVAL_SECONDS", "900")
    try:
        interval = int(interval_str)
    except ValueError:
        interval = 900

    model = "glm-4-flash"

    print(f"将持续运行，每 {interval} 秒抓取一次 RSS。可按 Ctrl+C 退出。")

    while True:
        print("\n========================================")
        print("新一轮抓取开始，时间：", datetime.utcnow().isoformat() + "Z")

        for feed_url in feed_urls:
            try:
                process_feed(feed_url, model=model, limit=10)
            except Exception as e:
                print(f"处理 RSS 源时出错：{feed_url} - {e}")

        print(f"\n本轮抓取完成，将休眠 {interval} 秒...\n")
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            print("收到中断信号，程序退出。")
            break


if __name__ == "__main__":
    main()

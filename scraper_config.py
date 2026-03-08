"""
scraper_config.py - 无 RSS 网站的爬虫配置
定义各网站的解析规则
"""

SCRAPER_CONFIG = {
    "semi-insights": {
        "name": "半导体产业观察",
        "url": "http://www.semi-insights.com/",
        "list_selector": "ul.info-news-c li.clearfix.bor",
        "title_selector": "h5.h5 a",
        "url_base": "http://www.semi-insights.com",  # 处理相对链接
        "date_selector": "span.date",
        "date_format": "%Y.%m.%d",  # 日期格式：2026.03.06
    },
    "icsmart": {
        "name": "半导体行业观察",
        "url": "https://www.icsmart.cn/",
        "list_selector": "div.entries article",
        "title_selector": "h2.entry-title a",
        "url_base": "https://www.icsmart.cn",
        "date_selector": "time.ct-meta-element-date",
        "date_format": "%Y年%m月%d日",
    },
}

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
    "aijiwei": {
        "name": "爱集微",
        "url": "https://laoyaoba.com/jwnews",
        "list_selector": "div#news-list li.card",
        "title_selector": "p.title",
        "url_base": "https://laoyaoba.com",
        "date_selector": "div.time",
        "date_format": "relative",  # 相对时间如 "3 分钟前"
    },
    "lam-research": {
        "name": "Lam Research",
        "url": "https://newsroom.lamresearch.com/blog",
        "list_selector": "ul.wd_item_list li.wd_item",
        "title_selector": "div.wd_title a",
        "url_base": "https://newsroom.lamresearch.com",
        "date_selector": "div.wd_date",
        "date_format": "%B %d, %Y",  # 日期格式：March 09, 2026
    },
    "lumentum": {
        "name": "Lumentum",
        "url": "https://www.lumentum.com/en/newsroom/news-releases",
        "list_selector": "div.news-item, article.news, div.press-release, li.news-item",
        "title_selector": "a",
        "url_base": "https://www.lumentum.com",
        "date_selector": "time, .date, .publish-date, span.date",
        "date_format": "%B %d, %Y",
    },
    "nvidia": {
        "name": "Nvidia News",
        "url": "https://nvidianews.nvidia.com/",
        "list_selector": "div.tiles article.tiles-item",
        "title_selector": "h3.tiles-item-text-title a",
        "url_base": "https://nvidianews.nvidia.com",
        "date_selector": "div.tiles-item-text-date",
        "date_format": "%B %d, %Y",  # 日期格式：March 12, 2026
    },
    "nvidia-dev": {
        "name": "Nvidia Developer Blog",
        "url": "https://developer.nvidia.com/blog/recent-posts/",
        "list_selector": "div.carousel-row__slide.js-post-card",
        "title_selector": "h3.carousel-row-slide__heading a, div.carousel-row-slide__title a, h3.carousel-row-slide__heading",
        "url_base": "https://developer.nvidia.com",
        "date_selector": "span.post-published-date",
        "date_format": "%b %d, %Y",  # 日期格式：Mar 12, 2026
    },
}

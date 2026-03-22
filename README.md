# jiclaw - RSS 和网页爬虫聚合器

自动抓取 RSS 源、网站内容和 Twitter 推文，通过 AI 生成摘要并写入 Notion，支持推送到 Telegram。

## 项目结构

```
jiclaw/
├── jiclaw_core.py          # 核心功能模块
├── jiclaw_scraper.py       # 网页爬虫模块（semi-insights 等）
├── jiclaw_twitter.py       # Twitter 爬虫模块（twstalker.com）
├── jiclaw_telegram.py      # Telegram 推送模块
├── telegram_config.py      # Telegram 配置
├── scraper_config.py       # 爬虫网站配置
├── twitter_config.py       # Twitter 账号配置
├── jiclaw.py               # 单次运行入口
├── jiclaw_local.py         # 循环运行入口
├── requirements.txt        # Python 依赖
└── .github/workflows/      # GitHub Action 配置
```

## 使用方法

### 本地运行

```bash
# 安装依赖
pip install -r requirements.txt

# 安装 Playwright 浏览器
playwright install chromium

# 设置环境变量
export ZHIPU_API_KEY="your_zhipu_api_key"
export NOTION_API_KEY="your_notion_api_key"
export NOTION_DATABASE_ID="your_notion_database_id"

# 单次运行
python jiclaw.py "https://feed.com/rss.xml" scraper:semi-insights twitter:paurooteri

# 循环运行（默认每 15 分钟）
python jiclaw_local.py "https://feed.com/rss.xml" twitter:paurooteri

# 自定义间隔
export RSS_INTERVAL_SECONDS=3600
python jiclaw_local.py twitter:paurooteri
```

### GitHub Action

1. 在 GitHub 仓库设置中配置 Secrets：
   - `ZHIPU_API_KEY` - 智谱 AI API 密钥
   - `NOTION_API_KEY` - Notion API 密钥
   - `NOTION_DATABASE_ID` - Notion 数据库 ID
   - `TELEGRAM_BOT_TOKEN` - Telegram Bot Token（可选）
   - `TELEGRAM_CHAT_ID` - Telegram 频道/群组 ID（可选）

2. （可选）配置 Variables：
   - `RSS_INTERVAL_SECONDS` - 抓取间隔（秒），默认 3600

3. 推送代码后会自动运行，或手动触发

**注意**：Secrets 在 GitHub 仓库的 **Settings** → **Secrets and variables** → **Actions** 中配置。

## 配置

### 1. 爬虫网站配置

编辑 `scraper_config.py` 添加新网站：

```python
SCRAPER_CONFIG = {
    "semi-insights": {
        "name": "半导体产业观察",
        "url": "http://www.semi-insights.com/",
        "list_selector": "ul.info-news-c li.clearfix.bor",
        "title_selector": "h5.h5 a",
        "url_base": "http://www.semi-insights.com",
        "date_selector": "span.date",
        "date_format": "%Y.%m.%d",
    },
    "tweaktown": {
        "name": "Tweaktown News",
        "url": "https://www.tweaktown.com/news/storage/index.html",
        "list_selector": "article.latestpost",
        "title_selector": "h2 a",
        "url_base": "https://www.tweaktown.com",
        "date_selector": "div.content-latestpost-infobar",
        "date_format": "%b %d, %Y",
    },
}
```

### 2. Twitter 账号配置

编辑 `twitter_config.py` 添加新账号：

```python
TWITTER_ACCOUNTS = {
    "paurooteri": {
        "name": "Pauro Ooteri",
        "username": "paurooteri",
    },
    "elonmusk": {
        "name": "Elon Musk",
        "username": "elonmusk",
    },
}
```

### 3. 环境变量

| 变量名 | 说明 | 必需 | 默认值 |
|--------|------|------|--------|
| `ZHIPU_API_KEY` | 智谱 AI API 密钥 | 是 | - |
| `NOTION_API_KEY` | Notion API 密钥 | 是 | - |
| `NOTION_DATABASE_ID` | Notion 数据库 ID | 是 | - |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 否 | - |
| `TELEGRAM_CHAT_ID` | Telegram 频道/群组 ID | 否 | - |
| `RSS_INTERVAL_SECONDS` | 抓取间隔（秒） | 否 | 900 |
| `TIMEZONE_OFFSET` | 时区偏移（小时数） | 否 | 0 |

**时区配置说明：**

- **GitHub Action**：设置为 `0`（服务器使用 UTC 时间）
- **本地运行（北京时间）**：设置为 `8`

```bash
# GitHub Action（UTC 时间）
TIMEZONE_OFFSET=0

# 本地运行（北京时间转 UTC）
export TIMEZONE_OFFSET=8
python jiclaw.py scraper:aijiwei
```

**Telegram 配置说明：**

**GitHub Actions 配置：**

1. 在 GitHub 仓库页面，进入 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加以下 Secrets：
   - `TELEGRAM_BOT_TOKEN` - 从 @BotFather 获取的 Bot Token
   - `TELEGRAM_CHAT_ID` - 频道/群组 ID（频道 ID 通常以 `-100` 开头）
3. 推送代码后会自动运行，或手动触发 workflow

**本地运行配置：**

1. 在 Telegram 中搜索 `@BotFather`，发送 `/newbot` 创建新 Bot
2. 按照提示设置 Bot 名称和用户名，获取 `BOT_TOKEN`
3. 创建频道或群组，将 Bot 添加为管理员
4. 获取频道/群组 ID（频道 ID 通常以 `-100` 开头）
   - 方法：将 Bot 拉入频道后发送一条消息，然后访问 `https://api.telegram.org/bot<YOUR_BOT_TOKEN>/getUpdates` 查看 `chat.id`
5. 设置环境变量：
   ```bash
   # Linux/macOS
   export TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
   export TELEGRAM_CHAT_ID="-1001234567890"
   
   # Windows PowerShell
   $env:TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
   $env:TELEGRAM_CHAT_ID="-1001234567890"
   
   # Windows CMD
   set TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   set TELEGRAM_CHAT_ID=-1001234567890
   ```

**测试 Telegram Bot：**

```bash
python jiclaw_telegram.py
```

## 功能特点

| 功能 | 说明 |
|------|------|
| RSS 抓取 | 支持多个 RSS 源 |
| 网页爬虫 | 支持无 RSS 网站（如 semi-insights.com） |
| Twitter 抓取 | 通过 twstalker.com 抓取，自动过滤转推 |
| AI 摘要 | 使用智谱 AI 生成中英文摘要和标签 |
| Notion 集成 | 自动写入 Notion 数据库 |
| Telegram 推送 | Notion 上传成功后自动推送到 Telegram |
| 去重 | 基于 URL 哈希值去重 |
| 循环运行 | 可配置间隔时间持续抓取 |

## 依赖

```bash
pip install feedparser requests beautifulsoup4 openai playwright playwright-stealth psutil
```

## 注意事项

1. **Twitter 抓取**：使用 twstalker.com 作为数据源，会自动通过 Cloudflare 验证
2. **转推过滤**：自动过滤掉 retweet 内容，只获取原创推文
3. **运行间隔**：GitHub Action 免费版建议间隔不低于 5-15 分钟
4. **浏览器缓存**：Playwright 会使用临时用户数据目录，每次运行都是干净的

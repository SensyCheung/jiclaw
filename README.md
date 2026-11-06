# jiclaw - RSS 和网页爬虫聚合器

自动抓取 RSS 源、网站内容和 Twitter 推文，通过 AI 生成摘要并写入 Notion，支持推送到 Telegram 和 Discord。

## 项目结构

```
jiclaw/
├── jiclaw_core.py          # 核心功能模块
├── jiclaw_scraper.py       # 网页爬虫模块（semi-insights 等）
├── jiclaw_twitter.py       # Twitter 爬虫模块（twstalker.com）
├── jiclaw_telegram.py      # Telegram 推送模块
├── jiclaw_discord.py       # Discord 推送模块
├── telegram_config.py      # Telegram 配置
├── discord_config.py       # Discord 配置
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
# 可选：再配一个免费模型分担限流（不配则只用智谱）
export GEMINI_API_KEY="your_gemini_api_key"
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
   - `GEMINI_API_KEY` - Google AI Studio API 密钥（可选，免费；不填则只用智谱）
   - `NOTION_API_KEY` - Notion API 密钥
   - `NOTION_DATABASE_ID` - Notion 数据库 ID
   - `TELEGRAM_BOT_TOKEN` - Telegram Bot Token（可选）
   - `TELEGRAM_CHAT_ID` - Telegram 频道/群组 ID（半导体文章，可选）
   - `TELEGRAM_CHAT_ID_OTHER` - Telegram 频道/群组 ID（非半导体文章，可选）
   - `DISCORD_BOT_TOKEN` - Discord Bot Token（可选）
   - `DISCORD_CHANNEL_ID` - Discord 频道 ID（可选）

2. （可选）配置 Variables：
   - `RSS_INTERVAL_SECONDS` - 抓取间隔（秒），默认 3600
   - `GEMINI_MODEL` - Gemini 模型名，默认 `gemini-3.1-flash-lite`
   - `LLM_MODE` - `fallback`（智谱优先、限流才切 Gemini，默认）/ `balance`（两家轮询分摊）
   - `LLM_REQUEST_INTERVAL` - 每篇之间的主动限速秒数，默认 `1`

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
| `ZHIPU_API_KEY` / `GEMINI_API_KEY` | 至少配一个模型密钥 | 是（二选一） | - |
| `GEMINI_MODEL` | Gemini 模型名 | 否 | `gemini-3.1-flash-lite` |
| `ZHIPU_MODEL` | 智谱模型名 | 否 | `GLM-4.7-Flash` |
| `LLM_MODE` | `fallback` 主备 / `balance` 轮询分摊 | 否 | `fallback` |
| `LLM_PROVIDER_ORDER` | provider 顺序，如 `gemini,zhipu` | 否 | `zhipu,gemini,compat` |
| `LLM_REQUEST_INTERVAL` | 每篇之间主动限速（秒） | 否 | `1` |
| `OPENAI_COMPAT_API_KEY` / `OPENAI_COMPAT_BASE_URL` / `OPENAI_COMPAT_MODEL` | 接任意 OpenAI 兼容服务（Groq、硅基流动、OpenRouter 等） | 否 | - |
| `NOTION_API_KEY` | Notion API 密钥 | 是 | - |
| `NOTION_DATABASE_ID` | Notion 数据库 ID | 是 | - |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot Token | 否 | - |
| `TELEGRAM_CHAT_ID` | Telegram 频道/群组 ID（半导体文章） | 否 | - |
| `TELEGRAM_CHAT_ID_OTHER` | Telegram 频道/群组 ID（非半导体文章） | 否 | - |
| `DISCORD_BOT_TOKEN` | Discord Bot Token | 否 | - |
| `DISCORD_CHANNEL_ID` | Discord 频道 ID | 否 | - |
| `RSS_INTERVAL_SECONDS` | 抓取间隔（秒） | 否 | 900 |
| `TIMEZONE_OFFSET` | 时区偏移（小时数） | 否 | 0 |

### 3.1 多模型配置（免费额度扩容）

免费档模型都有 RPM / RPD 限制，站点数量上来后单靠智谱很容易撞 `429 您的账户已达到速率限制`。
现在支持同时配置多个模型，一家报错就自动切到另一家：

| 模式 | 行为 |
|---|---|
| `LLM_MODE=fallback`（默认） | 始终先用智谱，只有它失败才动用 Gemini（省每日额度、风险低）。智谱进入 60 秒冷却期间，文章全部由 Gemini 接手 |
| `LLM_MODE=balance` | 每篇选「最久没用过」的模型，主动把请求摊到两家，吞吐最高；想最大化容量时改成这个 |

某个 provider 返回 429 / 5xx 会进入 60 秒冷却，Key 无效则冷却 10 分钟；
Gemini 模型名 404 时会自动降级到 `gemini-flash-latest` 等候选。
每篇日志会打印 `使用模型：gemini:gemini-3.1-flash-lite`，Notion 的 `LLM` 字段也记录实际使用的模型。

**验证是否生效**：每次运行开头都有 `Check LLM providers` 步骤，逐个打印各模型的
`[OK]` / `[FAIL]`（端点可达性、Key 有效性、模型名是否正确），失败不阻断抓取。
在 Actions 页面手动 **Run workflow** 时：

- `probe_only=true`：只自检、不抓取，约 2 分钟结束，日志立即可读；Gemini 失败时会自动
  列出该 Key 可见的所有模型并逐个试调，直接告诉你该用哪个模型名
- `llm_mode=balance`：强制两家分摊，用一次完整运行确认 Gemini 真在处理文章

> **模型名注意**：Google 对新建项目不再开放 `gemini-2.5-flash`（实测 404），
> 默认已设为实测可用的 `gemini-3.1-flash-lite`；想换更强的模型，先用 `probe_only` 探测，
> 再把模型名填进 Variables 的 `GEMINI_MODEL`。

**申请 Gemini 免费 Key**

1. 打开 <https://aistudio.google.com/>，用 Google 账号登录
2. 左侧 **Get API key** → **Create API key**（免费，不需要信用卡）
3. 在 GitHub 仓库 **Settings → Secrets and variables → Actions** 新增 Secret `GEMINI_API_KEY`
4. 下一次运行即生效，无需改代码；不填这个 Secret 时行为和以前一样（只用智谱）

Gemini 走官方 OpenAI 兼容端点 `https://generativelanguage.googleapis.com/v1beta/openai/`，
复用现有 `openai` SDK，没有新增依赖。免费层大致 **10–15 RPM / 250–1500 RPD**（不同模型不同，
以 AI Studio 的 Rate limit 页面为准）。

> **两点注意**
> - Google 端点在中国大陆**直连不通**。GitHub Actions 的 runner 在境外不受影响；
>   本地运行需要代理，或直接 `LLM_PROVIDER_ORDER=zhipu` 只用智谱。
> - Google 免费层的 prompt 可能被用于产品改进；如果内容敏感，别把正文发给它。
> - 也可以不配 Gemini，改用 `OPENAI_COMPAT_*` 接 Groq / 硅基流动 / OpenRouter 的免费模型，机制完全相同。

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

系统会用 AI 对每篇文章做行业分类，并按分类推送到不同群组：

- **半导体行业文章**（芯片/IC、晶圆制造、设备材料、存储、封装、EDA、光刻等）→ `TELEGRAM_CHAT_ID`
- **非半导体文章**（家电、消费电子整机、泛科技/软件、游戏、汽车整车等）→ `TELEGRAM_CHAT_ID_OTHER`

> 若未配置 `TELEGRAM_CHAT_ID_OTHER`，非半导体文章会被**剔除**（不推送），只保留半导体文章推送到主群组。
> 若希望所有文章都推送到同一个群组，将 `TELEGRAM_CHAT_ID_OTHER` 设为与 `TELEGRAM_CHAT_ID` 相同即可。
> 分类采用「拿不准就归其他群组」的保守策略：模型返回缺失、无法识别的取值，一律走 `TELEGRAM_CHAT_ID_OTHER`，主群组只保留判定明确的半导体文章。
> **ID 写错不会被静默吞掉**：只填了后 10 位（如 `3824126934`）或漏了负号时，代码会自动补成 `-1003824126934` 并在日志里提示；若补全后仍 `chat not found`，会回落用原值再试一次（兼容私聊用户 ID）。`403 Forbidden` 不会重试，那是 Bot 没进群/没发帖权限。

**GitHub Actions 配置：**

1. 在 GitHub 仓库页面，进入 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加以下 Secrets：
   - `TELEGRAM_BOT_TOKEN` - 从 @BotFather 获取的 Bot Token
   - `TELEGRAM_CHAT_ID` - 半导体文章的频道/群组 ID（频道 ID 通常以 `-100` 开头）
   - `TELEGRAM_CHAT_ID_OTHER` - 非半导体文章的频道/群组 ID（可选）
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
   export TELEGRAM_CHAT_ID_OTHER="-1009876543210"
   
   # Windows PowerShell
   $env:TELEGRAM_BOT_TOKEN="1234567890:ABCdefGHIjklMNOpqrsTUVwxyz"
   $env:TELEGRAM_CHAT_ID="-1001234567890"
   $env:TELEGRAM_CHAT_ID_OTHER="-1009876543210"
   
   # Windows CMD
   set TELEGRAM_BOT_TOKEN=1234567890:ABCdefGHIjklMNOpqrsTUVwxyz
   set TELEGRAM_CHAT_ID=-1001234567890
   set TELEGRAM_CHAT_ID_OTHER=-1009876543210
   ```

**测试 Telegram Bot：**

```bash
python jiclaw_telegram.py
```

**Discord 配置说明：**

**GitHub Actions 配置：**

1. 在 GitHub 仓库页面，进入 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加以下 Secrets：
   - `DISCORD_BOT_TOKEN` - 从 Discord Developer Portal 获取的 Bot Token
   - `DISCORD_CHANNEL_ID` - 频道 ID（数字，不带引号）
3. 推送代码后会自动运行，或手动触发 workflow

**本地运行配置：**

1. 访问 [Discord Developer Portal](https://discord.com/developers/applications)，创建新应用
2. 进入 **Bot** 页面，点击 **Reset Token** 获取 Bot Token
3. 在 **OAuth2 → URL Generator** 中，选择 `bot` 权限，复制生成的链接到浏览器打开
4. 将 Bot 邀请到你的服务器
5. 在服务器中右键点击频道，选择 **复制频道 ID**（需要开启开发者模式）
   - 开启开发者模式：用户设置 → 高级 → 开发者模式
6. 设置环境变量：
   ```bash
   # Linux/macOS
   export DISCORD_BOT_TOKEN="MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GJKLmN.OpQrStUvWxYz"
   export DISCORD_CHANNEL_ID="1234567890123456789"

   # Windows PowerShell
   $env:DISCORD_BOT_TOKEN="MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GJKLmN.OpQrStUvWxYz"
   $env:DISCORD_CHANNEL_ID="1234567890123456789"

   # Windows CMD
   set DISCORD_BOT_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.GJKLmN.OpQrStUvWxYz
   set DISCORD_CHANNEL_ID=1234567890123456789
   ```

**测试 Discord Bot：**

```bash
python jiclaw_discord.py
```

## 风险事件抽取（供下游风险平台消费）

摘要 prompt 在原有 `category / tags / title_cn / summary_*` 之外，额外抽取一个 `risk_event` 对象，
用于对接半导体风险监控类平台（如 SEISMIQ）。**同一次模型调用产出，不额外消耗额度。**

字段与枚举定义在 `jiclaw_core.py` 的 `_RISK_EVENT_TYPES` / `_HAZARD_CLASSES`：

| 字段 | 说明 |
|------|------|
| `is_risk_event` | 是否是「影响供应链/产能/经营连续性」的具体事件；纯技术、评测、观点类为 `false` |
| `event_type` | `labor_dispute` `natural_disaster` `power_outage` `fire_explosion` `water_shortage` `material_shortage` `export_control` `litigation` `capacity_disruption` `mna` `earnings` `technology_release` `supply_shortage` `geopolitical` `other` |
| `hazard_class` | `operational` `natural` `regulatory` `financial` `technology` `market` `other` |
| `entity` | 主体公司，统一中文通用名（首次附英文原名），如 `美光科技(Micron)` |
| `site` | 厂区/地点，中文，如 `桃园厂`、`Fab 18` |
| `country_region` | 国家/地区中文名 |
| `severity` | `0.0-1.0`；≥0.8 全线停产/重大灾害，0.4-0.7 部分产线受影响，≤0.3 局部摩擦 |
| `affected_products` | 受影响品类数组，如 `["HBM","DRAM"]` |
| `occurred_at` | 事件发生日期，`YYYY-MM-DD` 或 `YYYY-MM`，不确定为 `null` |

**模型输出的容错**：`_normalize_risk_event()` 会把中文枚举（「劳资纠纷」「停电」）、大小写/连字符变体、
0-100 分的 severity、`"0.65"` 字符串、数组包装等统一收敛成固定 schema；不认识的枚举降级为 `other`，
保证下游入库不会因脏值报错。

**日期校正**：中文新闻常用「11月5日」省略年份，实测模型会把 2026 的事件写成 2024。
`published_date` 会作为锚点传给模型；`_fix_event_year()` 再兜一层（仅同月、年份偏早且差距 ≤2 年时改），
避免把真旧闻改坏。

**落地方式**：命中风险事件的文章追加写入 `risk_events/risk_events-YYYY-MM-DD.jsonl`
（路径可用 `RISK_EVENTS_DIR` 覆盖），一行一条，字段与上表一致并附加 `post_id / dedup_key / url / title / title_cn /
source / published_at / llm / ingested_at`。GitHub Actions 每次运行都会把它作为产物上传
（`risk-events-<run_id>`，保留 30 天），下游按天拉取即可。

`dedup_key` 是 `entity + event_type + occurred_at` 的哈希，给下游做**软去重**：同一事件常被多家媒体同时报道
（实测美光停建被两个源各抽一条），同键可归为一簇。它不是权威 id——实体名写法不同（"美光" / "美光科技(Micron)"）会算出不同的键。

**判定口径（会持续按实测噪声调整）**：

- `earnings` 只认坏消息（下调指引、不及预期、亏损、裁员），营收增长 / 符合预期不算风险事件
- 常规制程量产、新品发布属于日常经营，没有明确产能或供应链冲击时 `is_risk_event` 为 `false`
- `entity` 禁止泛称（"存储巨头"、"某晶圆厂"、"业内人士"），定位不到具体主体时给 `null`

本地验证：

```bash
python -c "from jiclaw_core import check_llm_generation; check_llm_generation()"
python export_risk_feed.py --dir risk_events   # 重新汇总 latest.json
```

### 稳定数据端点（下游直接拉取，无需密钥）

工作流会把分片和汇总结果 commit 回仓库，所以下游只要 GET 一个固定地址即可，
不用碰会过期、且下载需鉴权的 Actions artifact：

```
https://raw.githubusercontent.com/SensyCheung/jiclaw/main/risk_events/latest.json
```

已实测 `HTTP 200` 且响应头为 `Access-Control-Allow-Origin: *`，浏览器端可直接跨域 fetch，无需密钥。
若后续 raw 请求被限流，可自行套一层 CDN（例如 `cdn.jsdelivr.net/gh/...`），
但该路径**本项目未验证可达性**，且 CDN 有缓存延迟，实时告警场景请优先用 raw。

`latest.json` 结构：`schema_version / generated_at / count / feed / events[]`。
`feed` 里写明裁剪策略（`max_age_days` / `max_events` / `dedup` / `sorted_by`），
下游不必猜数据口径；目前 `schema_version = 2`。
`export_risk_feed.py` 会做三件事：

1. **多源同题归并**：同一事件被多家媒体报道时合成一条，原始 URL 全留在 `sources[]`，
   `source_count` 是报道源数。实测美光停建的两篇报道判成了不同 `event_type`
   （`labor_dispute` 因 / `capacity_disruption` 果），现在合为一簇，
   `primary_event_type` 取优先级更高的「结果」类，两种类型都保留在 `event_types[]`。
2. **实体名容错**：`美光` / `美光科技(Micron)` 归一后同簇；旧数据里的泛称「存储巨头」
   与真名对不上时，用厂区 `site` 兜底合并，并让真名覆盖泛称。
3. **幂等 + 裁剪**：事件内容没变就不重写 `latest.json`（否则每小时一次空提交）；
   只保留最近 90 天、最多 500 条，按发生日期与严重度倒序。

前端接入示例（raw.githubusercontent.com 允许跨域）：

```js
const FEED = "https://raw.githubusercontent.com/SensyCheung/jiclaw/main/risk_events/latest.json";
const { events } = await (await fetch(FEED)).json();
// 入库前用 dedup_key 或 post_id 判重；severity >= 0.6 可视为需告警的高影响事件
const alerts = events.filter(e => e.severity >= 0.6);
```

> 归并是**保守的**：必须有相同的发生日期与灾种，且主体名互相包含（或泛称+同厂区）才合并。
> 泛称已在 prompt 层禁掉，所以新数据主要走实体名匹配这条路径。

## 功能特点

| 功能 | 说明 |
|------|------|
| RSS 抓取 | 支持多个 RSS 源 |
| 网页爬虫 | 支持无 RSS 网站（如 semi-insights.com） |
| Twitter 抓取 | 通过 twstalker.com 抓取，自动过滤转推 |
| AI 摘要 | 使用智谱 AI 生成中英文摘要、标签，并判断是否为半导体行业文章 |
| 风险事件抽取 | 同一次调用顺带抽取 event_type / severity / entity / site 等结构化字段，落 JSONL 供下游平台消费 |
| Notion 集成 | 自动写入 Notion 数据库 |
| Telegram 推送 | 按行业分类推送到不同群组：半导体文章推到主群组，其他文章推到其他群组（未配置则剔除） |
| Discord 推送 | Notion 上传成功后自动推送到 Discord |
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

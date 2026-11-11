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
   - `TELEGRAM_CHAT_ID` - Telegram 频道/群组 ID（主群组，可选）
   - `TELEGRAM_CHAT_ID_OTHER` - Telegram 频道/群组 ID（others 群：中文原文 + 非半导体文章，可选）
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

### 1.1 USBDev.ru（更新日志源，需俄罗斯出口）

`scraper:usbdev` 抓的是 <https://www.usbdev.ru/news/>，**这个站没有新闻流**：

- WordPress 文章停更在 2020-07，`/feed/` 里只有一个 2020 年的 item（等于死链）
- `/articles/`、`/reviews/` 都是静态索引页
- 唯一持续更新的是 `/news/` 页的**文件更新日志表格**：`31.10.2026 | [+] | Maxio MAS0902 Utility v2.12`，
  无链接、无正文，实测 2232 条 / 534 天

所以按天聚合成一条，而不是一条记录一条：

```
title:   USBDev.ru update log — 2026-10-31 (1 item)
summary: - [新增] Maxio MAS0902/MAS0901 MP/Sorting Tool A.0.08.014 [B0KB-T190103]
link:    https://www.usbdev.ru/news/#2026-10-31   ← 按天去重
```

- `limit` = 取最近多少天；`summary_only: True` 让它跳过正文抓取（否则会把整张表当正文喂给模型）
- 想改成每条一行，把 `scraper_config.py` 里 `usbdev` 的 `"mode"` 改为 `"item"` 即可
- **代价**：同一天后续新增的条目不会再补写（当天那条已按日期去重）。该站现实节奏约 1–2 条/天，可接受

**反爬**：站点按 IP 归属放行，中国大陆直连、真实 Chromium、Cloudflare 类代理出口实测**全部 403**
（连 `/robots.txt` 都 403），只有俄罗斯住宅 IP 能拿到 200。因此：

- GitHub Actions（Azure 机房 IP）必须走代理，在仓库 **Variables** 里配 `USBDEV_PROXY`
  （形如 `http://user:pass@host:port`）
- 未配置时代码会打印 `HTTP 403：该站只允许俄罗斯 IP 访问` 后返回空列表，不影响其他源
- 代理是 MITM 证书时可用 `USBDEV_INSECURE=1` 关闭校验（默认校验）

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
| `TELEGRAM_CHAT_ID` | Telegram 频道/群组 ID（主群组：外文原文的半导体文章） | 否 | - |
| `TELEGRAM_CHAT_ID_OTHER` | Telegram 频道/群组 ID（others 群：中文原文 + 非半导体文章） | 否 | - |
| `DISCORD_BOT_TOKEN` | Discord Bot Token | 否 | - |
| `DISCORD_CHANNEL_ID` | Discord 频道 ID | 否 | - |
| `RSS_INTERVAL_SECONDS` | 抓取间隔（秒） | 否 | 900 |
| `USBDEV_PROXY` | usbdev.ru 的俄罗斯出口代理（该站只放行俄区 IP） | 否 | - |
| `USBDEV_INSECURE` | 设为 `1` 时不校验 usbdev.ru 的 TLS 证书（代理做 MITM 时用） | 否 | - |
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

推送目标按「原文语言 → 行业分类」两级判定，**原文语言优先**：

1. **原文为中文的文章**：无条件推送到 `TELEGRAM_CHAT_ID_OTHER`（others 群），即使模型判定它是半导体文章。主群组只保留外文原文（由模型译成中文）的稿件。
2. 原文非中文时，再用 AI 行业分类结果分流：
   - **半导体行业文章**（芯片/IC、晶圆制造、设备材料、存储、封装、EDA、光刻等）→ `TELEGRAM_CHAT_ID`
   - **非半导体文章**（家电、消费电子整机、泛科技/软件、游戏、汽车整车等）→ `TELEGRAM_CHAT_ID_OTHER`

中文判定由 `jiclaw_telegram.py` 的 `is_chinese_original_text()` 完成，**只依据抓取到的原始标题 / 正文 / RSS 摘要**，绝不使用模型生成的 `title_cn` / `summary_cn`（它们恒为中文）。规则：标题汉字 ≥ 4 且占「汉字+拉丁字母」≥ 30% 即判中文；标题是英文但正文以中文为主时，用正文前 600 字兜底（先剥 HTML 标签，≥ 12 汉字且占比 ≥ 50%），避免英文文章里几处中文引文被误判。线上日志会打印实际路由，便于核对：`[Telegram] 路由：中文原文 → others 群组`。

> 若未配置 `TELEGRAM_CHAT_ID_OTHER`，非半导体文章**以及中文原文文章**都会被**剔除**（不推送），只保留外文原文的半导体文章推送到主群组。
> 若希望所有文章都推送到同一个群组，将 `TELEGRAM_CHAT_ID_OTHER` 设为与 `TELEGRAM_CHAT_ID` 相同即可。
> 分类采用「拿不准就归其他群组」的保守策略：模型返回缺失、无法识别的取值，一律走 `TELEGRAM_CHAT_ID_OTHER`；叠加语言规则后，主群组只保留「原文非中文 + 判定明确的半导体文章」。
> **ID 写错不会被静默吞掉**：只填了后 10 位（如 `3824126934`）或漏了负号时，代码会自动补成 `-1003824126934` 并在日志里提示；若补全后仍 `chat not found`，会回落用原值再试一次（兼容私聊用户 ID）。`403 Forbidden` 不会重试，那是 Bot 没进群/没发帖权限。

**GitHub Actions 配置：**

1. 在 GitHub 仓库页面，进入 **Settings** → **Secrets and variables** → **Actions**
2. 点击 **New repository secret**，添加以下 Secrets：
   - `TELEGRAM_BOT_TOKEN` - 从 @BotFather 获取的 Bot Token
   - `TELEGRAM_CHAT_ID` - 主群组的频道/群组 ID（外文原文的半导体文章，通常以 `-100` 开头）
   - `TELEGRAM_CHAT_ID_OTHER` - others 群的频道/群组 ID（中文原文 + 非半导体文章，可选）
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
| `lat` / `lon` | 事件地点的 WGS84 十进制度坐标；只在厂区位置公开且唯一确定时给，否则 `null`（禁止估算） |
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
- 厂区火灾、爆炸、起火等安全事故一律判 `fire_explosion`，即使报道重点在停产、缺货或供应链冲击（后果写进 `affected_products` / `severity`，不要改判 `capacity_disruption`）。汇总层还会按标题把历史误标的同类事件归一回火灾口径

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

## 自定义 RSSHub：Bloomberg 作者源

Bloomberg 作者页反爬严格，官方 `rsshub.app` 不稳；订阅走自建实例
`https://rsshub-demo.cautious-menu.workers.dev`，路由用 RSSHub 官方的
`/bloomberg/authors/:id/:slug/:source?`，第三段固定 `rss`（直接取 Bloomberg 作者的
`.rss`，比 lineup API 稳）。当前订阅 3 位长期写半导体的作者：

| 作者 | 分工 | id / slug |
|------|------|-----------|
| Ian King | 美国半导体记者：Nvidia / Intel / 出口管制 / 芯片股 | `AB1M_8ht4mM/ian-king` |
| Debby Wu | 亚洲科技：台积电、美光、存储与供应链 | `ATjxWD012Wk/debby-wu` |
| Catherine Thorbecke | 亚洲科技专栏：TSMC / Rapidus / 芯片地缘 | `AWztaihZGwI/catherine-thorbecke` |

Mark Gurman（苹果公司动向）、Matt Levine（金融）、John Authers（宏观）等也有作者 feed，
但内容与半导体无关，不订阅，避免污染首页。

**两个必须知道的特性**：

1. **慢源**：RSSHub 会串行抓取 20 篇文章全文，实测每个 feed 约 **60–65 秒**才返回。
   探测时 timeout 至少给 120–180 秒，45 秒会误判成不可用；生产路径用
   `feedparser.parse(url)`（无显式超时），可以正常等到。
2. **不要二次抓正文**：RSSHub 已把全文写进 `content:encoded`，而 `bloomberg.com`
   正文对 runner 返回 403。该域名已加进 `USE_RSS_SUMMARY_FEEDS`，直接用 RSS 内容；
   `get_website_name()` 也按 `/bloomberg` 前缀把 Notion 的 Website 还原成 `Bloomberg`，
   否则会显示成 `rsshub-demo`。

> RSSHub 的 feed 标题取自首篇文章的第一作者，合著稿件可能显示成别人
> （Debby Wu 的 feed 曾显示 `Bloomberg - Molly Schuetz`）；条目本身正确，不影响入库。
>
> 另外，`feedparser.parse(url)` 本身没有超时，慢源偶发不响应会把整轮 Actions 挂死；
> `jiclaw_core._parse_feed` 已给抓取设 180 秒上限并恢复全局 socket 设置。
>
> **已知问题**：Notion `tags` 是 multi_select，LLM 每造一个新标签就新增一个 select 选项，
> 线上该属性已涨到 499KB，触顶后 Notion 返回 400 拒绝整条页面。
> `create_notion_page` 现在遇到 `maximum size` 会去掉标签重试一次，保证文章本体入库；
> 彻底解决需要清理未使用的 tags 选项，或把标签收敛到固定词表。

新增作者：从作者页 URL `https://www.bloomberg.com/authors/{id}/{slug}` 取 id 与 slug，
先用只读探测确认可用，再加进 `rss_to_notion.yml` 的 URL 列表：

```bash
# 借 GitHub Actions 的境外网络（本地到 *.workers.dev 不通）
gh workflow run rss_to_notion.yml --ref main \
  -f probe_only=true -f probe_timeout=180 \
  -f "probe_feed=https://rsshub-demo.cautious-menu.workers.dev/bloomberg/authors/{id}/{slug}/rss"

# 本地能连外网时直接跑
python probe_feeds.py --timeout 180 "<feed url>"
```

`probe_feeds.py` 打印 HTTP 状态、feed 标题、条目数与前三条标题；加 `--feedparser`
可复现生产代码的 `feedparser.parse(url)` 路径。`probe_author_pages` 输入可从文章页
HTML 里提取 `/authors/{id}/{slug}` 链接（反爬拦截时拿不到，需另找作者页 URL）。

## Notion `tags` 选项治理

`tags` 是 multi_select：写入一个库里不存在的 name，Notion 会**自动新建一个选项**。
旧 prompt 允许模型「列表没有合适的就提取正文公司名」，长期累积到 **5859 个选项 /
651KB**（官方建议整个 schema ≤ 50KB），新文章写入开始返回
`400 database schema has exceeded the maximum size`，整条页面被拒。

**收敛到固定词表**（`jiclaw_core.py`）：`CANDIDATE_TAGS` 是唯一数据源（43 个标签），
既生成 prompt 的 `[Candidate Tags]`，也在 `create_notion_page` 里经 `normalize_tags()`
硬过滤 —— 归一大小写/空白、去重、最多 3 个、自造词一律丢弃。要加标签只改这一处，
`PROHIBITED_TAGS` 里的词永远不写入；这样选项数不会再增长。

**历史选项清理**（`notion_tags_cleanup.py`，密钥由 Actions Secrets 注入）：

```bash
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_tags=probe    # 只读统计 + 备份
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_tags=dry-run  # 预演
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_tags=apply    # 清理并自动恢复页面标签
# 从某次 run 的备份单独恢复标签
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_tags=restore -f tags_backup_run=42922769007
```

备份 `tags_options_backup.json` 含全部选项与**页面级标签映射**，由 workflow 上传为
artifact 保留 90 天（本地 `.gitignore` 忽略）。`apply` 幂等：零引用选项少于 20 个
（`MIN_ZERO_REF_TO_CLEAN`）时直接跳过 —— 因为每轮清理都会删掉大部分仍被引用的选项
再靠 `restore` 重建，为几个新垃圾跑一轮不划算。`tags_keep_min` 默认 1，
脚本会自动适配 API 上限。

三个实测坑，别再踩：

1. **选项名匹配忽略大小写/空格**。库里有 `chiplet` 时再新增 `Chiplet` 会 400
   `duplicates not allowed`；页面写入 `Chiplet` 本来就会复用 `chiplet`。
2. **PATCH database 的 `options` 数组最多 100 项**，且它是「完整保留列表」——
   未列出的选项会被移除。脚本会自动抬高 `tags_keep_min` 直到满足上限。
3. **移除选项会连带清掉页面上的标签值**（官方文档说不会，实测会）。所以 `apply`
   在清理后立即按清理前快照把标签逐页写回；写回不存在的 name 时 Notion 自动重建
   该选项，最终状态等价于「只删零引用选项」，页面数据零损失。

2026-11-11 执行结果：清掉 5454 个零引用选项，`tags` 属性 651,632 → 44,441 bytes，
选项 5859 → 405 个（全部仍被页面引用）；中途 277 个页面的标签被连带清掉，已用备份
全部补回，逐页校验缺失 0 条。随后完整抓取 run `42926440829` 成功：68 条新文章写入
Notion，`写入 Notion 失败` / `maximum size` / `去掉标签后重试` 均为 0；再次 `apply`
复跑时的「清理 → 自动恢复」逐页校验也显示全部标签值完好。

## HotorNot / 首页收录口径

Notion 的 `HotorNot` 是 select，选项为 `Not / Hot / Home / noHome`（**注意驼峰**，
写成 `nohome` 会凭空造出一个新选项）。`create_notion_page` 默认写 `Home`，下列文章写 `noHome`：

| 类型 | 例子 | 判定 |
|------|------|------|
| 整站口径 | kitguru（游戏资讯）、electronicsforu（招聘帖） | `NOHOME_WEBSITES` |
| 周期性栏目 | Seeking Alpha「政治与市场 11/8/26」「1-Minute Market Report」「国债收益率快照」、Tom's Hardware Premium 周报 | 显式栏目名 `_NOHOME_COLUMN_PATTERNS` |
| 其他源站的周期栏目 | Electronics Weekly 时事通讯订阅页、bits-chips「阅读最新一期杂志」、IFA 2026 展会图文 | 「系列键」在同一源站出现 ≥2 次，且标题带日期/期号或栏目词 |

`title_series_key()` 会把 `Politics And The Markets 11/8/26` 与 `... 10/26/26` 归一成
同一个系列键 —— 按完整标题去重是识别不出这种「周期性重复」的。发现新栏目时，
把栏目名加进 `_NOHOME_COLUMN_PATTERNS` 即可，不用改判定逻辑。

**半导体内容一律保留 `Home`**：`category=semiconductor`、标题含半导体主题词或公司名、
`tags` 含半导体标签，都优先保护，避免把湾芯展、光互连、瑞萨财报这类产业新闻踢出首页。

历史页面批量修正（`notion_hotornot.py`，密钥由 Actions Secrets 注入，本地无需保存 token）：

```bash
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_hotornot=probe     # 只读排查
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_hotornot=dry-run   # 预演增量
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f notion_hotornot=apply     # 批量写入
```

`apply` 幂等：已标 `noHome` 的页面会跳过。2026-11-09 首次执行更新 67 条、失败 0 条，
库内 `noHome` 由 358 条增至 425 条（总 10000 条），复跑预演新增 0 条。

## 推送到 SEISMIQ

`push_to_seismiq.py` 把 `latest.json` 推到 `POST https://seismiq-api.jijibot.workers.dev/api/webhook`。
在工作流里位于汇总之后、commit 之前，失败不阻断抓取。

**鉴权**：主 token 存于 GitHub 仓库 Secret **`SEISMIQ_WEBHOOK_SECRET`**——名字容易误解，
里面存的其实是 `Authorization: Bearer <主token>` 的值，**不是** HMAC 签名密钥。
工作流把它注入到脚本的 `SEISMIQ_WEBHOOK_TOKEN` 环境变量；未配置时自动跳过，不报错。
本地运行用 `SEISMIQ_WEBHOOK_TOKEN` 环境变量传入，或放在已 gitignore 的 `.env` 里；
脚本不读取、不打印 token 值。只有 SEISMIQ 侧**确实开启了** HMAC 校验时，才另设
`SEISMIQ_HMAC_SECRET`，脚本会对每批 raw body 计算 `X-Webhook-Signature: sha256=<HMAC-SHA256>`；
不设就不发签名头。

```bash
python push_to_seismiq.py --dry-run          # 只打印 payload，核对映射
python push_to_seismiq.py --limit 2          # 小批试推
python push_to_seismiq.py                   # 全量（靠 dedupe_key 幂等）
python push_to_seismiq.py --include-news     # 显式开启资讯通道（默认关闭）
```

只验证真实写入、跳过 9 分钟抓取：

```bash
gh workflow run rss_to_notion.yml --ref main -f probe_only=true -f test_seismiq_write=true
```

核对「推了没推、收了几条」：

```
GET https://seismiq-api.jijibot.workers.dev/api/webhook/runs?limit=20
```

### 只写风险事件，正常新闻不写入

按接入要求，`push_to_seismiq.py` 默认**只写 `events[]`，完全不写 `news[]`**。
财报、并购、长协、扩产、技术发布等经营资讯一律不进 SEISMIQ。两道闸：

1. **风险门槛**：跳过 `is_risk_event=false` 和 `severity < 0.4` 的记录（`--min-severity` 可调）。
   `latest.json` 里混着被上游误标成风险的低严重度财报，靠这道闸挡住。
2. **灾种映射**：`/api/webhook/schema` 的 `event.type` 是**封闭枚举**
   `earthquake / wildfire / volcano / flood / storm / drought / epidemic / strike / conflict / power / plantfire`，
   且写入后会立即重算「事件 × 晶圆厂」影响。没有对应灾种的事件硬塞进 `events`
   会写不进去或误触发地理影响计算，所以直接跳过。

以当前真实数据为例：14 条 → 保留 2 条（松下湖口厂 CCL 爆炸 → `plantfire`、
美光桃园厂停工 → `strike`），其余 12 条跳过，日志按原因计数（`严重度不足` / `无对应灾种`）。
确实需要资讯通道时显式加 `--include-news`。

| jiclaw 类型 | SEISMIQ 灾种 | 说明 |
|------|--------|------|
| `labor_dispute` | `strike` | 劳资纠纷/罢工 |
| `geopolitical` | `conflict` | 地缘政治/冲突 |
| `water_shortage` | `drought` | 供水短缺 |
| `power_outage` | `power` | grid 模式；只从 `site` 明确识别园区时填 `grid_zone`，认不出就留空让服务端 fail-closed |
| `fire_explosion` | `plantfire` | 工厂火灾/爆炸；SEISMIQ 的厂区火灾专用模式（`wildfire` 只留给山火） |
| `natural_disaster` | 按标题关键词细分 | 地震/火山/洪水/台风/干旱/山火/疫情；猜不出则不写入 |
| 其余（含 `capacity_disruption`、`mna`、`earnings`） | 无 | 枚举里没有对应灾种，跳过 |

> SEISMIQ 已新增 `plantfire`（厂区火灾）灾种，工厂火灾/爆炸现在走这个专用模式，
> 不再借 `wildfire`（山火语义）。`title` 会加 `[厂区火灾]` 前缀、`metric` 注明
> 「事故: 厂区火灾/爆炸」，原始 `event_type` 仍完整保存在 `raw` 里。
> 山火/野火仍归 `wildfire`，不会被当成厂区火灾。
>
> 上游 LLM 曾把「松下湖口厂爆炸」按后果判成 `capacity_disruption`。现在两层兜底：
> 汇总时按标题把这类事件的主类型归一为 `fire_explosion`（原始类型留在 `event_types`），
> 推送时再按标题关键词识别一次，保证旧数据也不会漏推；「需求爆炸式增长」等比喻用法会先剔除。

`event` 字段：`dedupe_key = jiclaw:<dedup_key 或 post_id>`（稳定幂等）、`ext_id = post_id`、
`source_id = jiclaw`、`country` 用 `country_region`（如 `中国台湾`，与 SEISMIQ 的 `fabs.region` 对齐）、
`place` 用 `site`、`severity` 由 0–1 映射为 `Red(≥0.7) / Orange(≥0.4) / Green`、
`started_at` **只传完整的 `YYYY-MM-DD`**（`2026-11` 这类年月不是合法 ISO 日期，宁可不传），
`raw` 存一份完整结构化快照（含 `event_types` / `sources` / `affected_products`）。

三点刻意保守的设计：

1. **坐标有来源、可追溯**。抽取 prompt 允许模型在「厂区位置公开且唯一确定」时输出
   `lat/lon`，并明确禁止按城市中心估算；归一化会丢弃越界值和 `(0,0)` 占位。
   推送时 `--coords-file` 的人工核准坐标表优先级最高，其次才用模型坐标；
   两者都没有就不传，走 `country` 的 region 模式。
2. **`confidence` 默认 `assessed`**。事件是 LLM 从新闻里抽取的评估而非官方确证；
   SEISMIQ 会把 assessed 事件的影响等级封顶为「关注」，避免模型推断被当成确证事实放大告警。
   确有权威来源时可改 `--confidence fact`。
3. **不写 `kv`**。schema 里 kv 分组形态有歧义，且 `event.raw` 已保留完整数据，
   不往对方的 KV 表塞未经要求的内容。

`kv` 之外还有 `X-Webhook-Source: jiclaw` 头；每批最多 100 条（schema 上限 200 条 / 1 MB）。

## 功能特点

| 功能 | 说明 |
|------|------|
| RSS 抓取 | 支持多个 RSS 源 |
| 网页爬虫 | 支持无 RSS 网站（如 semi-insights.com） |
| Twitter 抓取 | 通过 twstalker.com 抓取，自动过滤转推 |
| AI 摘要 | 使用智谱 AI 生成中英文摘要、标签，并判断是否为半导体行业文章 |
| 风险事件抽取 | 同一次调用顺带抽取 event_type / severity / entity / site 等结构化字段，落 JSONL 供下游平台消费 |
| Notion 集成 | 自动写入 Notion 数据库 |
| Telegram 推送 | 中文原文一律推到 others 群；外文原文再按行业分类：半导体→主群组，非半导体→others 群（未配置则剔除） |
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

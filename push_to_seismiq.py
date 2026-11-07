#!/usr/bin/env python3
"""把 risk_events/latest.json 推送到 SEISMIQ webhook。

分流依据 SEISMIQ 的 GET /api/webhook/schema：

* ``events[]`` 是「有地理影响的物理/社会事件」，``type`` 必须是封闭枚举
  (earthquake/wildfire/volcano/flood/storm/drought/epidemic/strike/conflict/power/plantfire)，
  写入后会立即重算「事件 × 晶圆厂」影响。只有能真实映射灾种的事件才放进来。
  工厂火灾/爆炸走 ``plantfire``（厂区火灾模式），不再借 ``wildfire``（山火语义）。
  坐标优先级：人工核准的厂区坐标表 > 抽取时由 LLM 写入的 lat/lon > 不传。
* **默认完全不写 ``news[]``**。要求「只写风险相关的，正常新闻不要往里面写入」：
  财报、并购、长协、扩产、技术发布等经营资讯一律跳过，不进 SEISMIQ。
  确实需要资讯通道时显式加 --include-news。
* 另有 severity 门槛（默认 0.4），把「被 LLM 标成风险但严重度极低」的噪声挡掉。

没有经纬度时**不编坐标**：不传 lat/lon，让 SEISMIQ 走 region 模式（按 country 匹配）。
需要 distance 模式时，用 --coords-file 提供一份显式、准确的厂区坐标表。

凭证（环境变量）：
    SEISMIQ_WEBHOOK_TOKEN  主 token（Bearer），必填，绝不打印
    SEISMIQ_HMAC_SECRET    可选；仅当 SEISMIQ 侧真的开了 HMAC 签名校验时才设

用法：
    python push_to_seismiq.py --dry-run          # 只打印 payload，不发送
    python push_to_seismiq.py --limit 2          # 小批试推
    python push_to_seismiq.py                   # 全量推送（靠 dedupe_key 幂等）
"""

import argparse
import hashlib
import hmac
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests

WEBHOOK_URL = "https://seismiq-api.jijibot.workers.dev/api/webhook"
SOURCE_ID = "jiclaw"
MAX_ITEMS_PER_REQUEST = 100   # schema: maxItems 200 / maxBodyBytes 1,000,000，留足余量
DEFAULT_MIN_SEVERITY = 0.4

# 直接映射：jiclaw event_type -> SEISMIQ 灾种
# fire_explosion 走 plantfire（厂区火灾专用模式）；wildfire 只留给真正的山火/野火。
# capacity_disruption、mna、earnings 等经营类事件仍无对应灾种，一律不写入。
_TYPE_MAP = {
    "labor_dispute": "strike",       # 劳资纠纷/罢工
    "geopolitical": "conflict",      # 地缘政治/冲突
    "water_shortage": "drought",     # 供水短缺，最接近干旱
    "power_outage": "power",         # 停电/限电，SEISMIQ 走 grid 分区模式
    "fire_explosion": "plantfire",   # 工厂火灾/爆炸 → 厂区火灾模式
}

# type=power 专用：grid_zone 是判定依据，缺失时服务端 fail-closed 不推算任何设施。
# 只认能明确定位到园区的写法；认不出就宁可不填（宁可漏算，不可错算）。
_GRID_ZONES = (
    ("南科", "南科 STSP"), ("台南科学园区", "南科 STSP"), ("STSP", "南科 STSP"),
    ("竹科", "竹科 HSP"), ("新竹科学园区", "竹科 HSP"), ("HSP", "竹科 HSP"),
    ("高雄", "高雄 KHH"), ("KHH", "高雄 KHH"),
    ("东北", "东北 TOHOKU"), ("TOHOKU", "东北 TOHOKU"),
)
_OUTAGE_KINDS = (
    ("全停", "blackout"), ("blackout", "blackout"),
    ("晃电", "sag"), ("压降", "sag"), ("电压骤降", "sag"), ("sag", "sag"),
    ("限电", "rationing"), ("rationing", "rationing"),
)

# natural_disaster 是粗类，按标题关键词细分；猜不出就返回 None，不硬猜
_NATURAL_KEYWORDS = (
    ("地震", "earthquake"), ("earthquake", "earthquake"), ("quake", "earthquake"),
    ("火山", "volcano"), ("volcano", "volcano"), ("eruption", "volcano"),
    ("洪水", "flood"), ("水灾", "flood"), ("海啸", "flood"), ("flood", "flood"),
    ("台风", "storm"), ("飓风", "storm"), ("暴雨", "storm"), ("风暴", "storm"),
    ("typhoon", "storm"), ("hurricane", "storm"), ("storm", "storm"),
    ("干旱", "drought"), ("drought", "drought"),
    ("山火", "wildfire"), ("野火", "wildfire"), ("森林火灾", "wildfire"), ("wildfire", "wildfire"),
    ("疫情", "epidemic"), ("传染病", "epidemic"), ("epidemic", "epidemic"),
    ("罢工", "strike"), ("strike", "strike"),
)

# 上游 LLM 会把「工厂爆炸」按后果判成 capacity_disruption（松下湖口厂 CCL 爆炸即一例）。
# 这类事故按标题关键词兜底识别，保证不会被漏推；山火/野火要排除，仍归 wildfire。
_FIRE_MARKERS = (
    "爆炸", "起火", "火灾", "火警", "焚毁", "blast", "explosion", "factory fire",
)
# 「需求爆炸式增长」「引爆市场」是比喻，不是事故；先把这些短语剔除再判火灾
_FIRE_FIGURES_OF_SPEECH = ("爆炸式", "爆炸性", "引爆", "爆款")
_WILDFIRE_MARKERS = ("山火", "野火", "森林火灾", "wildfire")

# 仅 --include-news 开启时使用（news kind 没有 type 字段，用标题前缀区分）
_LABELS = {
    "capacity_disruption": "产能中断", "mna": "并购", "earnings": "财报",
    "export_control": "出口管制", "litigation": "诉讼", "supply_shortage": "缺货涨价",
    "material_shortage": "材料短缺", "technology_release": "技术发布",
    "fire_explosion": "火灾爆炸", "power_outage": "停电限电",
    "natural_disaster": "自然灾害", "labor_dispute": "劳资纠纷",
    "geopolitical": "地缘政治", "water_shortage": "缺水", "other": "其他",
}


def _text(event):
    return " ".join(str(event.get(k) or "") for k in ("title_cn", "title", "site"))


def _is_fire_incident(event):
    """标题明确是火灾/爆炸事故（含被上游误标成产能中断的旧数据）。"""
    haystack = _text(event).lower()
    for phrase in _FIRE_FIGURES_OF_SPEECH:
        haystack = haystack.replace(phrase, "")
    return any(m in haystack for m in _FIRE_MARKERS)


def resolve_hazard_type(event):
    """返回 SEISMIQ 灾种；映射不了返回 None（不写 events）。"""
    primary = event.get("primary_event_type") or event.get("event_type")
    # 主类型优先：合簇后 primary 是「灾种」口径，先判它避免被原因类标签带偏
    types = [primary] + [t for t in (event.get("event_types") or []) if t != primary]
    haystack = _text(event).lower()

    for t in types:
        if t in _TYPE_MAP:
            return _TYPE_MAP[t]
        if t == "natural_disaster":
            for kw, hazard in _NATURAL_KEYWORDS:
                if kw.lower() in haystack:
                    return hazard
    # 兜底：标题明确是火灾/爆炸，但上游按「停产/供应链冲击」标成了经营类事件。
    # 只在前面没映射出任何灾种时启用，不会覆盖地震、罢工等明确分类。
    if _is_fire_incident(event):
        # 山火/野火不是厂区火灾，仍走 wildfire
        if any(m in haystack for m in _WILDFIRE_MARKERS):
            return "wildfire"
        return "plantfire"
    return None


def _severity_level(value):
    """jiclaw 0-1 浮点 -> SEISMIQ Red/Orange/Green。"""
    try:
        v = float(value or 0)
    except (TypeError, ValueError):
        v = 0.0
    if v >= 0.7:
        return "Red"
    if v >= 0.4:
        return "Orange"
    return "Green"


def _clip(value, limit=200):
    s = str(value or "").strip()
    return s[:limit]


def _event_title(event):
    """SEISMIQ 里标题要能一眼看出主体；LLM 抽取的标题常省略公司名。"""
    title = _clip(event.get("title_cn") or event.get("title"))
    entity = str(event.get("entity") or "").strip()
    # 泛称（旧数据里的「存储巨头」）补了也没信息量
    if not entity or any(m in entity for m in ("巨头", "大厂", "厂商", "业者", "某", "龙头")):
        return title
    short = re.split(r"[（(,，]", entity)[0].strip()
    if short and short not in title:
        return _clip(f"{short}｜{title}")
    return title


def _iso_date(value):
    """只传完整 YYYY-MM-DD；'2026-11' 这类年月不是合法 ISO 日期，宁可不传。"""
    s = str(value or "").strip()
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else None


def _stable_key(event):
    raw = event.get("dedup_key") or event.get("post_id") or event.get("url") or event.get("title")
    return f"{SOURCE_ID}:{raw}"


def _parse_coord(value, limit):
    """坐标只认合法数值，越界/非数字返回 None。"""
    try:
        v = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if not (-limit <= v <= limit):
        return None
    return round(v, 6)


def _coords_for(event, coords):
    """返回 (lat, lon)：人工核准的厂区坐标表优先，其次用抽取时写入的 LLM 坐标。

    坐标错会让影响算到错误的晶圆厂上，所以：
    1) --coords-file 里显式给出的人工坐标优先级最高；
    2) 其次才用事件里的 lat/lon（抽取层已做范围校验）；
    3) 两处都没有就返回 None，交给服务端走 region 模式，绝不推断。
    """
    if coords:
        site = str(event.get("site") or "")
        for key, pair in coords.items():
            if key and key in site:
                try:
                    lat, lon = float(pair[0]), float(pair[1])
                except (TypeError, ValueError, IndexError):
                    continue
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon

    lat = _parse_coord(event.get("lat"), 90)
    lon = _parse_coord(event.get("lon"), 180)
    if lat is not None and lon is not None:
        return lat, lon
    return None, None


def _power_fields(event):
    """type=power 的专有字段；识别不出就少填，让服务端 fail-closed。"""
    haystack = _text(event).lower()
    fields = {}
    for kw, zone in _GRID_ZONES:
        if kw.lower() in haystack:
            fields["grid_zone"] = zone
            break
    for kw, kind in _OUTAGE_KINDS:
        if kw.lower() in haystack:
            fields["outage_kind"] = kind
            break
    return fields


def is_actionable_risk(event, min_severity):
    """风险门槛：只让真正的风险事件进入后续映射，返回 (bool, 跳过原因)。

    latest.json 里混着财报、并购、长协等被上游标成 risk_event 的「正常新闻」，
    这里用 severity 做第二道闸；灾种枚举映射在 build_batches 里再判一次。
    """
    if event.get("is_risk_event") is False:
        return False, "非风险事件"
    try:
        sev = float(event.get("severity") or 0)
    except (TypeError, ValueError):
        sev = 0.0
    if sev < min_severity:
        return False, f"严重度 {sev} < 门槛 {min_severity}"
    return True, None


def build_event_payload(event, coords, confidence):
    hazard = resolve_hazard_type(event)
    if not hazard:
        return None

    title = _event_title(event)
    metric_note = ""
    if hazard == "plantfire":
        # 灾种已是「厂区火灾」，标题前缀便于在列表里快速识别，兼作人读标注
        title = _clip(f"[厂区火灾] {title}")
        metric_note = " | 事故: 厂区火灾/爆炸"

    payload = {
        "type": hazard,
        "title": title,
        "dedupe_key": _stable_key(event),
        "ext_id": event.get("post_id"),
        "source_id": SOURCE_ID,
        "place": _clip(event.get("site"), 100),
        "country": event.get("country_region"),
        "severity": _severity_level(event.get("severity")),
        "confidence": confidence,
        "url": event.get("url"),
        # 完整结构化快照留档：event_types / sources / affected_products 等
        "raw": json.dumps(event, ensure_ascii=False),
    }
    started_at = _iso_date(event.get("occurred_at"))
    if started_at:
        payload["started_at"] = started_at
    products = event.get("affected_products") or []
    metric = ""
    if products:
        metric = f"影响品类: {', '.join(map(str, products))} | 严重度 {event.get('severity')}"
    elif metric_note:
        metric = f"严重度 {event.get('severity')}"
    if metric:
        payload["metric"] = _clip(metric + metric_note, 100)

    lat, lon = _coords_for(event, coords)
    if lat is not None and lon is not None:
        payload["lat"] = lat
        payload["lon"] = lon

    if hazard == "power":
        payload.update(_power_fields(event))

    return {k: v for k, v in payload.items() if v is not None and v != ""}


def build_news_payload(event, confidence):
    primary = event.get("primary_event_type") or event.get("event_type") or "other"
    label = _LABELS.get(primary, primary)
    entities = [e for e in (event.get("entity"), event.get("site")) if e]
    entities += [str(p) for p in (event.get("affected_products") or [])][:4]

    payload = {
        "id": _stable_key(event),
        "title": _clip(f"[{label}] {event.get('title_cn') or event.get('title')}"),
        "url": event.get("url"),
        "published_at": event.get("published_at"),
        "entities": entities[:8],
        "rel": int(round(float(event.get("severity") or 0) * 100)),
        "confidence": confidence,
    }
    return {k: v for k, v in payload.items() if v is not None and v != "" and v != []}


def build_batches(events, coords, confidence, batch_size,
                  include_news=False, min_severity=DEFAULT_MIN_SEVERITY):
    """返回 (请求体列表, 跳过原因统计, 入选条数)。

    kv 分组在 schema 里形态有歧义，且 event.raw 已存了完整结构化快照，
    所以不往 SEISMIQ 的 kv 表写东西，避免污染对方数据。
    """
    items = []
    skipped = {}
    for event in events:
        ok, reason = is_actionable_risk(event, min_severity)
        if not ok:
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        ev = build_event_payload(event, coords, confidence)
        if ev:
            items.append(("events", ev))
        elif include_news:
            items.append(("news", build_news_payload(event, confidence)))
        else:
            key = "无对应 SEISMIQ 灾种（正常新闻不写入）"
            skipped[key] = skipped.get(key, 0) + 1

    bodies = []
    for start in range(0, len(items), batch_size):
        body = {"events": [], "news": []}
        for kind, payload in items[start:start + batch_size]:
            body[kind].append(payload)
        # 空数组不发，避免服务端把它当成有效分组
        body = {k: v for k, v in body.items() if v}
        if body:
            bodies.append(body)
    return bodies, skipped, len(items)


def load_feed(path):
    with open(path, encoding="utf-8") as f:
        feed = json.load(f)
    events = feed.get("events") or []
    if not isinstance(events, list):
        raise ValueError("latest.json 的 events 不是数组")
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feed", default="risk_events/latest.json")
    ap.add_argument("--url", default=os.environ.get("SEISMIQ_WEBHOOK_URL", WEBHOOK_URL))
    ap.add_argument("--token", default=os.environ.get("SEISMIQ_WEBHOOK_TOKEN"))
    ap.add_argument("--signing-secret", default=os.environ.get("SEISMIQ_HMAC_SECRET"),
                    help="仅当 SEISMIQ 侧开启 HMAC 校验时需要；对每批 raw body 做 HMAC-SHA256")
    ap.add_argument("--coords-file", help="厂区坐标表 JSON：{\"桃园厂\": [24.99, 121.22]}；缺省不传坐标")
    ap.add_argument("--confidence", choices=("fact", "assessed"), default="assessed",
                    help="LLM 抽取属于评估，默认 assessed（SEISMIQ 会把影响等级封顶为「关注」）")
    ap.add_argument("--batch-size", type=int, default=MAX_ITEMS_PER_REQUEST)
    ap.add_argument("--limit", type=int, default=0, help="只推前 N 条，用于小批试推")
    ap.add_argument("--min-severity", type=float, default=DEFAULT_MIN_SEVERITY,
                    help="风险门槛，低于此值视为正常新闻不写入（默认 0.4）")
    ap.add_argument("--include-news", action="store_true",
                    help="显式开启才写 news[]；默认关闭，正常新闻一律不进 SEISMIQ")
    ap.add_argument("--dry-run", action="store_true", help="只打印请求体，不发送")
    args = ap.parse_args()

    events = load_feed(args.feed)
    if args.limit > 0:
        events = events[:args.limit]
    print(f"读取 {args.feed}：{len(events)} 条事件")

    coords = {}
    if args.coords_file:
        with open(args.coords_file, encoding="utf-8") as f:
            coords = json.load(f)
        print(f"载入厂区坐标表：{len(coords)} 条")

    batches, skipped, kept = build_batches(
        events, coords, args.confidence, args.batch_size,
        include_news=args.include_news, min_severity=args.min_severity)
    n_ev = sum(len(b.get("events", [])) for b in batches)
    n_news = sum(len(b.get("news", [])) for b in batches)
    print(f"风险过滤：保留 {kept} 条 / 跳过 {len(events) - kept} 条"
          f"（门槛 severity >= {args.min_severity}，news 通道"
          f"{'已开启' if args.include_news else '默认关闭'}）")
    for reason, n in sorted(skipped.items(), key=lambda x: -x[1]):
        print(f"  跳过 {n} 条：{reason}")
    print(f"映射结果：event {n_ev} 条 / news {n_news} 条，分 {len(batches)} 批")

    if args.dry_run:
        for i, body in enumerate(batches, 1):
            print(f"\n===== 第 {i} 批 payload =====")
            print(json.dumps(body, ensure_ascii=False, indent=2))
        print("\n[dry-run] 未发送任何请求")
        return 0

    if not batches:
        print("没有符合风险门槛且能映射灾种的事件，跳过写入")
        return 0

    if not args.token:
        print("错误：未提供主 token。请设置环境变量 SEISMIQ_WEBHOOK_TOKEN "
              "（本仓库 GitHub Secret 名为 SEISMIQ_WEBHOOK_SECRET）。", file=sys.stderr)
        return 2

    headers = {
        "Authorization": f"Bearer {args.token}",
        "Content-Type": "application/json",
        "X-Webhook-Source": SOURCE_ID,
    }
    failed = 0
    for i, body in enumerate(batches, 1):
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        batch_headers = dict(headers)
        if args.signing_secret:
            sig = hmac.new(args.signing_secret.encode("utf-8"), raw_body,
                           hashlib.sha256).hexdigest()
            batch_headers["X-Webhook-Signature"] = f"sha256={sig}"
        try:
            resp = requests.post(args.url, headers=batch_headers,
                                 data=raw_body, timeout=30)
        except requests.RequestException as e:
            print(f"第 {i} 批请求异常：{type(e).__name__}: {str(e)[:200]}")
            failed += 1
            continue
        print(f"第 {i} 批 HTTP {resp.status_code}：{resp.text[:800]}")
        if resp.status_code >= 300:
            failed += 1
            continue
        try:
            data = resp.json()
            if data.get("ok") is False:
                print(f"  服务端返回 ok=false：{json.dumps(data, ensure_ascii=False)[:500]}")
                failed += 1
        except ValueError:
            pass

    if failed:
        print(f"推送完成：{len(batches) - failed}/{len(batches)} 批成功，{failed} 批失败", file=sys.stderr)
        return 1
    print(f"推送完成：{len(batches)}/{len(batches)} 批成功 @ "
          f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

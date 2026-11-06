#!/usr/bin/env python3
"""把 risk_events/latest.json 推送到 SEISMIQ webhook。

分流依据 SEISMIQ 的 GET /api/webhook/schema：

* ``events[]`` 是「有地理影响的物理/社会事件」，``type`` 必须是封闭枚举
  (earthquake/wildfire/volcano/flood/storm/drought/epidemic/strike/conflict)，
  写入后会立即重算「事件 × 晶圆厂」影响。只有能真实映射灾种的事件才放进来。
* ``news[]`` 是资讯。产业经营类事件（财报、并购、扩产延期、出口管制…）走这里，
  硬塞进 events 会因灾种不匹配写不进去，或误触发影响计算。

没有经纬度时**不编坐标**：不传 lat/lon，让 SEISMIQ 走 region 模式（按 country 匹配）。
需要 distance 模式时，用 --coords-file 提供一份显式、准确的厂区坐标表。

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

# 直接映射：jiclaw event_type -> SEISMIQ 灾种
_TYPE_MAP = {
    "labor_dispute": "strike",       # 劳资纠纷/罢工
    "geopolitical": "conflict",      # 地缘政治/冲突
    "water_shortage": "drought",     # 供水短缺，最接近干旱
}

# natural_disaster 是粗类，按标题关键词细分；猜不出就返回 None 走 news，不硬猜
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

# news 标题前缀，便于在 SEISMIQ 里一眼区分类型（news kind 没有 type 字段）
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


def resolve_hazard_type(event):
    """返回 SEISMIQ 灾种；映射不了返回 None（改走 news）。"""
    primary = event.get("primary_event_type") or event.get("event_type")
    types = list(event.get("event_types") or []) + [primary]
    haystack = _text(event).lower()

    for t in types:
        if t in _TYPE_MAP:
            return _TYPE_MAP[t]
        if t == "natural_disaster":
            for kw, hazard in _NATURAL_KEYWORDS:
                if kw.lower() in haystack:
                    return hazard
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


def _coords_for(event, coords):
    """只在用户提供显式坐标表时返回 (lat, lon)，绝不推断。"""
    if not coords:
        return None, None
    site = str(event.get("site") or "")
    for key, pair in coords.items():
        if key and key in site:
            try:
                lat, lon = float(pair[0]), float(pair[1])
                if -90 <= lat <= 90 and -180 <= lon <= 180:
                    return lat, lon
            except (TypeError, ValueError, IndexError):
                continue
    return None, None


def build_event_payload(event, coords, confidence):
    hazard = resolve_hazard_type(event)
    if not hazard:
        return None

    payload = {
        "type": hazard,
        "title": _event_title(event),
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
    if products:
        payload["metric"] = _clip(
            f"影响品类: {', '.join(map(str, products))} | 严重度 {event.get('severity')}", 100)

    lat, lon = _coords_for(event, coords)
    if lat is not None and lon is not None:
        payload["lat"] = lat
        payload["lon"] = lon

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


def build_batches(events, coords, confidence, batch_size):
    """返回若干 {'events': [...], 'news': [...]} 请求体。

    kv 分组在 schema 里形态有歧义，且 event.raw 已存了完整结构化快照，
    所以不往 SEISMIQ 的 kv 表写东西，避免污染对方数据。
    """
    items = []
    for event in events:
        ev = build_event_payload(event, coords, confidence)
        if ev:
            items.append(("events", ev))
        else:
            items.append(("news", build_news_payload(event, confidence)))

    for start in range(0, len(items), batch_size):
        body = {"events": [], "news": []}
        for kind, payload in items[start:start + batch_size]:
            body[kind].append(payload)
        # 空数组不发，避免服务端把它当成有效分组
        yield {k: v for k, v in body.items() if v}


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
    ap.add_argument("--signing-secret", default=os.environ.get("SEISMIQ_WEBHOOK_SECRET"),
                    help="仅当 SEISMIQ 配置了 WEBHOOK_SECRET 时需要；对每批 raw body 做 HMAC-SHA256")
    ap.add_argument("--coords-file", help="厂区坐标表 JSON：{\"桃园厂\": [24.99, 121.22]}；缺省不传坐标")
    ap.add_argument("--confidence", choices=("fact", "assessed"), default="assessed",
                    help="LLM 抽取属于评估，默认 assessed（SEISMIQ 会把影响等级封顶为「关注」）")
    ap.add_argument("--batch-size", type=int, default=MAX_ITEMS_PER_REQUEST)
    ap.add_argument("--limit", type=int, default=0, help="只推前 N 条，用于小批试推")
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

    batches = list(build_batches(events, coords, args.confidence, args.batch_size))
    n_ev = sum(len(b.get("events", [])) for b in batches)
    n_news = sum(len(b.get("news", [])) for b in batches)
    print(f"映射结果：event {n_ev} 条 / news {n_news} 条，分 {len(batches)} 批")

    if args.dry_run:
        for i, body in enumerate(batches, 1):
            print(f"\n===== 第 {i} 批 payload =====")
            print(json.dumps(body, ensure_ascii=False, indent=2))
        print("\n[dry-run] 未发送任何请求")
        return 0

    if not args.token:
        print("错误：未提供 token。请设置环境变量 SEISMIQ_WEBHOOK_TOKEN 或 GitHub Secret。",
              file=sys.stderr)
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
        print(f"第 {i} 批 HTTP {resp.status_code}：{resp.text[:500]}")
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

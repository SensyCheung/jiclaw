#!/usr/bin/env python3
"""把按天分片的 risk_events-*.jsonl 汇总成下游可直接拉取的 latest.json。

设计目标：
1. 一个 URL 拿到全部事件 —— SEISMIQ 之类下游平台不必知道分片规则，也不用碰
   GitHub Actions 的 artifact（artifact 会过期、下载还要鉴权）。
2. 多源同题归并 —— 同一事件被多家媒体报道时（美光停建曾出现两条），
   合并成一条，来源 URL 全部保留在 sources 里，避免风险计数被放大。
3. 幂等 —— events 内容没变就不重写 latest.json（否则工作流每小时会产生一次
   只有 generated_at 不同的空提交）。

用法：
    python export_risk_feed.py                 # 默认 risk_events/
    python export_risk_feed.py --dir /tmp/re  # 指定目录（本地调试用）
"""

import argparse
import glob
import json
import os
import re
from datetime import datetime, timedelta, timezone

MAX_EVENTS = 500       # latest.json 最多保留的事件数，防止无限膨胀
MAX_AGE_DAYS = 90      # 只保留最近 N 天的事件（按 published_at / ingested_at 判断）
OUTPUT_NAME = "latest.json"


_GENERIC_MARKERS = ("巨头", "大厂", "厂商", "业者", "龙头", "某", "制造商", "供应商")

# 火灾/爆炸是「事故类型」，上游 LLM 常按后果标成 capacity_disruption。
# 合簇后统一把这类事件的主类型归一为 fire_explosion，原始类型仍留在 event_types。
_FIRE_TITLE_MARKERS = ("爆炸", "起火", "火灾", "火警", "焚毁",
                       "blast", "explosion", "factory fire")
_FIRE_FIGURES_OF_SPEECH = ("爆炸式", "爆炸性", "引爆", "爆款")
# 山火/野火不是厂区火灾，保持 natural_disaster 口径，不要归一成 fire_explosion
_WILDFIRE_TITLE_MARKERS = ("山火", "野火", "森林火灾", "wildfire")


def _is_generic_entity(value):
    """识别「存储巨头」「某晶圆厂」这类泛称（旧版 prompt 会抽出来）。"""
    return bool(value) and any(m in str(value) for m in _GENERIC_MARKERS)


def _is_fire_title(rec):
    """标题命中厂区火灾/爆炸；用于把被误标成经营类的事件归一回火灾口径。"""
    text = " ".join(str(rec.get(k) or "") for k in ("title_cn", "title", "site")).lower()
    for phrase in _FIRE_FIGURES_OF_SPEECH:
        text = text.replace(phrase, "")
    if any(m in text for m in _WILDFIRE_TITLE_MARKERS):
        return False
    return any(m in text for m in _FIRE_TITLE_MARKERS)


def _norm_site(value):
    if not value:
        return ""
    s = re.sub(r"[（(].*?[)）]", "", str(value))
    return re.sub(r"\s+", "", s).strip().lower()


def _norm_entity(value):
    """实体名归一化，用于宽松归并 '美光' / '美光科技(Micron)' 这类写法差异。

    只做保守清洗：去括号内容、去常见公司后缀、小写。不做模糊匹配，避免把
    '台积电' 和 '台积电美国子公司' 之外的不相干主体硬凑到一起。
    """
    if not value:
        return ""
    s = str(value).split(",")[0].split("，")[0]  # 多主体时只取第一个
    s = re.sub(r"[（(].*?[)）]", "", s)
    s = re.sub(r"\s+", "", s)
    for suffix in ("股份有限公司", "有限公司", "集团公司", "公司", "科技", "半导体",
                   "Corporation", "Incorporated", "Inc.", "Inc", "Corp", "Ltd", "Co."):
        s = s.replace(suffix, "")
    return s.strip().lower()


# 同一事件常被判成不同 event_type（美光那条：一个源报「劳资纠纷」，另一个报「停工」）。
# 风险平台关心的是影响，所以合簇后主类型取「结果」类，原因保留在 event_types 里。
_EVENT_PRIORITY = {
    "capacity_disruption": 90, "natural_disaster": 85, "fire_explosion": 85,
    "power_outage": 80, "export_control": 78, "supply_shortage": 72,
    "labor_dispute": 70, "material_shortage": 70, "water_shortage": 70,
    "geopolitical": 65, "litigation": 60, "mna": 50, "earnings": 40,
    "technology_release": 20, "other": 10,
}


def _same_cluster(a, b):
    """判断两条记录是否同一个事件簇。

    1) jiclaw_core 写出的 dedup_key 相同 —— 最可靠。
    2) 主体归一后相同/互相包含 + 发生日期相同 + 灾种相同 —— 兜宽松归并。
       放宽 event_type 是刻意的：劳资纠纷(因) 与 停工(果) 在下游应是同一事件的两个标签。
       但 entity 为空时绝不合并，避免把同一天不同公司的事件揉成一团。
    """
    if a.get("dedup_key") and a.get("dedup_key") == b.get("dedup_key"):
        return True

    # 下面两条兜底规则都要求同日期、同灾种，避免把不同时间的事件揉成一团
    if a.get("occurred_at") != b.get("occurred_at") or not a.get("occurred_at"):
        return False
    if a.get("hazard_class") != b.get("hazard_class"):
        return False

    # 2) 主体名归一后互相包含（「美光」vs「美光科技(Micron)」）
    ea, eb = _norm_entity(a.get("entity")), _norm_entity(b.get("entity"))
    if len(ea) >= 2 and len(eb) >= 2 and (ea in eb or eb in ea):
        return True

    # 3) 旧数据里 entity 是泛称、新数据是真名时，实体名对不上；
    #    此时若厂区一致且至少一侧是泛称，按厂区补救合并。
    sa, sb = _norm_site(a.get("site")), _norm_site(b.get("site"))
    if sa and sb and (sa in sb or sb in sa):
        if _is_generic_entity(a.get("entity")) or _is_generic_entity(b.get("entity")):
            return True
    return False


def _event_rank(rec):
    return _EVENT_PRIORITY.get(rec.get("event_type"), 0)


def _source_of(rec):
    return {
        "post_id": rec.get("post_id"),
        "url": rec.get("url"),
        "title": rec.get("title"),
        "title_cn": rec.get("title_cn"),
        "source": rec.get("source"),
        "published_at": rec.get("published_at"),
        "llm": rec.get("llm"),
    }


def load_events(base):
    """读取所有分片，坏行跳过但不中断（CI 里宁可少一条也不要整个流程挂掉）。"""
    rows = []
    paths = sorted(glob.glob(os.path.join(base, "risk_events-*.jsonl")))
    for path in paths:
        with open(path, encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"跳过坏行 {os.path.basename(path)}:{lineno} -> {e}")
                    continue
                if isinstance(rec, dict) and rec.get("is_risk_event"):
                    rec["_shard"] = os.path.basename(path)
                    rows.append(rec)
    print(f"读取 {len(paths)} 个分片，共 {len(rows)} 条风险事件")
    return rows


def merge_events(rows):
    """按事件簇合并，保留全部分来源，severity 取簇内最高。"""
    clusters = []
    for rec in rows:
        target = None
        for cl in clusters:
            if _same_cluster(cl, rec):
                target = cl
                break
        if target is None:
            merged = dict(rec)
            merged.pop("_shard", None)
            merged["sources"] = [_source_of(rec)]
            merged["event_types"] = [rec.get("event_type")] if rec.get("event_type") else []
            clusters.append(merged)
            continue

        for et in [rec.get("event_type")]:
            if et and et not in target["event_types"]:
                target["event_types"].append(et)
        # 结果类优先于原因类：capacity_disruption 盖过 labor_dispute
        if _event_rank(rec) > _event_rank(target):
            target["event_type"] = rec.get("event_type")
            target["primary_event_type"] = rec.get("event_type")
        seen = {s.get("url") for s in target["sources"]}
        if rec.get("url") not in seen:
            target["sources"].append(_source_of(rec))
        try:
            if float(rec.get("severity") or 0) > float(target.get("severity") or 0):
                target["severity"] = rec["severity"]
        except (TypeError, ValueError):
            pass
        # 主标题/主实体取更具体的那个：真名优先于泛称，其次取更长的名称
        cur = str(target.get("entity") or "")
        new = str(rec.get("entity") or "")
        cur_g, new_g = _is_generic_entity(cur), _is_generic_entity(new)
        better = (cur_g and not new_g) or (
            cur_g == new_g and len(_norm_entity(new)) > len(_norm_entity(cur))
        )
        if new and (not cur or better):
            target["entity"] = new
            target["title_cn"] = rec.get("title_cn") or target.get("title_cn")
            target["url"] = rec.get("url") or target.get("url")
        for key in ("site", "country_region", "lat", "lon", "occurred_at", "affected_products"):
            if not target.get(key) and rec.get(key):
                target[key] = rec[key]

    for cl in clusters:
        cl["source_count"] = len(cl["sources"])
        cl.setdefault("primary_event_type", cl.get("event_type"))
        if _is_fire_title(cl):
            # 灾种口径优先于经营后果口径：即使模型判成「产能中断」，下游也应当作
            # 火灾事故处理；原始类型完整保留在 event_types / sources 里可追溯。
            ets = cl.setdefault("event_types", [])
            if "fire_explosion" not in ets:
                ets.append("fire_explosion")
            cl["primary_event_type"] = "fire_explosion"
        cl["event_types"] = sorted(
            cl.get("event_types") or [],
            key=lambda t: -_EVENT_PRIORITY.get(t, 0),
        )
    return clusters


def _freshness(rec):
    return str(rec.get("published_at") or rec.get("ingested_at") or "")[:10]


def build_feed(base):
    rows = load_events(base)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    rows = [r for r in rows if not _freshness(r) or _freshness(r) >= cutoff]

    events = merge_events(rows)
    # 最近发生优先；同日则严重度高的在前；occurred_at 为空的沉底
    events.sort(key=lambda e: (
        e.get("occurred_at") or "",
        float(e.get("severity") or 0),
    ), reverse=True)
    events = events[:MAX_EVENTS]
    print(f"归并后 {len(events)} 条事件（原始 {len(rows)} 条）")
    return {"events": events}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.environ.get("RISK_EVENTS_DIR", "risk_events"),
                    help="risk_events 分片目录")
    args = ap.parse_args()

    base = args.dir
    os.makedirs(base, exist_ok=True)
    out_path = os.path.join(base, OUTPUT_NAME)

    feed = build_feed(base)

    # 幂等：比较除 generated_at 外的整个输出（包括 schema 元数据），
    # 只比 events 会导致以后升级 schema / 改裁剪策略时 latest.json 永远不刷新。
    def comparable(obj):
        return {k: v for k, v in obj.items() if k != "generated_at"}

    old_payload = None
    if os.path.exists(out_path):
        try:
            with open(out_path, encoding="utf-8") as f:
                old_payload = json.load(f)
        except (OSError, json.JSONDecodeError):
            old_payload = None

    payload = {
        "schema_version": 2,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(feed["events"]),
        "feed": {
            "max_age_days": MAX_AGE_DAYS,
            "max_events": MAX_EVENTS,
            "dedup": "entity_norm + occurred_at + hazard_class, site fallback for generic entity",
            "sorted_by": "occurred_at desc, severity desc",
        },
        "dedup_note": "同簇事件已合并，全部原始来源见 sources；source_count 为报道源数",
        "events": feed["events"],
    }

    if isinstance(old_payload, dict) and comparable(old_payload) == comparable(payload):
        print("事件内容无变化，保留原 latest.json（不产生提交）")
        return 0

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print(f"已写出 {out_path}（{payload['count']} 条，schema v{payload['schema_version']}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

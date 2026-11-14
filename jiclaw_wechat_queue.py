"""jiclaw_wechat_queue.py - 把文章登记成公众号候选。

配置分两层：
1. wechat_post_config.json（总开关 + 兜底白名单）：collect_enabled /
   publish_enabled / max_per_run / max_items，以及 rules 缺失时的 sites。
2. article_rules.json（单一权威）：每个来源画像的 queue.mode、
   min_image_count、exempt_max_per_run、template、auto_publish。
   rules_are_authoritative=true 时以它为准，sites 只作兜底。

队列在 risk_events/wechat_queue.json（程序生成，由 workflow 的 commit 步骤
回写仓库）。WorkBuddy 经 semivoice.com/api/wechat-queue 消费队列，用本地
processed.json 去重，所以队列里的 status 一律是 pending，仓库侧不回写状态。

入队模式（来自 article_rules.json → profiles[].queue.mode）：
- all            不看图片数，所有新文章入队（imec、Electronics Weekly）；
                 exempt_max_per_run=true 时不受单次上限约束。
- image_threshold 正文配图数 >= min_image_count 才入队。
- manual         不自动入队，只在用户手工给 URL 时由 WorkBuddy 直接生成。

免图全量必须能豁免 max_per_run：_process_items 里已存在于 Notion 的文章
会被直接 continue，本轮没登记的文章下一轮不会再出现，一旦被上限挡住就
永久漏稿。

这里不发任何网络请求：配图数来自抓正文时缓存的 image_count，
没抓过正文的源是 0，但 mode=all 不受影响。
"""

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse


CONFIG_PATH = os.environ.get("WECHAT_POST_CONFIG", "wechat_post_config.json")
QUEUE_PATH = os.environ.get("WECHAT_POST_QUEUE", "risk_events/wechat_queue.json")
RULES_PATH = os.environ.get("WECHAT_ARTICLE_RULES", None)

DEFAULT_CONFIG = {
    "collect_enabled": True,
    "publish_enabled": False,
    "min_image_count": 4,
    "rules_are_authoritative": True,
    "article_rules_file": "article_rules.json",
    "sites": ["semiwiki.com"],
    "always_collect_sites": [],
    "max_per_run": 2,
    "max_items": 200,
}

SCHEMA_VERSION = 2

# 单次进程内累计新增数，跨 record_candidate 多次调用生效。
_ADDED_THIS_RUN = 0


def load_config(path=CONFIG_PATH):
    """读配置；文件损坏时返回 None，宁可本轮不登记也不要误触发。"""
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(path, encoding="utf-8") as f:
            user = json.load(f)
        if isinstance(user, dict):
            cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
        return cfg
    except FileNotFoundError:
        print(f"[贴图队列] 未找到配置 {path}，使用默认值（改阈值请先 push 该文件）")
        return cfg
    except (OSError, json.JSONDecodeError) as e:
        print(f"[贴图队列] 配置读取失败，本轮跳过登记：{str(e)[:100]}")
        return None


def load_rules(cfg, path=None):
    """读来源规则。缺失或损坏时返回 None，自动退回旧的白名单逻辑。"""
    p = path or RULES_PATH or cfg.get("article_rules_file") or "article_rules.json"
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("profiles"), list):
            return data
    except (OSError, json.JSONDecodeError) as e:
        print(f"[贴图队列] 规则文件 {p} 不可读，退回白名单逻辑：{str(e)[:100]}")
    return None


def _host(url):
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _site_allowed(host, sites):
    allowed = [str(s).lower().lstrip(".").replace("www.", "") for s in (sites or [])]
    return bool(host) and any(host == s or host.endswith("." + s) for s in allowed)


def _profile_for(host, rules):
    """按 host 后缀匹配来源画像，未命中用 hosts=['*'] 的 default。"""
    if not rules:
        return None
    fallback = None
    for p in rules.get("profiles", []):
        hs = p.get("hosts") or []
        if "*" in hs:
            fallback = p
        for s in hs:
            if s == "*":
                continue
            s = str(s).lower().lstrip(".").replace("www.", "")
            if host == s or host.endswith("." + s):
                return p
    return fallback


def _low_image_override(profile, title):
    """命中访谈类标题时返回放宽后的阈值与模板，否则返回 None。"""
    ov = ((profile or {}).get("queue") or {}).get("low_image_override") or {}
    if not ov:
        return None
    lowered = (title or "").lower()
    if any(str(k).lower() in lowered for k in (ov.get("title_keywords") or [])):
        return ov
    return None


def _template_for(profile, image_count, title):
    """按画像的 routing 决定这一篇用哪个模板。"""
    if not profile:
        return None
    ov = _low_image_override(profile, title)
    if ov and ov.get("template"):
        return ov["template"]
    base = profile.get("template")
    routing = profile.get("routing") or {}
    lowered = (title or "").lower()
    kws = routing.get("interview_keywords") or []
    if kws and any(str(k).lower() in lowered for k in kws):
        return routing.get("interview_template") or base
    if routing.get("always"):
        return routing["always"]
    gte = routing.get("image_count_gte")
    if gte is not None:
        if int(image_count or 0) >= int(gte):
            return routing.get("then") or base
        return routing.get("else") or profile.get("template_fallback") or base
    return base


def _canon(url):
    """URL 去重键：忽略 scheme、www.、末尾斜杠与 fragment，保留 path/query。

    imec 抓取器产出 https://www.imec-int.com/...，人工登记或下游回写的
    可能是 https://imec-int.com/...；不做归一化会把同一篇登记两次。
    """
    p = urlparse(url or "")
    path = (p.path or "/").rstrip("/") or "/"
    return _host(url) + path + (("?" + p.query) if p.query else "")


def _load_queue(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"schema_version": SCHEMA_VERSION, "items": []}


def record_candidate(url, title, image_count,
                     config_path=CONFIG_PATH, queue_path=QUEUE_PATH,
                     rules_path=None):
    """命中来源画像就往队列追加一条；按 URL 去重。

    rules_are_authoritative 且规则文件可读时，入队条件完全由画像的
    queue.mode 决定；否则退回旧的 sites / always_collect_sites / 阈值逻辑。
    """
    global _ADDED_THIS_RUN

    cfg = load_config(config_path)
    if not cfg or not cfg.get("collect_enabled"):
        return False

    try:
        n = int(image_count or 0)
        min_n = int(cfg.get("min_image_count") or 0)
        max_run = int(cfg.get("max_per_run") or 0)
        max_items = int(cfg.get("max_items") or 200)
    except (TypeError, ValueError):
        return False

    host = _host(url)
    rules = load_rules(cfg, rules_path)
    authoritative = bool(cfg.get("rules_are_authoritative")) and bool(rules)
    profile = _profile_for(host, rules) if rules else None

    if authoritative:
        q = (profile or {}).get("queue") or {}
        if not q.get("enabled"):
            return False
        mode = q.get("mode") or "manual"
        if mode == "manual":
            return False
        min_n = int(q.get("min_image_count") or min_n or 0)
        ov = _low_image_override(profile, title)
        if ov:
            # 访谈稿只有 1 张封面也放行，但仍要占 max_per_run 名额。
            min_n = int(ov.get("min_image_count") or 1)
        always = mode == "all"
        exempt = bool(q.get("exempt_max_per_run"))
        if mode != "all" and (min_n <= 0 or n < min_n):
            return False
        # mode=all 但没豁免上限的源仍要占位，避免一轮涌进太多短讯。
        if not (always and exempt) and max_run > 0 and _ADDED_THIS_RUN >= max_run:
            return False
    else:
        # 旧逻辑：规则文件缺失时的兜底，保证队列不会因为规则没 push 就停摆。
        if not _site_allowed(host, cfg.get("sites")):
            return False
        always = _site_allowed(host, cfg.get("always_collect_sites"))
        if not always:
            if min_n <= 0 or n < min_n:
                return False
            if max_run > 0 and _ADDED_THIS_RUN >= max_run:
                return False
        exempt = always

    data = _load_queue(queue_path)
    items = data["items"]
    key = _canon(url)
    if any(isinstance(i, dict) and _canon(i.get("url")) == key for i in items):
        return False

    item = {
        "url": url,
        "title": str(title or "")[:200],
        "image_count": n,
        "site": host,
        "always_publish": always,
        "status": "pending",
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if profile:
        item["profile_id"] = profile.get("id")
        item["template"] = _template_for(profile, n, title)
        item["auto_publish"] = bool(profile.get("auto_publish", True))
    items.append(item)
    if len(items) > max_items:
        items[:] = items[-max_items:]

    data["schema_version"] = SCHEMA_VERSION
    data["generated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    data["count"] = len(items)
    data["_note"] = "status 由 WorkBuddy 本地 processed.json 判重，仓库内不回写状态"
    data["config_snapshot"] = {
        "publish_enabled": bool(cfg.get("publish_enabled")),
        "min_image_count": min_n,
        "sites": cfg.get("sites"),
        "always_collect_sites": cfg.get("always_collect_sites"),
        "rules_version": (rules or {}).get("schema_version"),
        "rules_authoritative": authoritative,
    }

    os.makedirs(os.path.dirname(queue_path) or ".", exist_ok=True)
    tmp = queue_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, queue_path)

    if not (always and exempt):
        _ADDED_THIS_RUN += 1
    tag = "免图全量" if always else f"{n} 图"
    who = (profile or {}).get("id", "legacy")
    print(f"[贴图队列] 登记候选：{tag} · {who} · {str(title)[:40]}")
    return True

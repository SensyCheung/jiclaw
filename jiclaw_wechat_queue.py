"""jiclaw_wechat_queue.py - 把「正文配图够多」的文章登记成公众号贴图候选。

配置在 wechat_post_config.json（人写、程序只读），队列在
risk_events/wechat_queue.json（程序生成，由 workflow 的 commit 步骤回写仓库）。
WorkBuddy 每小时 GET 仓库 raw URL 消费队列，用本地 processed.json 去重，
所以队列里的 status 一律是 pending，仓库侧不回写状态。

这里不发任何网络请求：配图数来自抓正文时缓存的 image_count，
没抓过正文的源是 0，自然不满足阈值。
"""

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse


CONFIG_PATH = os.environ.get("WECHAT_POST_CONFIG", "wechat_post_config.json")
QUEUE_PATH = os.environ.get("WECHAT_POST_QUEUE", "risk_events/wechat_queue.json")

DEFAULT_CONFIG = {
    "collect_enabled": True,
    "publish_enabled": False,
    "min_image_count": 4,
    "sites": ["semiwiki.com"],
    "max_per_run": 2,
    "max_items": 200,
}

SCHEMA_VERSION = 1

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


def _host(url):
    host = (urlparse(url or "").netloc or "").lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _site_allowed(host, sites):
    allowed = [str(s).lower().lstrip(".") for s in (sites or [])]
    return bool(host) and any(host == s or host.endswith("." + s) for s in allowed)


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
                     config_path=CONFIG_PATH, queue_path=QUEUE_PATH):
    """命中「站点白名单 + 图片数 >= 阈值」就往队列追加一条；按 URL 去重。"""
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

    if min_n <= 0 or n < min_n:
        return False
    host = _host(url)
    if not _site_allowed(host, cfg.get("sites")):
        return False
    if max_run > 0 and _ADDED_THIS_RUN >= max_run:
        return False

    data = _load_queue(queue_path)
    items = data["items"]
    if any(isinstance(i, dict) and i.get("url") == url for i in items):
        return False

    items.append({
        "url": url,
        "title": str(title or "")[:200],
        "image_count": n,
        "site": host,
        "status": "pending",
        "discovered_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
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
    }

    os.makedirs(os.path.dirname(queue_path) or ".", exist_ok=True)
    tmp = queue_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, queue_path)

    _ADDED_THIS_RUN += 1
    print(f"[贴图队列] 登记候选：{n} 图 · {str(title)[:40]}")
    return True

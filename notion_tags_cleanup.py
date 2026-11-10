#!/usr/bin/env python3
"""notion_tags_cleanup.py - 收敛 Notion 数据库 tags (multi_select) 选项。

背景
----
``tags`` 是 multi_select：写入一个库里不存在的 name，Notion 会自动新建一个
select 选项。旧 prompt 允许 LLM「列表没有合适的就提取正文公司名」，长期累积出
几千个选项，把 database schema 撑到约 499KB（官方建议 ≤50KB），新文章写入时
返回 400 ``database schema has exceeded the maximum size``，整条页面被拒。

Notion 2022-06-28 的官方行为（Update property schema 文档）::

    "Set multi_select.options using the same rules as select. Include every
     option to retain. Omitted options are removed. ... This changes the
     available tags, not the tags selected on one page."

即 PATCH database 传「要保留」的 option id 列表，未列出的选项会被移除；
**页面上已经选中的标签值不受影响**，只是不再作为可选项出现，历史标签不会丢。

保留策略
--------
1. 固定词表 ``jiclaw_core.CANDIDATE_TAGS`` 里的规范名（写库前已硬过滤到这个词表）；
2. 词表的历史大小写变体（仅在仍被页面引用时保留，避免历史值成为幽灵选项）；
3. 引用次数 ≥ ``--keep-min`` 的高频历史标签（保住常用筛选视图）；
其余一律移除。apply 时还会补齐词表里缺失的规范名，之后写入永远命中已有选项。

Notion PATCH database 的 ``options`` 数组**最多 100 项**，而它是「完整保留列表」，
所以清理后的选项总数必然 ≤ 100。``--keep-min`` 会在必要时自动提高，使
「保留 + 新增」不超上限：例如 keep-min=1（只删零引用、保留 405 项）会被自动抬到
实际可行的最小阈值。这也是为什么想保住所有历史引用标签在 API 上做不到。

用法（需要 NOTION_API_KEY / NOTION_DATABASE_ID）::

    python notion_tags_cleanup.py --mode probe     # 只读统计 + 备份完整选项清单
    python notion_tags_cleanup.py --mode dry-run   # 预演保留/移除范围，不写
    python notion_tags_cleanup.py --mode apply     # 真正收敛选项（自动先备份）

线上通过 workflow_dispatch 的 ``notion_tags`` 输入触发，密钥由 Actions Secrets
注入；完整选项清单写成本地 JSON 并由 workflow 上传为 artifact 留档。
"""

import argparse
import collections
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests

from jiclaw_core import CANDIDATE_TAGS, _norm_tag

NOTION_VERSION = "2022-06-28"
BASE = "https://api.notion.com"
PAGE_SIZE = 100
TAG_PROP = "tags"
BACKUP_FILE = "tags_options_backup.json"
# Notion PATCH database 的 body.properties.tags.multi_select.options 最多 100 项，
# 而该数组是「完整保留列表」（未列出的会被移除），所以清理后选项总数必然 ≤ 100。
# 库里能累积到几千个，是因为页面写入时自动新建选项不走这条校验。
MAX_PATCH_OPTIONS = 100


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def fetch_database(token, db_id):
    resp = requests.get(f"{BASE}/v1/databases/{db_id}", headers=_headers(token), timeout=30)
    if resp.status_code >= 300:
        raise RuntimeError(f"[schema] 查询失败 HTTP {resp.status_code}: {resp.text[:600]}")
    return resp.json()


def iter_pages(token, db_id):
    """分页拉取数据库全部页面，用于统计每个选项被多少页面引用。"""
    cursor, total = None, 0
    while True:
        payload = {"page_size": PAGE_SIZE}
        if cursor:
            payload["start_cursor"] = cursor
        resp = requests.post(
            f"{BASE}/v1/databases/{db_id}/query",
            headers=_headers(token),
            data=json.dumps(payload),
            timeout=40,
        )
        if resp.status_code >= 300:
            raise RuntimeError(f"[query] 查询失败 HTTP {resp.status_code}: {resp.text[:600]}")
        data = resp.json()
        rows = data.get("results", [])
        total += len(rows)
        for row in rows:
            yield row
        if not data.get("has_more"):
            print(f"[query] 分页读取完成，共 {total} 条页面")
            break
        cursor = data.get("next_cursor")
        time.sleep(0.35)


def page_tags(page):
    prop = (page.get("properties") or {}).get(TAG_PROP) or {}
    return [(m.get("id"), m.get("name") or "")
            for m in (prop.get("multi_select") or []) if isinstance(m, dict)]


def _byte_size(obj):
    return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def collect_usage(rows, options):
    """返回 (usage_by_id, usage_by_norm, orphan_names)。"""
    known_ids = {o.get("id") for o in options}
    usage_id = collections.Counter()
    usage_norm = collections.Counter()
    orphan = collections.Counter()
    for page in rows:
        for opt_id, name in page_tags(page):
            norm = _norm_tag(name)
            if opt_id:
                usage_id[opt_id] += 1
            if norm:
                usage_norm[norm] += 1
            if opt_id and opt_id not in known_ids:
                orphan[name] += 1
    return usage_id, usage_norm, orphan


def build_plan(options, usage_id, usage_norm, keep_min):
    """给出 (保留的 option 列表, 移除的 option 列表, 需新增的规范名, 原因分布)。"""
    vocab = {_norm_tag(t): t for t in CANDIDATE_TAGS}
    exact_names = {o.get("name") for o in options}
    keep, remove, reasons = [], [], collections.Counter()

    for opt in options:
        name = str(opt.get("name") or "")
        norm = _norm_tag(name)
        uses = usage_id.get(opt.get("id"), 0) or usage_norm.get(norm, 0)
        if name in exact_names and name in CANDIDATE_TAGS:
            keep.append(opt)
            reasons["词表规范名"] += 1
        elif norm in vocab and uses > 0:
            keep.append(opt)
            reasons["词表大小写变体(有引用)"] += 1
        elif uses >= keep_min:
            keep.append(opt)
            reasons[f"高频引用≥{keep_min}"] += 1
        else:
            remove.append(opt)

    new_names = [t for t in CANDIDATE_TAGS if t not in exact_names]
    return keep, remove, new_names, reasons


def fit_plan(options, usage_id, usage_norm, keep_min):
    """把保留规模压到 Notion 的 100 项上限内：必要时自动提高 keep-min。

    返回 (实际 keep_min, keep, remove, new_names, reasons)。
    提高阈值会移除更多低频选项，但词表里的 43 个规范名始终保留。
    """
    for k in range(max(1, keep_min), 1001):
        keep, remove, new_names, reasons = build_plan(options, usage_id, usage_norm, k)
        if len(keep) + len(new_names) <= MAX_PATCH_OPTIONS:
            return k, keep, remove, new_names, reasons
    # 理论上到不了这里（光词表只有 43+3 项）；兜底按引用次数截断
    k = 1000
    keep, remove, new_names, reasons = build_plan(options, usage_id, usage_norm, k)
    keep = keep[:MAX_PATCH_OPTIONS - len(new_names)]
    keep_ids = {o.get("id") for o in keep}
    remove = [o for o in options if o.get("id") not in keep_ids]
    return k, keep, remove, new_names, reasons


def describe(db, options, usage_id, usage_norm, orphan, keep_min):
    props = db.get("properties") or {}
    print("=== tags 选项规模 ===")
    print(f"选项总数: {len(options)}")
    print(f"tags 属性估算字节: {_byte_size(props.get(TAG_PROP)):,}")
    print(f"全部属性估算字节: {_byte_size(props):,}  (官方建议整个 schema ≤ 50KB)")
    name_chars = sum(len(str(o.get('name') or '')) for o in options)
    print(f"选项名总字符: {name_chars:,} | 平均 {name_chars / max(len(options), 1):.1f}")
    print(f"已引用选项: {sum(1 for o in options if usage_id.get(o.get('id'), 0) > 0)}")
    zero = [o for o in options if not usage_id.get(o.get("id"), 0)
            and not usage_norm.get(_norm_tag(o.get("name")), 0)]
    print(f"零引用选项: {len(zero)}")

    buckets = collections.Counter()
    for opt in options:
        n = usage_id.get(opt.get("id"), 0) or usage_norm.get(_norm_tag(opt.get("name")), 0)
        if n == 0:
            buckets["0 次"] += 1
        elif n == 1:
            buckets["1 次"] += 1
        elif n < 5:
            buckets["2-4 次"] += 1
        elif n < 10:
            buckets["5-9 次"] += 1
        elif n < 50:
            buckets["10-49 次"] += 1
        elif n < 100:
            buckets["50-99 次"] += 1
        else:
            buckets["≥100 次"] += 1
    print(f"引用分布: {dict(buckets)}")

    print("=== 不同 keep-min 下的保留规模（词表 + 高频） ===")
    print(f"  Notion PATCH 限制保留列表 ≤ {MAX_PATCH_OPTIONS} 项；超限的方案会被自动提高阈值")
    print("  keep-min=1 即「只删零引用」，但该方案保留 405 项，超过 API 上限，无法直接执行")
    for k in (1, 2, 5, 10, 20, 50, 100):
        keep, _, new_names, _ = build_plan(options, usage_id, usage_norm, k)
        est = sum(_byte_size({"id": o.get("id"), "name": o.get("name")}) for o in keep)
        total = len(keep) + len(new_names)
        flag = "可用" if total <= MAX_PATCH_OPTIONS else "超限"
        print(f"  keep-min={k:<4} 保留 {len(keep):>5} 个 | 移除 {len(options)-len(keep):>5} 个 "
              f"| 新增词表 {len(new_names):>2} 个 | PATCH {total:>3} 项 {flag} "
              f"| 估算 tags ≈ {est:,} bytes")

    print("=== 最长选项名 Top 20（长名字最占 schema） ===")
    for opt in sorted(options, key=lambda o: -len(str(o.get("name") or "")))[:20]:
        print(f"  {len(str(opt.get('name') or '')):>4} 字 | 引用 {usage_id.get(opt.get('id'), 0):>4} | {opt.get('name')}")

    print("=== 引用最多 Top 30 ===")
    for opt in sorted(options, key=lambda o: -(usage_id.get(o.get("id"), 0)
                                               or usage_norm.get(_norm_tag(o.get("name")), 0)))[:30]:
        n = usage_id.get(opt.get("id"), 0) or usage_norm.get(_norm_tag(opt.get("name")), 0)
        print(f"  {n:>5}x | {opt.get('name')}")

    groups = collections.defaultdict(list)
    for opt in options:
        groups[_norm_tag(opt.get("name"))].append(opt.get("name"))
    dup = {k: v for k, v in groups.items() if len(v) > 1}
    print(f"=== 归一化后重名（大小写/空格变体）{len(dup)} 组，样本 15 ===")
    for k, v in list(dup.items())[:15]:
        print(f"  {k!r}: {v}")

    print(f"=== 页面上出现但已不在选项表里的值（幽灵选项）{sum(orphan.values())} 次 / {len(orphan)} 种，样本 15 ===")
    for name, n in orphan.most_common(15):
        print(f"  {n:>5}x | {name}")

    effective_min, keep, remove, new_names, reasons = fit_plan(
        options, usage_id, usage_norm, keep_min)
    print(f"=== 本次方案（实际 keep-min={effective_min}）：保留 {len(keep)} / "
          f"移除 {len(remove)} / 新增词表 {len(new_names)} / "
          f"PATCH 共 {len(keep) + len(new_names)} 项 ===")
    print(f"保留原因: {dict(reasons)}")
    print("=== 保留清单（前 60） ===")
    for opt in keep[:60]:
        print(f"  {usage_id.get(opt.get('id'), 0):>5}x | {opt.get('name')}")
    print("=== 移除清单样本（前 30） ===")
    for opt in remove[:30]:
        print(f"  {usage_id.get(opt.get('id'), 0):>5}x | {opt.get('name')}")
    if new_names:
        print(f"=== 将补齐的词表规范名（{len(new_names)}） ===")
        print("  " + ", ".join(new_names))


def write_backup(db_id, options, usage_id, usage_norm, keep_min, rows=None):
    # 页面级映射是兜底：万一某平台把「移除选项」实现成清空页面值，
    # 这份备份仍能逐页把标签补回去。只存有标签的页面，控制体积。
    page_tags_map = {}
    for page in rows or []:
        names = [n for _, n in page_tags(page)]
        if names:
            page_tags_map[page.get("id")] = names
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_id": db_id,
        "property": TAG_PROP,
        "keep_min": keep_min,
        "option_count": len(options),
        "page_tag_count": len(page_tags_map),
        "options": [
            {
                **opt,
                "usage_by_id": usage_id.get(opt.get("id"), 0),
                "usage_by_norm": usage_norm.get(_norm_tag(opt.get("name")), 0),
            }
            for opt in options
        ],
        "page_tags": page_tags_map,
    }
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"[backup] 完整选项清单已写入 {BACKUP_FILE} "
          f"({os.path.getsize(BACKUP_FILE):,} bytes)，请从 workflow artifact 下载留档")
    return payload


def page_tag_stats(rows):
    """统计页面上实际选中的标签，用于 apply 前后核对「值没有被误删」。"""
    total = 0
    distinct = set()
    for page in rows:
        for _, name in page_tags(page):
            total += 1
            norm = _norm_tag(name)
            if norm:
                distinct.add(norm)
    return total, len(distinct)


def page_tag_map(rows):
    """page_id -> 该页已选标签的归一化集合，用于逐页比对。"""
    result = {}
    for page in rows:
        names = set()
        for _, name in page_tags(page):
            norm = _norm_tag(name)
            if norm:
                names.add(norm)
        result[page.get("id")] = names
    return result


def verify_page_values(token, db_id, rows, removed_ids, before_total, before_distinct):
    """移除选项后重新读库，逐页确认标签值完好（官方说只改可选项、不改页面值）。"""
    print("[verify] 重新分页读取全部页面，逐页核对标签值是否被误删 ...")
    before_map = page_tag_map(rows)
    after_rows = list(iter_pages(token, db_id))
    after_total, after_distinct = page_tag_stats(after_rows)
    after_map = page_tag_map(after_rows)
    print(f"[verify] 页面已选标签总次数: {before_total} → {after_total} | "
          f"不同标签值: {before_distinct} → {after_distinct}")

    lost = []
    for pid, names in before_map.items():
        missing = names - after_map.get(pid, set())
        if missing:
            lost.append((pid, sorted(missing)))
    print(f"[verify] 逐页比对 {len(before_map)} 页，标签值缺失页面: {len(lost)} 个")
    for pid, missing in lost[:20]:
        print(f"  [verify] 缺失 {pid}: {missing}")

    affected = [p for p in rows if any(i in removed_ids for i, _ in page_tags(p))]
    print(f"[verify] 原本引用了被移除选项的页面: {len(affected)} 个（这些页面值应保持不变）")
    for page in affected[:10]:
        pid = page.get("id")
        print(f"  [verify] {'OK' if before_map[pid] <= after_map.get(pid, set()) else 'DIFF'} "
              f"{pid} 前={sorted(before_map[pid])} 后={sorted(after_map.get(pid, set()))}")

    if lost or after_total < before_total or after_distinct < before_distinct:
        print("[verify] ⚠️ 页面标签值出现缺失，备份里的 page_tags 映射可用于逐页恢复")
        return False
    print("[verify] 页面标签值完整，未丢失")
    return True


def apply_plan(token, db_id, keep, new_names):
    body = {
        "properties": {
            TAG_PROP: {
                "multi_select": {
                    "options": (
                        [{"id": o.get("id")} for o in keep]
                        + [{"name": name} for name in new_names]
                    )
                }
            }
        }
    }
    print(f"[apply] PATCH database：保留 {len(keep)} 个选项，新增 {len(new_names)} 个词表选项")
    resp = requests.patch(
        f"{BASE}/v1/databases/{db_id}",
        headers=_headers(token),
        data=json.dumps(body),
        timeout=60,
    )
    if resp.status_code >= 300:
        print(f"[apply] 失败 HTTP {resp.status_code}: {resp.text[:1200]}")
        return False
    print("[apply] PATCH 成功，重新读取 schema 复核")
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("probe", "dry-run", "apply"), default="probe")
    ap.add_argument("--keep-min", type=int,
                    default=int(os.environ.get("TAGS_KEEP_MIN", "1")),
                    help="高频标签保留阈值（引用次数 ≥ 该值）；默认 1 = 只删零引用选项")
    args = ap.parse_args()

    token = os.environ.get("NOTION_API_KEY", "")
    db_id = os.environ.get("NOTION_DATABASE_ID", "")
    if not token or not db_id:
        print("缺少 NOTION_API_KEY / NOTION_DATABASE_ID，退出")
        return 1
    print(f"模式: {args.mode} | keep-min: {args.keep_min} | 密钥状态: 已注入（值不打印）")

    db = fetch_database(token, db_id)
    props = db.get("properties") or {}
    if TAG_PROP not in props or "multi_select" not in (props.get(TAG_PROP) or {}):
        print(f"数据库里没有 multi_select 属性 {TAG_PROP!r}，退出")
        return 1
    options = props[TAG_PROP]["multi_select"].get("options") or []
    before_bytes = _byte_size(props.get(TAG_PROP))
    before_count = len(options)

    rows = list(iter_pages(token, db_id))
    usage_id, usage_norm, orphan = collect_usage(rows, options)
    before_tags, before_distinct = page_tag_stats(rows)
    print(f"=== 页面上已选标签：{before_tags} 次 / {before_distinct} 种（清理前基线） ===")
    describe(db, options, usage_id, usage_norm, orphan, args.keep_min)
    effective_min, keep, remove, new_names, _ = fit_plan(
        options, usage_id, usage_norm, args.keep_min)
    if effective_min != args.keep_min:
        print(f"=== 自动适配：keep-min {args.keep_min} → {effective_min} "
              f"（满足 Notion PATCH ≤ {MAX_PATCH_OPTIONS} 项上限） ===")
    write_backup(db_id, options, usage_id, usage_norm, effective_min, rows)

    if args.mode == "probe":
        print("probe：只读，未写入任何内容")
        return 0

    print(f"=== 移除清单（共 {len(remove)} 个，日志只列前 100；完整清单见备份 artifact） ===")
    for opt in remove[:100]:
        print(f"  {usage_id.get(opt.get('id'), 0):>5}x | {opt.get('id')} | {opt.get('name')}")
    if len(remove) > 100:
        print(f"  ... 其余 {len(remove) - 100} 个见 {BACKUP_FILE}")
    print(f"=== 移除项里有引用的：{sum(1 for o in remove if usage_id.get(o.get('id'), 0))} 个 ===")

    if args.mode == "dry-run":
        print("dry-run：未写入任何内容")
        return 0

    if len(keep) + len(new_names) > MAX_PATCH_OPTIONS:
        print(f"[apply] 拒绝执行：保留列表 {len(keep) + len(new_names)} 项超过 "
              f"Notion 的 {MAX_PATCH_OPTIONS} 项上限")
        return 1

    if not remove and not new_names:
        print("无需变更：选项集合已经收敛（幂等）")
        return 0

    if not apply_plan(token, db_id, keep, new_names):
        return 1

    after = fetch_database(token, db_id)
    after_options = (after["properties"][TAG_PROP]["multi_select"].get("options") or [])
    after_bytes = _byte_size(after["properties"].get(TAG_PROP))
    print(f"=== 复核：选项 {before_count} → {len(after_options)} | "
          f"tags 属性 {before_bytes:,} → {after_bytes:,} bytes ===")
    print("=== 现有选项（前 60） ===")
    print([o.get("name") for o in after_options[:60]])

    if not verify_page_values(token, db_id, rows, {o.get("id") for o in remove},
                              before_tags, before_distinct):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

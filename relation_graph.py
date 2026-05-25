"""
relation_graph.py

从中文新闻/长文本中抽取实体（人名/机构/地点等），并基于“同句共现 + 触发词”
生成关系图谱，导出为：
 - HTML（PyVis，可交互）
 - DOT（Graphviz）
 - JSON（节点/边，便于二次处理）

示例：
  python relation_graph.py --input test.txt --out-html graph.html --out-dot graph.dot --out-json graph.json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import jieba
import jieba.posseg as pseg

try:
    from pyvis.network import Network
except Exception:  # pragma: no cover
    Network = None  # type: ignore


SENT_SPLIT_RE = re.compile(r"[。！？!?；;]\s*")

# 常见组织/机构后缀（可按你的语料继续加）
ORG_SUFFIXES = (
    "公司",
    "集团",
    "委员会",
    "政府",
    "部门",
    "警方",
    "白宫",
    "基金",
    "投资",
    "工业部",
    "内政部",
    "研究中心",
    "新闻频道",
    "事务所",
    "平台",
)

# 触发词：用于给边打更像“关系”的标签
REL_TRIGGERS = (
    "调查",
    "审查",
    "启动",
    "表示",
    "称",
    "证实",
    "指出",
    "透露",
    "担忧",
    "拒绝置评",
    "沟通",
    "采购",
    "输送",
    "成立",
    "拆分",
    "更名",
    "逮捕",
    "指控",
    "加强",
    "要求",
    "施加压力",
    "提供服务",
    "帮助",
)


@dataclass(frozen=True)
class Entity:
    text: str
    etype: str  # PERSON / ORG / LOC / MISC


@dataclass
class Edge:
    src: str
    dst: str
    label: str
    weight: int = 1


def read_text(path: Path) -> str:
    data = path.read_text(encoding="utf-8", errors="ignore")
    # 兼容 Windows 文本里可能出现的 \r\n
    return data.replace("\r\n", "\n").strip()


def split_sentences(text: str) -> List[str]:
    parts = [s.strip() for s in SENT_SPLIT_RE.split(text) if s.strip()]
    return parts


def _looks_like_latin_org(token: str) -> bool:
    # 例如 Megaspeed / ImportGenius / WireScreen / NetThunder / ChatGPT
    if len(token) < 2:
        return False
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9\-\.]{1,40}", token):
        return True
    return False


def _looks_like_chinese_person(token: str) -> bool:
    # 极简启发：2-3 个汉字，且不以常见机构后缀结尾
    if not (2 <= len(token) <= 3):
        return False
    if not re.fullmatch(r"[\u4e00-\u9fff]{2,3}", token):
        return False
    if token.endswith(ORG_SUFFIXES):
        return False
    return True


def _guess_entity_type(token: str) -> str:
    if _looks_like_latin_org(token):
        return "ORG"
    if token.endswith(ORG_SUFFIXES):
        return "ORG"
    if _looks_like_chinese_person(token):
        return "PERSON"
    return "MISC"


def extract_entities(sentence: str) -> List[Entity]:
    """
    使用 jieba 词性标注 + 规则抽取实体。
    说明：
    - jieba 的人名/机构/地名在新闻中文里效果尚可，但不是严格 NER；
    - 这里做了额外规则，让英文专名/带后缀机构更容易被识别。
    """
    entities: Dict[str, str] = {}

    # 1) 词性线索
    for word, flag in pseg.cut(sentence):
        w = word.strip()
        if not w:
            continue
        # 排除纯标点/数字
        if re.fullmatch(r"[\W_]+", w):
            continue
        if re.fullmatch(r"\d+(\.\d+)?", w):
            continue

        etype: Optional[str] = None
        if flag in ("nr", "nrfg"):  # 人名
            etype = "PERSON"
        elif flag in ("nt",):  # 机构团体
            etype = "ORG"
        elif flag in ("ns",):  # 地名
            etype = "LOC"
        elif _looks_like_latin_org(w) or w.endswith(ORG_SUFFIXES):
            etype = _guess_entity_type(w)

        if etype:
            # 过滤明显噪声
            if len(w) == 1:
                continue
            if w in ("今年", "去年", "本周", "近日", "记者", "人士"):
                continue
            # 同名更精确的类型优先：ORG/LOC/PERSON > MISC
            prev = entities.get(w)
            if prev is None or (prev == "MISC" and etype != "MISC"):
                entities[w] = etype

    # 2) 额外：英文连续片段（在分词失败时兜底）
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9\-\.]{1,40}", sentence):
        tok = m.group(0)
        if _looks_like_latin_org(tok):
            entities.setdefault(tok, "ORG")

    return [Entity(text=k, etype=v) for k, v in entities.items()]


def detect_relation_label(sentence: str, a: str, b: str) -> str:
    """
    尝试找出更像“关系”的标签：
    - 若句中出现触发词，则用第一个匹配的触发词作为 label
    - 否则默认 cooccur（同句共现）
    """
    # 只在两实体都在句子中时调用
    pos_a = sentence.find(a)
    pos_b = sentence.find(b)
    if pos_a == -1 or pos_b == -1:
        return "cooccur"

    lo, hi = sorted((pos_a, pos_b))
    window = sentence[lo:hi]
    for t in REL_TRIGGERS:
        if t in window or t in sentence:
            return t
    return "cooccur"


def build_graph(
    sentences: Sequence[str],
    min_edge_weight: int = 1,
    max_entities_per_sentence: int = 12,
) -> Tuple[Dict[str, str], Dict[Tuple[str, str, str], int]]:
    """
    Returns:
      nodes: entity_text -> entity_type
      edges: (src, dst, label) -> weight
    """
    nodes: Dict[str, str] = {}
    edges: Dict[Tuple[str, str, str], int] = {}

    for s in sentences:
        ents = extract_entities(s)
        if not ents:
            continue

        # 控制每句实体上限，避免长句共现爆炸
        ents = sorted(ents, key=lambda e: (-len(e.text), e.text))
        ents = ents[:max_entities_per_sentence]

        # 注册节点
        for e in ents:
            nodes.setdefault(e.text, e.etype)

        # 共现连边（无向视为双向存储的有向边，方便可视化）
        texts = [e.text for e in ents]
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a, b = texts[i], texts[j]
                if a == b:
                    continue
                label = detect_relation_label(s, a, b)

                key1 = (a, b, label)
                key2 = (b, a, label)
                edges[key1] = edges.get(key1, 0) + 1
                edges[key2] = edges.get(key2, 0) + 1

    # 过滤低权重边
    edges = {k: w for k, w in edges.items() if w >= min_edge_weight}
    return nodes, edges


def export_json(out_path: Path, nodes: Dict[str, str], edges: Dict[Tuple[str, str, str], int]) -> None:
    payload = {
        "nodes": [{"id": k, "type": v} for k, v in sorted(nodes.items())],
        "edges": [
            {"source": s, "target": t, "label": label, "weight": w}
            for (s, t, label), w in sorted(edges.items(), key=lambda x: (-x[1], x[0]))
        ],
    }
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def export_dot(out_path: Path, nodes: Dict[str, str], edges: Dict[Tuple[str, str, str], int]) -> None:
    def node_color(t: str) -> str:
        return {
            "PERSON": "#ffb703",
            "ORG": "#219ebc",
            "LOC": "#8ecae6",
            "MISC": "#adb5bd",
        }.get(t, "#adb5bd")

    lines: List[str] = []
    lines.append("digraph G {")
    lines.append('  graph [rankdir="LR", bgcolor="white"];')
    lines.append('  node [shape="box", style="rounded,filled", fontname="Microsoft YaHei"];')
    lines.append('  edge [fontname="Microsoft YaHei"];')

    for n, t in sorted(nodes.items()):
        color = node_color(t)
        safe = n.replace('"', '\\"')
        lines.append(f'  "{safe}" [fillcolor="{color}", label="{safe}\\n({t})"];')

    for (s, t, label), w in sorted(edges.items(), key=lambda x: (-x[1], x[0])):
        if s not in nodes or t not in nodes:
            continue
        safe_s = s.replace('"', '\\"')
        safe_t = t.replace('"', '\\"')
        safe_l = label.replace('"', '\\"')
        penwidth = 1 + math.log(1 + w)
        lines.append(f'  "{safe_s}" -> "{safe_t}" [label="{safe_l} ({w})", penwidth="{penwidth:.2f}"];')

    lines.append("}")
    out_path.write_text("\n".join(lines), encoding="utf-8")


def export_html(out_path: Path, nodes: Dict[str, str], edges: Dict[Tuple[str, str, str], int]) -> None:
    if Network is None:
        raise RuntimeError("未安装 pyvis：请先 pip install pyvis")

    def node_color(t: str) -> str:
        return {
            "PERSON": "#ffb703",
            "ORG": "#219ebc",
            "LOC": "#8ecae6",
            "MISC": "#adb5bd",
        }.get(t, "#adb5bd")

    net = Network(height="900px", width="100%", bgcolor="#ffffff", directed=True)
    net.barnes_hut(gravity=-8000, central_gravity=0.3, spring_length=130, spring_strength=0.02)

    for n, t in nodes.items():
        net.add_node(
            n,
            label=n,
            title=f"{n}\n{t}",
            color=node_color(t),
            shape="box",
        )

    for (s, t, label), w in edges.items():
        if s not in nodes or t not in nodes:
            continue
        net.add_edge(s, t, label=label, title=f"{label} ({w})", value=w)

    # 让浏览器里支持中文字体
    net.set_options(
        """
var options = {
  "nodes": {
    "font": { "face": "Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif" }
  },
  "edges": {
    "font": { "face": "Microsoft YaHei, PingFang SC, Noto Sans CJK SC, sans-serif", "align": "middle" },
    "arrows": { "to": { "enabled": true } },
    "smooth": { "type": "dynamic" }
  },
  "physics": { "stabilization": { "iterations": 300 } }
}
"""
    )

    net.write_html(str(out_path))


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="从文本生成关系图谱（实体共现 + 触发词）")
    p.add_argument("--input", required=True, help="输入文本文件路径（UTF-8）")
    p.add_argument("--out-html", default="graph.html", help="输出 HTML 文件（PyVis）")
    p.add_argument("--out-dot", default="graph.dot", help="输出 DOT 文件（Graphviz）")
    p.add_argument("--out-json", default="graph.json", help="输出 JSON 文件（nodes/edges）")
    p.add_argument("--min-edge-weight", type=int, default=2, help="边最小权重（默认 2）")
    p.add_argument("--max-entities-per-sentence", type=int, default=12, help="每句最多使用多少个实体（默认 12）")
    return p.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    in_path = Path(args.input)
    text = read_text(in_path)
    sents = split_sentences(text)
    nodes, edges = build_graph(
        sents,
        min_edge_weight=args.min_edge_weight,
        max_entities_per_sentence=args.max_entities_per_sentence,
    )

    out_html = Path(args.out_html)
    out_dot = Path(args.out_dot)
    out_json = Path(args.out_json)

    export_json(out_json, nodes, edges)
    export_dot(out_dot, nodes, edges)
    # HTML 是可选（没装 pyvis 也能产出 dot/json）
    try:
        export_html(out_html, nodes, edges)
    except Exception as e:
        print(f"HTML 导出跳过：{e}")

    print(f"nodes={len(nodes)} edges={len(edges)}")
    print(f"JSON: {out_json}")
    print(f"DOT : {out_dot}")
    print(f"HTML: {out_html}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


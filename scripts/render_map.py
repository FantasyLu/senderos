#!/usr/bin/env python3
"""
render_map.py — 将 waypoints JSON 渲染为 Leaflet HTML 地图

用法：
  python render_map.py <waypoints.json> [output.html] [--title "标题"] [--subtitle "副标题"]
  python render_map.py <waypoints.json> [output.html] --cluster      # 启用 markercluster 聚合
  python render_map.py <waypoints.json> [output.html] --major-only   # 仅主要节点
  python render_map.py <waypoints.json> [output.html] --stages       # 按章节阶段切换

  # 也可以作为模块调用
  from render_map import render
  html_path = render(waypoints, title="徐霞客游记路线图", subtitle="明代·云南段")

waypoints JSON 格式（数组）：
[
  {
    "place_raw": "昆明府",
    "place_modern": "云南省昆明市",
    "layer": "major",          // "major" | "transit"
    "confidence": "confirmed", // "confirmed"|"single_source"|"inferred"|"disputed"
    "lat": 25.0389,
    "lon": 102.7183,
    "resolved": true,
    "coord_note": "",          // 坐标备注（推测/模糊匹配时有内容）
    "route_to_next": "straight", // "straight" | "described"
    "geo_source": "ancient",   // "ancient"|"nominatim"|"inferred"|"unresolved"
    "visits": [
      {
        "chapter_num": 3,
        "chapter_title": "第三章·滇西之行",
        "time_raw": "万历四十一年九月初一",
        "time_note": "约1613年10月",
        "duration": "三日",
        "description": "出发，休整补给",
        "source": "pdf",        // "pdf"|"user-pdf"|"web"|"inferred"
        "source_sentence": "万历四十一年九月初一，余自昆明府启程……"
      }
    ]
  }
]
"""

import json
import sys
import os
import re
import math
import argparse
from pathlib import Path
from datetime import datetime

SKILL_DIR = Path(__file__).parent.parent
TEMPLATE_FILE = SKILL_DIR / "references" / "map-template.html"
OUTPUT_DIR = SKILL_DIR / "output"


# ══════════════════════════════════════════════════════════════════
#  辅助函数
# ══════════════════════════════════════════════════════════════════

def _compute_center(waypoints: list[dict]) -> tuple[float, float]:
    """计算有效坐标点的中心"""
    lats = [wp["lat"] for wp in waypoints if wp.get("lat") and wp.get("lon")]
    lons = [wp["lon"] for wp in waypoints if wp.get("lat") and wp.get("lon")]
    if not lats:
        return (35.0, 105.0)  # 中国地理中心默认值
    return (sum(lats) / len(lats), sum(lons) / len(lons))


def _compute_zoom(waypoints: list[dict]) -> int:
    """根据坐标范围估算合适的初始缩放级别"""
    lats = [wp["lat"] for wp in waypoints if wp.get("lat") and wp.get("lon")]
    lons = [wp["lon"] for wp in waypoints if wp.get("lat") and wp.get("lon")]
    if len(lats) < 2:
        return 7

    lat_span = max(lats) - min(lats)
    lon_span = max(lons) - min(lons)
    max_span = max(lat_span, lon_span)

    if max_span > 50:   return 3
    if max_span > 20:   return 4
    if max_span > 10:   return 5
    if max_span > 5:    return 6
    if max_span > 2:    return 7
    if max_span > 1:    return 8
    return 9


def _count_stats(waypoints: list[dict]) -> tuple[int, int, int]:
    """统计主要节点、途经点、推测点数量"""
    major = sum(1 for wp in waypoints if wp.get("layer") == "major" and wp.get("resolved"))
    transit = sum(1 for wp in waypoints if wp.get("layer") == "transit" and wp.get("resolved"))
    inferred = sum(
        1 for wp in waypoints
        if wp.get("resolved") and all(v.get("source") == "inferred" for v in wp.get("visits", []))
    )
    return major, transit, inferred


def _source_label(source: str) -> str:
    labels = {
        "pdf": "PDF资料",
        "user-pdf": "用户提供",
        "web": "网络检索",
        "inferred": "推测",
    }
    return labels.get(source, source)


def _build_stages_data(waypoints: list[dict]) -> list[dict]:
    """
    按章节（chapter_title）分组，生成阶段切换器数据。
    返回：[{"label": "第三章·滇西之行", "indices": [0, 1, 2]}, ...]
    """
    stages: dict[str, list[int]] = {}
    for idx, wp in enumerate(waypoints):
        if not wp.get("lat") or not wp.get("lon"):
            continue
        chapter = ""
        if wp.get("visits"):
            chapter = wp["visits"][0].get("chapter_title", "") or ""
        if not chapter:
            chapter = "其他"
        if chapter not in stages:
            stages[chapter] = []
        stages[chapter].append(idx)

    return [{"label": k, "indices": v} for k, v in stages.items()]


# ══════════════════════════════════════════════════════════════════
#  生成时间轴侧边栏 HTML
# ══════════════════════════════════════════════════════════════════

def _build_timeline_html(waypoints: list[dict]) -> str:
    """生成侧边栏时间轴 HTML"""
    html_parts = []
    current_chapter = None

    # 只展示主要节点（transit 点不进时间轴）
    major_wps = [(idx, wp) for idx, wp in enumerate(waypoints) if wp.get("layer") == "major"]

    for idx, wp in major_wps:
        first_visit = wp["visits"][0] if wp.get("visits") else {}
        chapter_title = first_visit.get("chapter_title", "")

        # 章节分组标题
        if chapter_title and chapter_title != current_chapter:
            current_chapter = chapter_title
            html_parts.append(
                f'<div class="timeline-divider">{chapter_title}</div>'
            )

        # 来源徽章
        source = first_visit.get("source", "web")
        badge_map = {
            "pdf": ('pdf', 'PDF'),
            "user-pdf": ('pdf', '用户PDF'),
            "web": ('web', '检索'),
            "inferred": ('inferred', '推测'),
        }
        badge_cls, badge_text = badge_map.get(source, ('web', source))

        # 置信度徽章
        confidence = wp.get("confidence", "single_source")
        conf_badge_map = {
            "confirmed":     ('conf-confirmed', '✓'),
            "single_source": ('conf-single', '◎'),
            "inferred":      ('conf-inferred', '?'),
            "disputed":      ('conf-disputed', '!'),
        }
        conf_cls, conf_symbol = conf_badge_map.get(confidence, ('conf-single', '◎'))

        # 多次到访标记
        visit_count = len(wp.get("visits", []))
        visit_note = f"（{visit_count}次到访）" if visit_count > 1 else ""

        place_display = wp.get("place_raw", "")
        if wp.get("place_modern") and wp["place_modern"] != place_display:
            place_display = f"{wp['place_raw']}"

        time_display = first_visit.get("time_raw", "")

        unresolved_note = "" if wp.get("resolved") else ' <span style="color:#c05030;">⚠️坐标待确认</span>'

        html_parts.append(f'''
<div class="timeline-item" data-idx="{idx}">
  <div class="tl-num">#{idx + 1} {visit_note}</div>
  <div class="tl-place">{place_display}{unresolved_note} <span class="tl-conf {conf_cls}" title="{confidence}">{conf_symbol}</span></div>
  {f'<div class="tl-time">{time_display}</div>' if time_display else ''}
  {f'<div class="tl-chapter">{chapter_title}</div>' if chapter_title and visit_count > 1 else ''}
  <span class="tl-badge {badge_cls}">{badge_text}</span>
</div>''')

    return "\n".join(html_parts)


# ══════════════════════════════════════════════════════════════════
#  主渲染函数
# ══════════════════════════════════════════════════════════════════

def render(
    waypoints: list[dict],
    title: str = "行程路线图",
    subtitle: str = "",
    output_path: str | Path | None = None,
    cluster: bool = False,
    major_only: bool = False,
    stages: bool = False,
) -> Path:
    """
    将 waypoints 渲染为 Leaflet HTML 地图文件。

    参数：
      cluster    — 启用 markercluster 聚合模式
      major_only — 仅显示主要节点
      stages     — 启用阶段切换器（按章节）

    返回生成的 HTML 文件路径。
    """
    # 读取模板
    if not TEMPLATE_FILE.exists():
        raise FileNotFoundError(f"地图模板不存在: {TEMPLATE_FILE}")

    template = TEMPLATE_FILE.read_text(encoding="utf-8")

    # major_only 过滤
    render_wps = waypoints
    if major_only:
        render_wps = [wp for wp in waypoints if wp.get("layer") == "major"]

    # ── 兼容性：补全 visits 字段 ──
    # 若 waypoint 只有 event/year 字段（geocode 输出格式），自动转换为 visits 数组
    normalized = []
    for wp in render_wps:
        wp = dict(wp)  # 避免修改原对象
        if not wp.get("visits"):
            event_text     = wp.get("event", "")
            year_text      = wp.get("year", "")
            chapter_title  = wp.get("chapter_title", "") or (f"{year_text}年" if year_text else "")
            # confidence 映射到 source
            conf = wp.get("confidence", "single_source")
            source = "web" if conf in ("confirmed", "single_source") else "inferred"
            wp["visits"] = [{
                "chapter_num":     wp.get("order", 0),
                "chapter_title":   chapter_title,
                "time_raw":        year_text,
                "time_note":       "",
                "duration":        "",
                "description":     event_text,
                "source":          source,
                "source_sentence": wp.get("source_sentence", event_text),
            }]
        normalized.append(wp)
    render_wps = normalized

    # 统计
    major_count, transit_count, inferred_count = _count_stats(render_wps)
    center = _compute_center(render_wps)
    zoom = _compute_zoom(render_wps)

    # 生成时间轴 HTML
    timeline_html = _build_timeline_html(render_wps)

    # 阶段数据
    stages_data = _build_stages_data(render_wps) if stages else []

    # 默认副标题
    if not subtitle:
        valid_times = [
            wp["visits"][0]["time_raw"]
            for wp in render_wps
            if wp.get("visits") and wp["visits"][0].get("time_raw")
        ]
        if valid_times:
            subtitle = f"{valid_times[0]} — {valid_times[-1]}"

    # 替换模板占位符
    replacements = {
        "{{TITLE}}": title,
        "{{SUBTITLE}}": subtitle,
        "{{MAJOR_COUNT}}": str(major_count),
        "{{TRANSIT_COUNT}}": str(transit_count),
        "{{INFERRED_COUNT}}": str(inferred_count),
        "{{TIMELINE_HTML}}": timeline_html,
        "{{WAYPOINTS_JSON}}": json.dumps(render_wps, ensure_ascii=False, indent=2),
        "{{MAP_CENTER}}": json.dumps(list(center)),
        "{{MAP_ZOOM}}": str(zoom),
        "{{USE_CLUSTER}}": "true" if cluster else "false",
        "{{USE_STAGES}}": "true" if stages else "false",
        "{{STAGES_DATA}}": json.dumps(stages_data, ensure_ascii=False),
    }

    html = template
    for placeholder, value in replacements.items():
        html = html.replace(placeholder, value)

    # 确定输出路径
    if output_path is None:
        OUTPUT_DIR.mkdir(exist_ok=True)
        safe_title = re.sub(r'[^\w\u4e00-\u9fff\-]', '_', title)[:40]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = OUTPUT_DIR / f"{safe_title}_{timestamp}.html"
    else:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

    output_path.write_text(html, encoding="utf-8")

    print(f"\n✅ 地图已生成：{output_path}")
    print(f"   主要节点：{major_count}  途经点：{transit_count}  推测：{inferred_count}")
    if cluster:
        print("   📍 聚合模式已启用（相近图钉自动合并）")
    if stages:
        print(f"   📑 阶段切换器已启用（{len(stages_data)} 个阶段）")
    if major_only:
        print("   🔍 仅显示主要节点，途经点已隐藏")
    if inferred_count > 0:
        print(f"   ⚠️  {inferred_count} 处标注为「推测」，请注意核实")

    return output_path


# ══════════════════════════════════════════════════════════════════
#  生成 CHECKPOINT 确认表格（供 SKILL.md 流程调用）
# ══════════════════════════════════════════════════════════════════

def build_checkpoint_table(waypoints: list[dict]) -> str:
    """
    生成供用户确认的表格字符串（Markdown 格式）。
    每个地点一行，含章节、时间、简述、坐标状态、置信度。
    同一地点多次到访展示为多行。
    """
    lines = []
    lines.append("| # | 地点 | 章节 | 时间 | 简述 | 层级 | 坐标 | 置信度 |")
    lines.append("|---|------|------|------|------|------|------|--------|")

    conf_labels = {
        "confirmed":     "✅ 确认",
        "single_source": "🟡 单一来源",
        "inferred":      "💭 推测",
        "disputed":      "🔴 存在矛盾",
    }

    row_num = 1
    for wp in waypoints:
        place = wp.get("place_raw", "")
        layer = "主要" if wp.get("layer") == "major" else "途经"
        coord_status = "✅" if wp.get("resolved") else "⚠️ 待确认"
        confidence = wp.get("confidence", "single_source")
        conf_label = conf_labels.get(confidence, confidence)

        # 若 source_sentence 校验失败，覆盖置信度显示
        for visit in wp.get("visits", [{}]):
            if visit.get("verification_status") == "sentence_not_found":
                conf_label = "🔴 原文句未找到"
                break

        for visit in wp.get("visits", [{}]):
            chapter = visit.get("chapter_title", "")
            time_raw = visit.get("time_raw", "")
            desc = visit.get("description", "")
            # 截断过长描述
            if len(desc) > 20:
                desc = desc[:18] + "…"

            lines.append(
                f"| {row_num} | {place} | {chapter} | {time_raw} | {desc} | {layer} | {coord_status} | {conf_label} |"
            )
            row_num += 1

    return "\n".join(lines)


def build_density_prompt(waypoints: list[dict]) -> str:
    """
    根据节点数生成密度建议提示（返回空字符串表示节点稀少，无需选择）。
    """
    resolved_count = sum(1 for wp in waypoints if wp.get("resolved"))

    if resolved_count <= 30:
        return ""  # 直接渲染，无需提示

    if resolved_count <= 80:
        return (
            f"\n共提取 {resolved_count} 个有效节点，地图可能较为密集。建议选择显示方式：\n"
            "  ① 全部显示 + 聚合模式（相近图钉自动合并，缩放后展开）【推荐】\n"
            "  ② 仅显示主要节点（途经点隐藏）\n"
            "  ③ 按章节分段显示（顶部切换器，每次只看一章）\n"
            "（回复数字即可，默认选 ①）"
        )

    return (
        f"\n共 {resolved_count} 个节点，建议按章节分段显示，否则地图会非常拥挤。\n"
        "  ① 按章节分段显示【强烈推荐】\n"
        "  ② 强行全部显示 + 聚合模式\n"
        "（回复数字即可，默认选 ①）"
    )


def build_unresolved_list(waypoints: list[dict]) -> str:
    """列出所有无法自动解析坐标的地名"""
    unresolved = [wp for wp in waypoints if not wp.get("resolved")]
    if not unresolved:
        return "（全部坐标已解析）"

    lines = [f"⚠️ 以下 {len(unresolved)} 处地名无法自动解析坐标，请指示处理方式："]
    for wp in unresolved:
        lines.append(f"  - 「{wp['place_raw']}」{wp.get('coord_note', '')}")
    lines.append("\n可回复：① 提供大致地区（如「在云南西部」）② 跳过该地点不显示 ③ 保留但标注「位置待确认」")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  命令行入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 waypoints JSON 渲染为 Leaflet 地图")
    parser.add_argument("input", help="waypoints JSON 文件路径")
    parser.add_argument("output", nargs="?", help="输出 HTML 路径（可选，默认 output/ 目录）")
    parser.add_argument("--title", default="行程路线图", help="地图标题")
    parser.add_argument("--subtitle", default="", help="副标题（时间范围等）")
    parser.add_argument("--checkpoint", action="store_true", help="只输出确认表格，不生成地图")
    parser.add_argument("--cluster", action="store_true", help="启用 markercluster 聚合")
    parser.add_argument("--major-only", action="store_true", help="仅渲染主要节点")
    parser.add_argument("--stages", action="store_true", help="启用按章节阶段切换器")
    args = parser.parse_args()

    with open(args.input, encoding="utf-8") as f:
        waypoints = json.load(f)

    if args.checkpoint:
        print(build_checkpoint_table(waypoints))
        print()
        print(build_unresolved_list(waypoints))
        density_prompt = build_density_prompt(waypoints)
        if density_prompt:
            print(density_prompt)
    else:
        render(
            waypoints,
            title=args.title,
            subtitle=args.subtitle,
            output_path=args.output,
            cluster=args.cluster,
            major_only=getattr(args, 'major_only', False),
            stages=args.stages,
        )

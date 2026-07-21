#!/usr/bin/env python3
"""
geocode.py — 地名 → 坐标解析工具

优先级：
  1. 本地古地名映射表 (ancient-place-names.md)
  2. OSM Nominatim API（免费，无需 key）
  3. 标记为 unresolved

用法：
  from geocode import geocode
  result = geocode("昆明府")
  # → {"lat": 25.0389, "lon": 102.7183, "source": "ancient", "modern": "云南省昆明市"}

  # 批量
  from geocode import geocode_batch
  results = geocode_batch(["昆明府", "大理府", "不存在的地名"])
"""

import re
import time
import json
import os
import urllib.request
import urllib.parse
import urllib.error
from pathlib import Path

# ── 路径配置 ──
SKILL_DIR = Path(__file__).parent.parent
ANCIENT_NAMES_FILE = SKILL_DIR / "references" / "ancient-place-names.md"

# OSM Nominatim 配置
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {
    "User-Agent": "route-mapper-skill/1.0 (travel route visualization)",
    "Accept-Language": "en",  # 英文优先，避免中文地名误匹配中国境内同名地点
}
NOMINATIM_DELAY = 1.1  # 秒，遵守 Nominatim 使用政策（≤1 req/s）

# 地名后缀列表（用于 fallback 去掉后缀再查）
PLACE_SUFFIXES = ["府", "州", "县", "路", "道", "郡", "卫", "所", "镇", "市", "区",
                  "省", "国", "城", "关", "寨", "堡", "驿", "川", "江", "河"]


# ══════════════════════════════════════════════════════════════════
#  解析本地古地名映射表
# ══════════════════════════════════════════════════════════════════

def _load_ancient_names() -> dict:
    """
    解析 ancient-place-names.md，返回
    { "古地名": {"modern": "现代地名", "lat": float, "lon": float, "note": "备注"} }
    """
    if not ANCIENT_NAMES_FILE.exists():
        return {}

    mapping = {}
    with open(ANCIENT_NAMES_FILE, encoding="utf-8") as f:
        for line in f:
            # 匹配表格行：| 古地名 | 现代地名 | 纬度 | 经度 | 备注 |
            m = re.match(
                r"\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([-\d.]+)\s*\|\s*([-\d.]+)\s*\|(?:\s*([^|]*?)\s*\|)?",
                line
            )
            if not m:
                continue
            ancient, modern, lat, lon = m.group(1), m.group(2), m.group(3), m.group(4)
            note = m.group(5) or ""
            # 跳过表头
            if ancient in ("古地名", "#", "---"):
                continue
            try:
                mapping[ancient.strip()] = {
                    "modern": modern.strip(),
                    "lat": float(lat),
                    "lon": float(lon),
                    "note": note.strip(),
                }
            except ValueError:
                continue
    return mapping


# 全局缓存（模块级，避免重复读文件）
_ANCIENT_CACHE: dict | None = None


def _get_ancient_names() -> dict:
    global _ANCIENT_CACHE
    if _ANCIENT_CACHE is None:
        _ANCIENT_CACHE = _load_ancient_names()
    return _ANCIENT_CACHE


# ══════════════════════════════════════════════════════════════════
#  查本地映射表
# ══════════════════════════════════════════════════════════════════

def _lookup_ancient(place: str) -> dict | None:
    """
    在古地名表中查找地名。
    1. 精确匹配
    2. 去掉行政后缀后再查
    返回 None 表示未命中。
    """
    db = _get_ancient_names()

    # 精确匹配
    if place in db:
        entry = db[place]
        return {
            "lat": entry["lat"],
            "lon": entry["lon"],
            "source": "ancient",
            "modern": entry["modern"],
            "note": entry["note"],
        }

    # 去后缀后再查
    stripped = place
    for suffix in PLACE_SUFFIXES:
        if place.endswith(suffix):
            stripped = place[: -len(suffix)]
            break

    if stripped != place and stripped in db:
        entry = db[stripped]
        return {
            "lat": entry["lat"],
            "lon": entry["lon"],
            "source": "ancient",
            "modern": entry["modern"],
            "note": f"通过去后缀「{suffix}」匹配到「{stripped}」。{entry['note']}",
        }

    # 模糊：db 中有任何以 stripped 开头的条目（仅限 stripped 长度 ≥ 2，避免单字误触）
    if stripped and len(stripped) >= 2:
        for key, entry in db.items():
            if key.startswith(stripped):
                return {
                    "lat": entry["lat"],
                    "lon": entry["lon"],
                    "source": "ancient_fuzzy",
                    "modern": entry["modern"],
                    "note": f"模糊匹配到「{key}」，请核实是否正确。{entry['note']}",
                }

    return None


# ══════════════════════════════════════════════════════════════════
#  Nominatim API 查询
# ══════════════════════════════════════════════════════════════════

def _query_nominatim(query: str, country_bias: str = "", expected_country_codes: list[str] | None = None) -> dict | None:
    """
    调用 OSM Nominatim API 查询地名坐标。
    country_bias: 国家代码限定（如 "cn"），可选。
    expected_country_codes: 期望结果落在的国家代码列表（如 ["kz","uz","tm"]），
                            若返回结果不在列表中则视为误匹配，返回 None。
    遵守 Nominatim 使用规则：每秒最多 1 次请求。
    """
    params = {
        "q": query,
        "format": "json",
        "limit": "1",
        "addressdetails": "1",  # 包含国家代码，用于校验
    }
    if country_bias:
        params["countrycodes"] = country_bias

    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers=NOMINATIM_HEADERS)

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data:
                item = data[0]
                # 若指定了期望国家，校验返回结果
                if expected_country_codes:
                    got_cc = (item.get("address") or {}).get("country_code", "")
                    if got_cc and got_cc.lower() not in [c.lower() for c in expected_country_codes]:
                        return None  # 国家不符，视为误匹配
                return {
                    "lat": float(item["lat"]),
                    "lon": float(item["lon"]),
                    "source": "nominatim",
                    "modern": item.get("display_name", query).split(",")[0].strip(),
                    "note": "",
                }
    except (urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, KeyError):
        pass

    return None


# ══════════════════════════════════════════════════════════════════
#  主函数：单个地名解析
# ══════════════════════════════════════════════════════════════════

def geocode(place: str, modern_hint: str = "", country_bias: str = "",
            expected_country_codes: list[str] | None = None,
            en_fallback: str = "") -> dict:
    """
    解析单个地名，返回：
    {
      "place_raw": str,      # 原始输入地名
      "lat": float | None,
      "lon": float | None,
      "source": str,         # "ancient" | "ancient_fuzzy" | "nominatim" | "unresolved"
      "modern": str,         # 现代对应地名
      "note": str,           # 附加说明（含警告）
      "resolved": bool,
    }

    参数：
      country_bias: Nominatim countrycodes 限定（如 "cn"），默认空=全球
      expected_country_codes: 期望结果落在的国家代码（如 ["kz","uz"]），
                              不符时自动重试 en_fallback
      en_fallback: 当中文查询结果不符预期国家时，改用此英文名重试
    """
    result_base = {
        "place_raw": place,
        "lat": None,
        "lon": None,
        "source": "unresolved",
        "modern": modern_hint or place,
        "note": "",
        "resolved": False,
    }

    if not place or not place.strip():
        return result_base

    place = place.strip()

    # ── 1. 先查本地古地名表 ──
    ancient_result = _lookup_ancient(place)
    if ancient_result:
        result_base.update(ancient_result)
        result_base["place_raw"] = place
        result_base["resolved"] = True
        return result_base

    # ── 2. 用 modern_hint 或原名查 Nominatim ──
    queries = []
    if modern_hint and modern_hint != place:
        queries.append(modern_hint)
    queries.append(place)

    # 如果有后缀，也尝试去掉后缀查
    stripped = place
    for suffix in PLACE_SUFFIXES:
        if place.endswith(suffix):
            stripped = place[: -len(suffix)]
            queries.append(stripped)
            break

    for query in queries:
        time.sleep(NOMINATIM_DELAY)
        nom_result = _query_nominatim(query, country_bias=country_bias,
                                      expected_country_codes=expected_country_codes)
        if nom_result:
            result_base.update(nom_result)
            result_base["place_raw"] = place
            result_base["resolved"] = True
            return result_base

    # ── 3. 若有英文 fallback，再试一次 ──
    if en_fallback:
        time.sleep(NOMINATIM_DELAY)
        nom_result = _query_nominatim(en_fallback, country_bias=country_bias,
                                      expected_country_codes=expected_country_codes)
        if nom_result:
            nom_result["source"] = "nominatim_en"
            result_base.update(nom_result)
            result_base["place_raw"] = place
            result_base["resolved"] = True
            return result_base

    # ── 4. 全部失败 → unresolved ──
    result_base["note"] = f"无法自动解析坐标，请手动确认「{place}」的位置"
    return result_base


# ══════════════════════════════════════════════════════════════════
#  批量解析（自动限速）
# ══════════════════════════════════════════════════════════════════

def geocode_batch(places: list[dict], verbose: bool = True, country_bias: str = "",
                  expected_country_codes: list[str] | None = None) -> list[dict]:
    """
    批量解析地名。

    参数:
      places: list of dict，每个 dict 至少包含 "place_raw"，可选字段：
              - "place_modern": 现代地名提示，优先用于查询
              - "en_name": 英文名，中文查询失败或国家不符时自动 fallback
      verbose: 是否打印进度
      country_bias: Nominatim countrycodes 限定（如 "cn"=中国，默认空=全球）
      expected_country_codes: 期望结果落在的国家代码列表（如 ["kz","uz","tm","tj","kg"]），
                              配合 en_name 可防止中文地名误匹配到其他国家

    返回:
      同输入 list，每个 dict 补充了 lat/lon/geo_source/resolved/coord_note 字段
    """
    results = []
    total = len(places)

    for i, wp in enumerate(places):
        place_raw = wp.get("place_raw", "")
        place_modern = wp.get("place_modern", "")
        en_name = wp.get("en_name", "")

        if verbose:
            print(f"  [{i+1}/{total}] 解析「{place_raw}」...", end=" ", flush=True)

        geo = geocode(place_raw, modern_hint=place_modern, country_bias=country_bias,
                      expected_country_codes=expected_country_codes, en_fallback=en_name)

        # 合并到 waypoint dict
        merged = dict(wp)
        merged["lat"] = geo["lat"]
        merged["lon"] = geo["lon"]
        merged["geo_source"] = geo["source"]
        merged["coord_note"] = geo["note"]
        merged["resolved"] = geo["resolved"]
        if not merged.get("place_modern") and geo.get("modern"):
            merged["place_modern"] = geo["modern"]

        if verbose:
            status = "✅" if geo["resolved"] else "⚠️ 未解析"
            print(status)

        results.append(merged)

    return results


# ══════════════════════════════════════════════════════════════════
#  统计报告
# ══════════════════════════════════════════════════════════════════

def geocode_report(results: list[dict]) -> str:
    """
    生成 CHECKPOINT 用的解析报告字符串。
    """
    total = len(results)
    resolved = [r for r in results if r.get("resolved")]
    unresolved = [r for r in results if not r.get("resolved")]
    ancient = [r for r in resolved if r.get("geo_source", "").startswith("ancient")]
    nominatim = [r for r in resolved if r.get("geo_source") == "nominatim"]

    lines = [
        f"坐标解析完成：{len(resolved)}/{total} 成功",
        f"  · 本地古地名表命中：{len(ancient)} 处",
        f"  · OSM 在线解析：{len(nominatim)} 处",
    ]

    if unresolved:
        lines.append(f"\n⚠️  以下 {len(unresolved)} 处无法自动解析坐标，需手动确认：")
        for r in unresolved:
            lines.append(f"  - 「{r['place_raw']}」{r.get('coord_note', '')}")

    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  命令行入口（用于测试）
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("用法: python geocode.py <地名1> [地名2] ...")
        print("示例: python geocode.py 昆明府 大理府 玉门关 龙井坡")
        sys.exit(0)

    places = [{"place_raw": p} for p in sys.argv[1:]]
    results = geocode_batch(places, verbose=True)

    print()
    print(geocode_report(results))
    print()
    print("详细结果：")
    for r in results:
        status = "✅" if r["resolved"] else "❌"
        print(f"  {status} {r['place_raw']}")
        if r.get("lat"):
            print(f"       坐标: ({r['lat']:.4f}, {r['lon']:.4f})")
            print(f"       来源: {r['geo_source']}  现代地名: {r.get('place_modern', '-')}")
        if r.get("coord_note"):
            print(f"       备注: {r['coord_note']}")

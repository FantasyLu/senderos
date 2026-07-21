#!/usr/bin/env python3
"""
parse_ebook.py — 将 ePub / MOBI 文件解析为结构化章节列表

支持格式：
  - .epub   → 直接用 ebooklib 解析
  - .mobi   → 用 mobi 库解压为 epub，再走同一流程
  - .azw3   → 同 mobi（kindle 格式，mobi 库支持）

输出格式：
  [
    {
      "chapter_num":   1,
      "chapter_title": "第一回·灵根育孕源流出 心性修持大道生",
      "text":          "……正文……",
      "word_count":    3200,
    },
    …
  ]

用法：
  # 作为模块
  from parse_ebook import parse_ebook
  chapters = parse_ebook("book.epub")
  chapters = parse_ebook("book.mobi")

  # 命令行（输出 JSON）
  python parse_ebook.py book.epub
  python parse_ebook.py book.mobi --output chapters.json
  python parse_ebook.py book.epub --preview        # 只打印各章节摘要
"""

import sys
import os
import re
import json
import tempfile
import shutil
import argparse
from pathlib import Path


# ══════════════════════════════════════════════════════════════════
#  依赖检查（友好提示）
# ══════════════════════════════════════════════════════════════════

def _require(module_name: str, install_hint: str):
    import importlib
    import importlib.util
    if importlib.util.find_spec(module_name) is None:
        print(f"❌ 缺少依赖：{module_name}")
        print(f"   请运行：{install_hint}")
        sys.exit(1)


# ══════════════════════════════════════════════════════════════════
#  HTML → 纯文本（去除标签）
# ══════════════════════════════════════════════════════════════════

def _html_to_text(html_content: str) -> str:
    """将 HTML 内容转为纯文本，保留段落换行。"""
    try:
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html_content, "lxml")

        # 删除 script / style
        for tag in soup(["script", "style"]):
            tag.decompose()

        # 块级元素后加换行
        for tag in soup.find_all(["p", "div", "br", "h1", "h2", "h3",
                                   "h4", "h5", "h6", "li", "tr"]):
            tag.append("\n")

        text = soup.get_text(separator="")
    except Exception:
        # bs4 不可用时降级：正则粗暴去标签
        text = re.sub(r"<[^>]+>", " ", html_content)

    # 清理多余空行
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  判断是否为章节标题
# ══════════════════════════════════════════════════════════════════

# 匹配常见章节标题格式
_CHAPTER_PATTERNS = [
    re.compile(r"^(第[零一二三四五六七八九十百千\d]+[回章节篇卷部])"),  # 中文章节
    re.compile(r"^(chapter\s*\d+)", re.IGNORECASE),                    # 英文 chapter
    re.compile(r"^(part\s*\d+)", re.IGNORECASE),                       # part
    re.compile(r"^(卷[零一二三四五六七八九十\d]+)"),                     # 卷
    re.compile(r"^(\d+[\.、\s])"),                                      # 数字开头
    re.compile(r"^(序|序言|前言|引言|后记|跋|附录)"),                    # 特殊章节
]

def _looks_like_chapter_title(text: str) -> bool:
    text = text.strip()
    if not text or len(text) > 80:
        return False
    for p in _CHAPTER_PATTERNS:
        if p.search(text):
            return True
    return False


# ══════════════════════════════════════════════════════════════════
#  epub 解析
# ══════════════════════════════════════════════════════════════════

def _parse_epub(epub_path: str) -> list[dict]:
    """
    解析 epub 文件，返回章节列表。
    每个章节对应 epub 中的一个 spine item（HTML 文件）。
    """
    _require("ebooklib", "pip install ebooklib")
    _require("bs4", "pip install beautifulsoup4")

    from ebooklib import epub, ITEM_DOCUMENT
    import warnings
    # ebooklib 有一些无害的 UserWarning，抑制掉
    warnings.filterwarnings("ignore", category=UserWarning, module="ebooklib")

    book = epub.read_epub(epub_path)

    # 获取阅读顺序（spine）
    spine_ids = [item_id for item_id, _ in book.spine]

    chapters = []
    chapter_num = 0

    for item_id in spine_ids:
        item = book.get_item_with_id(item_id)
        if item is None or item.get_type() != ITEM_DOCUMENT:
            continue

        html = item.get_body_content()
        if isinstance(html, bytes):
            html = html.decode("utf-8", errors="replace")

        text = _html_to_text(html)
        if not text.strip():
            continue

        # 尝试从内容第一行提取章节标题
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        first_line = lines[0] if lines else ""

        # 也尝试从 epub 元数据的 title 属性获取
        item_title = getattr(item, "title", "") or ""

        if item_title and len(item_title) < 80:
            title = item_title.strip()
        elif _looks_like_chapter_title(first_line):
            title = first_line
            # 正文去掉第一行（已作为标题）
            text = "\n".join(lines[1:])
        else:
            title = first_line[:40] if first_line else f"第{chapter_num+1}节"

        chapter_num += 1
        chapters.append({
            "chapter_num":   chapter_num,
            "chapter_title": title,
            "text":          text.strip(),
            "word_count":    len(text.replace(" ", "").replace("\n", "")),
        })

    return chapters


# ══════════════════════════════════════════════════════════════════
#  mobi / azw3 解析
# ══════════════════════════════════════════════════════════════════

def _parse_mobi(mobi_path: str) -> list[dict]:
    """
    解析 mobi/azw3 文件。
    mobi 库解压后产生 HTML 文件，按 <h1>/<h2> 标题分割为章节。
    解压产生的临时目录在解析完成后自动清理。
    """
    _require("mobi", "pip install mobi")
    _require("bs4", "pip install beautifulsoup4")

    import mobi as mobi_lib
    from bs4 import BeautifulSoup

    tmp_dir, html_path = mobi_lib.extract(mobi_path)
    try:
        if not html_path or not os.path.exists(html_path):
            raise RuntimeError(f"mobi 解压失败，未找到 HTML 输出：{html_path}")

        raw = Path(html_path).read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(raw, "lxml")

        # 删除 script/style
        for tag in soup(["script", "style"]):
            tag.decompose()

        # 按 h1/h2/h3 分割章节
        chapters = []
        chapter_num = 0
        current_title = "前言"
        current_blocks = []

        heading_tags = {"h1", "h2", "h3"}

        for elem in soup.body.descendants if soup.body else []:
            if not hasattr(elem, "name") or elem.name is None:
                continue
            if elem.name in heading_tags:
                # 保存上一章
                text = "\n".join(
                    b.strip() for b in current_blocks if b.strip()
                )
                if text.strip():
                    chapter_num += 1
                    chapters.append({
                        "chapter_num":   chapter_num,
                        "chapter_title": current_title,
                        "text":          text.strip(),
                        "word_count":    len(text.replace(" ", "").replace("\n", "")),
                    })
                current_title = elem.get_text(strip=True) or f"第{chapter_num+1}章"
                current_blocks = []
            elif elem.name in ("p", "div", "li", "blockquote"):
                t = elem.get_text(separator=" ", strip=True)
                if t:
                    current_blocks.append(t)

        # 最后一章
        text = "\n".join(b.strip() for b in current_blocks if b.strip())
        if text.strip():
            chapter_num += 1
            chapters.append({
                "chapter_num":   chapter_num,
                "chapter_title": current_title,
                "text":          text.strip(),
                "word_count":    len(text.replace(" ", "").replace("\n", "")),
            })

        # 若没有任何标题标签，整本作为单章
        if not chapters:
            full_text = _html_to_text(raw)
            if full_text.strip():
                chapters.append({
                    "chapter_num":   1,
                    "chapter_title": Path(mobi_path).stem,
                    "text":          full_text.strip(),
                    "word_count":    len(full_text.replace(" ", "").replace("\n", "")),
                })

        return chapters
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ══════════════════════════════════════════════════════════════════
#  公共入口
# ══════════════════════════════════════════════════════════════════

def parse_ebook(file_path: str) -> list[dict]:
    """
    解析 epub/mobi/azw3 文件，返回结构化章节列表。

    参数：
      file_path — 书籍文件的绝对或相对路径

    返回：
      [
        {
          "chapter_num":   int,    # 章节序号（从1开始）
          "chapter_title": str,    # 章节标题
          "text":          str,    # 章节纯文本内容
          "word_count":    int,    # 字数（中文按字符计）
        },
        …
      ]

    异常：
      FileNotFoundError — 文件不存在
      ValueError        — 不支持的文件格式
      RuntimeError      — 解析失败
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在：{file_path}")

    suffix = path.suffix.lower()
    if suffix == ".epub":
        chapters = _parse_epub(str(path))
    elif suffix in (".mobi", ".azw3", ".azw"):
        chapters = _parse_mobi(str(path))
    else:
        raise ValueError(
            f"不支持的格式：{suffix}。"
            f"支持的格式：.epub / .mobi / .azw3 / .azw\n"
            f"如需处理 PDF，请使用 PDF 上传模式。"
        )

    if not chapters:
        raise RuntimeError("解析完成但未找到任何章节内容，请确认文件内容完整。")

    return chapters


# ══════════════════════════════════════════════════════════════════
#  章节合并（超短章节合并到上一章）
# ══════════════════════════════════════════════════════════════════

def merge_short_chapters(chapters: list[dict], min_words: int = 200) -> list[dict]:
    """
    将字数不足 min_words 的章节合并到上一章。
    适用于 epub 中目录页、版权页等散碎 item。
    """
    merged = []
    for ch in chapters:
        if merged and ch["word_count"] < min_words:
            # 合并到上一章
            prev = merged[-1]
            prev["text"] += "\n\n" + ch["text"]
            prev["word_count"] += ch["word_count"]
            prev["chapter_title"] += f" / {ch['chapter_title']}"
        else:
            merged.append(dict(ch))
    # 重新编号
    for i, ch in enumerate(merged, 1):
        ch["chapter_num"] = i
    return merged


# ══════════════════════════════════════════════════════════════════
#  摘要报告（供 SKILL 调用显示给用户）
# ══════════════════════════════════════════════════════════════════

def ebook_summary(chapters: list[dict]) -> str:
    """生成章节摘要，供 CHECKPOINT 前展示给用户确认。"""
    total_words = sum(ch["word_count"] for ch in chapters)
    lines = [
        f"共解析 {len(chapters)} 个章节，总计约 {total_words:,} 字",
        "",
        f"{'#':<4} {'章节标题':<40} {'字数':>6}",
        f"{'-'*4} {'-'*40} {'-'*6}",
    ]
    for ch in chapters:
        title = ch["chapter_title"][:38]
        lines.append(f"{ch['chapter_num']:<4} {title:<40} {ch['word_count']:>6,}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════
#  命令行入口
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="解析 epub/mobi 电子书为结构化章节 JSON"
    )
    parser.add_argument("input", help="输入文件路径（.epub / .mobi / .azw3）")
    parser.add_argument("--output", "-o", help="输出 JSON 文件路径（默认打印到 stdout）")
    parser.add_argument("--preview", action="store_true", help="仅打印章节摘要，不输出全文")
    parser.add_argument("--min-words", type=int, default=200,
                        help="超短章节合并阈值（默认 200 字）")
    args = parser.parse_args()

    try:
        print(f"📖 正在解析：{args.input}", file=sys.stderr)
        chapters = parse_ebook(args.input)
        chapters = merge_short_chapters(chapters, min_words=args.min_words)
        print(f"✅ 解析完成", file=sys.stderr)
        print(ebook_summary(chapters), file=sys.stderr)

        if args.preview:
            sys.exit(0)

        output_data = json.dumps(chapters, ensure_ascii=False, indent=2)

        if args.output:
            Path(args.output).write_text(output_data, encoding="utf-8")
            print(f"\n📄 已写入：{args.output}", file=sys.stderr)
        else:
            print(output_data)

    except (FileNotFoundError, ValueError, RuntimeError) as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(1)

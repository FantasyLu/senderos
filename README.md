# senderos

> *"El jardín de senderos que se bifurcan"*
> ——《小径分叉的花园》，豪尔赫·路易斯·博尔赫斯，1941

**senderos**（西班牙语，"小径"）取名自博尔赫斯的短篇小说《小径分叉的花园》。小说中，花园是一座无限分叉的迷宫，每一条小径代表一个可能的时间分支，所有路径并行存在、互不排斥。

这个 skill 试图做类似的事情：把一个人一生或一次旅程中走过的所有小径，从文字中还原成地图上的线条。

---

## 功能概述

senderos 是一个路线绘图 AI Skill，将人物行程（游记、传记、历史文献、检索资料）转化为**可在浏览器中交互的地理路线图**。

核心输出：一个自包含的 HTML 文件，内嵌：
- OpenStreetMap 底图（无需 API Key）
- 分层图钉（主要节点 / 途经点），颜色区分置信度
- **流动动画路线**：蚂蚁线（ant-path）展示行进方向与时序
- 两种路线模式可切换：**同时流动** / **依次点亮**（含播放/暂停/重置控制）
- 左侧时间轴侧边栏，点击定位并联动路线显示
- 阶段切换器（按章节/年份筛选显示）

---

## 两种工作模式

### Mode A — 书籍解析

用户上传书籍文件，AI 自动：
1. 识别目标人物，确认别名
2. 按章节分段，逐段提取地名、时间、行为
3. 执行 source_sentence 反查校验，标注置信度
4. 跨章合并去重，解析坐标
5. 输出 CHECKPOINT 确认表格，等用户确认后渲染地图

**支持格式：**

| 格式 | 扩展名 | 解析方式 |
|------|--------|----------|
| PDF  | `.pdf` | 直接文字提取 |
| ePub | `.epub` | `parse_ebook.py` → ebooklib，按 spine 分章 |
| Kindle | `.mobi` / `.azw3` / `.azw` | `parse_ebook.py` → mobi 库解压 → HTML 按标题分章 |

> epub/mobi 先转为结构化章节 JSON，再走与 PDF 完全相同的提取流程。

### Mode B — 人物检索

用户指定人物名（历史或现代），AI 主动检索：
1. 查询传记、年谱、历史文献
2. 提取地理信息，推断空白路段
3. CHECKPOINT 确认，渲染地图

两种模式可混合：用户上传 PDF + AI 补充网络资料。

---

## 地图特性

| 特性 | 说明 |
|------|------|
| 路线动画 | leaflet-ant-path 蚂蚁线，白色光点沿路线方向流动 |
| 同时流动模式 | 全程路线同时显示并流动，适合总览 |
| 依次点亮模式 | 路段按时序自动追加，含播放/暂停/重置，支持时间轴联动跳转 |
| 图钉置信度 | 深橙（confirmed）/ 橙（single_source）/ 金（inferred）/ 红（disputed）|
| 路径类型 | 实线（有记载）/ 虚线（推测直线）|
| 坐标解析 | 优先查本地古地名库（130+ 条），次用 OSM Nominatim |
| 阶段切换 | 按章节/年份分组，顶部切换器一键筛选 |
| 完全离线可用 | CDN 资源加载后，HTML 文件无需联网即可使用 |

---

## 文件结构

```
senderos/
├── SKILL.md                          # Skill 主文件（AI 执行规范）
├── README.md                         # 本文件
├── scripts/
│   ├── parse_ebook.py                # epub/mobi/azw3 → 结构化章节列表
│   ├── geocode.py                    # 地名 → 坐标解析（本地库 + OSM）
│   └── render_map.py                 # waypoints JSON → Leaflet HTML
├── references/
│   ├── map-template.html             # Leaflet 地图模板
│   └── ancient-place-names.md        # 历史地名坐标库（130+ 条）
├── output/                           # 生成的地图和中间文件
│   ├── 徐霞客_route.html
│   ├── 徐霞客_waypoints_draft.json
│   ├── 徐霞客_waypoints_render.json
│   ├── 中亚行纪_法特兰_route.html
│   ├── 中亚行纪_法特兰_waypoints_draft.json
│   └── 中亚行纪_法特兰_waypoints_render.json
└── examples/
    ├── mode-b-xu-xiake/              # 示例：徐霞客人生游历（Mode B）
    └── mode-a-central-asia/          # 示例：《中亚行纪》埃丽卡·法特兰（Mode A）
```

---

## 安装

### 方式一：让 Agent 自动安装（推荐）

直接把链接发给你正在用的 AI Agent：

```
帮我安装这个 skill：https://github.com/FantasyLu/senderos
```

Agent 会自动完成克隆、依赖安装和验证，无需手动操作。

### 方式二：手动安装

<details>
<summary>展开查看各 runtime 的 skills 目录</summary>

| Runtime | 安装路径 |
|---|---|
| Claude Code | `~/.claude/skills/senderos/` |
| Codex CLI | `~/.codex/skills/senderos/` |
| Cursor | `~/.cursor/skills/senderos/` |
| 其他 runtime | clone 到对应 runtime 的 `skills/` 目录 |

</details>

```bash
# 1. 克隆到对应 runtime 的 skills 目录（以 Claude Code 为例）
git clone https://github.com/FantasyLu/senderos.git ~/.claude/skills/senderos

# 2. 安装 Python 依赖（epub/mobi 解析需要，地图渲染无需额外依赖）
pip install ebooklib beautifulsoup4 mobi

# 3. 重启 Agent，输入触发词「画路线」即可激活
```

> macOS 系统 Python 可能需要加 `--break-system-packages`：
> `pip install ebooklib beautifulsoup4 mobi --break-system-packages`

### 方式三：直接粘贴使用

即使你的 runtime 不支持 skills 自动加载，也可以直接把 `SKILL.md` 的内容粘贴进对话——它本质就是一份 markdown 指令文件，粘贴即生效。

---

## 快速使用

### 触发词

```
画路线 / 绘制行程 / 画地图 / 旅行轨迹 / 路线图
route map / travel route / 行程地图 / 人物路线
```

### Mode A（书籍文件）

```
用户上传《徐霞客游记》.epub → 「画出徐霞客的路线图」
用户上传旅行日记 .mobi      → 「画书中作者的行程」
```

### Mode B（人物检索）

```
「帮我画马可·波罗的旅行路线」
「画出玄奘西行取经的路线」
「徐霞客一生去了哪些地方，帮我画出来」
```

---

## 脚本直接调用

```bash
# 解析地名坐标
python scripts/geocode.py 昆明府 大理府 丽江府

# 生成地图（所有节点流动）
python scripts/render_map.py output/waypoints.json output/map.html \
  --title "徐霞客路线" \
  --subtitle "1607–1641" \
  --cluster \
  --stages
```

waypoints JSON 格式参见 `scripts/render_map.py` 文件头部注释，或参考 `output/` 目录中的示例文件。

---

## 示例

### 示例 1：徐霞客人生游历（Mode B）

基于维基百科《徐霞客游记》年表，检索并提取徐霞客 1607–1641 年间的完整游历路线，覆盖 16 个省，34 个地理节点，**34/34 坐标全部解析成功**（本地库 8 处，OSM 26 处）。

路线从太湖出发，经山东、浙江沿海、安徽、福建、江西、中原五岳，最终深入云南腾冲（最西端），1640 年昆明得病，1641 年返回江阴病逝。

输出文件：`examples/mode-b-xu-xiake/route.html`

详见：[examples/mode-b-xu-xiake/](examples/mode-b-xu-xiake/)

### 示例 2：《中亚行纪》埃丽卡·法特兰（Mode A）

从 epub 电子书中提取挪威旅行作家埃丽卡·法特兰（Erika Fatland）的中亚五国旅行路线，37 个地理节点，**37/37 坐标全部解析成功**。

路线覆盖土库曼斯坦（达瓦扎"地狱之门"）→ 哈萨克斯坦（咸海干涸现场、苏联核试验区）→ 塔吉克斯坦（帕米尔高原霍罗格）→ 吉尔吉斯斯坦（传统养鹰人、族群冲突旧址）→ 乌兹别克斯坦（希瓦古城、撒马尔罕）。

输出文件：`examples/mode-a-central-asia/route.html`

详见：[examples/mode-a-central-asia/](examples/mode-a-central-asia/)

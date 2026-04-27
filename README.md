# DJ Set Curator 🎧

基于锚点歌曲的智能 DJ 选曲工具，调用网易云音乐 MCP Server 自动构建**可混音的 DJ Set 歌单**。

> v0.2.0 核心升级：从"相似歌曲推荐"进化为"过渡感知的 DJ Set 构建器"

## 功能特性

- **锚点驱动**：输入 1-2 首锚点歌曲，自动构建风格连贯的 Set
- **Pair-wise 过渡评分**：不再评分"单曲像不像锚点"，而是评分"下一首能不能接上一首"
  - BPM 兼容：支持 ±3% pitch、半速/倍速混音、±5/±10 BPM 分级
  - Key 过渡：DJ-proven Camelot 规则（+1 升能 / -1 降能 / ±2 张力 / relative 和谐）
  - 能量衔接：选曲时直接考虑目标能量曲线，避免事后硬重排
- **多源采集**：7 个来源同时采集，候选池 40-80 首
  - **相似推荐**（网易云官方 API）
  - **艺术家热门**
  - **相似艺人**（网易云 `/simi/artist` API，核心来源）
  - **流派搜索**（基于 BPM 推断流派关键词）
  - **同专辑**、**每日推荐**、**标签搜索**
- **粗粒度能量估计**：BPM 代理 + 歌曲名 heuristics，VIP 歌曲 100% 可用
- **能量曲线编排**：支持 flat / warm-up / peak-mid / rollercoaster / climax-end 五种模式
- **级联扩展**：候选池不足时，自动用推荐歌曲作为二级锚点继续搜索
- **锚点入单**：锚点歌曲自动加入最终歌单，作为 Set 的核心曲目
- **一键建单**：自动创建网易云歌单并批量收藏入选曲目
- **智能命名**：自动生成 `🎧 DJ Curator · {模式} · {艺人}` 格式歌单名，一眼识别来源和氛围

## 前置依赖

1. **Python 3.10+**
2. **[cloud-music-mcp-extended](https://github.com/xuzhenyang/cloud-music-mcp-extended)** 已安装并登录

   ```bash
   cd ../cloud-music-mcp-extended
   uv pip install -e .
   # 或
   pip install -e .
   ```

3. **登录网易云音乐**

   ```bash
   cloud-music-mcp
   # 在 MCP Client 中调用 cloud_music_login 扫码登录
   ```

## 安装

```bash
# 使用 uv（推荐）
cd dj-set-curator
uv pip install -e .

# 或使用 pip
pip install -e .
```

安装完成后，`dj-curator` 命令即可使用。

## 使用示例

### 自动生成歌单名（推荐）

不传 `--name` 时，自动生成方案 E 格式：

```bash
# 单锚点 → 🎧 DJ Curator · Warm Up · keshi
dj-curator -a "keshi - WANTCHU" --count 15 --arrange warm-up -v

# 多锚点 → 🎧 DJ Curator · Peak Mid · keshi × The Weeknd
dj-curator \
  -a "keshi - WANTCHU" \
  -a "The Weeknd - Blinding Lights" \
  --count 20 --arrange peak-mid
```

### 自定义歌单名

传 `--name` 时会自动包装为方案 E 格式：

```bash
# → 🎧 DJ Curator · 周五晚 · Warm Up
dj-curator -a "keshi - WANTCHU" -n "周五晚" --count 15 --arrange warm-up

# → 🎧 DJ Curator · Late Night Drive · Flat
dj-curator -a "keshi - WANTCHU" -n "Late Night Drive" --arrange flat
```

### 多锚点（打破同质化的最强手段）

```bash
dj-curator \
  -a "keshi - WANTCHU" \
  -a "The Weeknd - Blinding Lights" \
  --count 20
```

### 使用歌曲 ID

```bash
dj-curator -a "29732235" --name "Test Set" -v
```

### 详细输出

添加 `-v` / `--verbose` 参数查看完整的选曲列表和过渡评分：

```bash
dj-curator -a "Radiohead - Everything In Its Right Place" \
  --name "Chill Electronic" \
  --count 15 \
  --verbose
```

### 调整多样性

```bash
# 默认值 0.8（80% 的歌曲来自不同艺术家）
dj-curator -a "Daft Punk - One More Time" \
  --name "French House" \
  --diversity 0.5 \
  --count 25
```

### 级联扩展（候选不足时自动扩充）

```bash
dj-curator -a "keshi - WANTCHU" --name "WANTCHU vibe" --count 20 --expand
```

### 关闭级联扩展

```bash
dj-curator -a "周杰伦 - 晴天" --name "纯原推荐" --count 10 --no-expand
```

## CLI 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--anchor` | `-a` | *必填* | 锚点歌曲，可多次指定 |
| `--name` | `-n` | *可选* | 输出歌单名称（不传则自动生成） |
| `--count` | `-c` | `20` | 目标歌曲数量 (1-100) |
| `--bpm-tol` | | `5.0` | BPM 容差范围 |
| `--diversity` | `-d` | `0.8` | 多样性比例 (0-1)，越高歌单风格越多样 |
| `--arrange` | `-r` | `flat` | 能量曲线模式: flat/warm-up/peak-mid/rollercoaster/climax-end |
| `--expand` / `--no-expand` | | `True` | 候选不足时启用级联扩展 |
| `--verbose` | `-v` | `False` | 显示详细选曲列表和过渡评分 |
| `--server` | | `cloud-music-mcp` | MCP Server 命令 |

## 引擎架构

### 选曲流程（v0.2.1）

```
锚点歌曲
  ↓
多源采集（7 个来源并行）
  ├─ SimilarSource          网易云相似推荐 API
  ├─ ArtistTopSource        艺术家热门歌曲
  ├─ CrossArtistSource      相似艺人 → 热门歌曲（核心来源）
  ├─ GenreSearchSource      BPM 推断流派 → 关键词搜索
  ├─ AlbumSource            同专辑歌曲
  ├─ DailyRecSource         每日推荐模拟
  └─ TagSearchSource        标签搜索
  ↓
去重 + 过滤（ID 去重 / 歌曲名去重 / 语言一致性 / 低质内容过滤）
  ↓
粗粒度能量估计（BPM 代理 + 歌曲名 heuristics）
  ↓
音频分析（全量并发，所有缺失 BPM/Key 的候选）
  ↓
预过滤（SongFilter 过滤掉低分候选）
  ↓
贪心序列构建（SequentialSelector）
  ← 每一步选择"与当前末尾过渡分最高"的下一首
  ← 同时匹配目标能量曲线
  ↓
精能量分析（对入选歌曲做 librosa RMS）
  ↓
创建网易云歌单
```

### 多源采集详情

| 来源 | 机制 | 预期贡献 |
|------|------|----------|
| **SimilarSource** | 网易云 `simi/song` API | 10-20 首，风格最接近锚点 |
| **ArtistTopSource** | `GetArtistTracks` API | 8-12 首，同艺术家热门 |
| **CrossArtistSource** | `simi/artist` API → 热门歌曲 | 15-25 首，**真正风格相近的其他艺人** |
| **GenreSearchSource** | BPM 推断流派 → 搜索 | 3-8 首，跨流派探索 |
| **AlbumSource** | `GetAlbumInfo` API | 0-5 首（单曲专辑可能为 0） |
| **DailyRecSource** | 搜索艺术家名字 | 5-8 首 |
| **TagSearchSource** | 搜索 "artist 热门" | 3-5 首 |

**CrossArtistSource 是核心升级**：使用网易云官方 `/simi/artist` API（非搜索关键词匹配），返回真正风格相近的艺人（如 keshi → Lauv, Demxntia, The Weeknd, JVKE），再获取他们的热门歌曲。质量远高于搜索方案。

### 过滤策略

| 过滤层 | 机制 | 作用 |
|--------|------|------|
| **ID 去重** | 按 song id 去重 | 避免同一首歌出现两次 |
| **歌曲名去重** | 同名 + 同 artist 视为重复 | 避免不同版本的同一首歌 |
| **语言一致性** | 英文锚点过滤中文候选 | 避免跨语言噪音（如 keshi → 王可可） |
| **低质内容过滤** | 排除 "DJ版"、"车载版"、"抖音"、"Cover" | 提升候选质量 |
| **Artist 连续惩罚** | 同 artist 连续出现扣分递增 | 避免同质化 |

### 过渡评分维度

| 维度 | 权重 | 说明 |
|------|------|------|
| BPM 过渡 | 30% | ±3% pitch=100, 半速/倍速=85, ±5 BPM=60~100, ±10 BPM=0~60 |
| Key 过渡 | 25% | 同 key=100, +1=90, -1=85, relative=95, ±2=70, >2=10 |
| 能量方向 | 45% | 接近目标能量加分，突变 >30 分扣分 |

> Energy 权重最高（45%），确保能量曲线对选曲有显著影响。

### 调性匹配（Camelot Wheel）

| 过渡类型 | 含义 | 评分 |
|----------|------|------|
| 同 key | 完全匹配 | 100 |
| Relative (A↔B) | 大小调互换 | 95 |
| +1 顺时针 | 能量提升 | 90 |
| -1 逆时针 | 能量下降 | 85 |
| ±2 | 有轻微张力 | 70 |
| >2 | 不推荐混音 | 10 |

### 能量曲线模式

| 模式 | 描述 | 适用场景 |
|------|------|----------|
| `flat` | 能量均匀分布 | 背景播放、工作学习 |
| `warm-up` | 低→高→低（抛物线） | 派对开场 |
| `peak-mid` | 中段能量最高 | 演出核心时段 |
| `rollercoaster` | 高低交替 | 活跃氛围 |
| `climax-end` | 逐步攀升到结尾 | 收尾高潮 |

### 音频分析策略

- **粗粒度**（所有候选）：`energy = BPM × 0.5` + 歌曲名关键词 heuristics
  - 高能量词：remix / club / bass / drop → +8
  - 低能量词：acoustic / piano / sleep / chill → -12
- **全量精分析**（所有缺失 BPM/Key 的候选）：librosa `beat_track` + `chroma_cqt`，并发 batch=10
  - 不再限制前 25 首，候选池内全部分析
  - 分析结果缓存到 `analysis_cache.json`，永久复用
- **入选后精能量**（最终入选歌曲）：librosa RMS 能量分析，替换 heuristics 能量
- **VIP 歌曲**：粗粒度能量 100% 可用，不阻塞流程

### 音频分析缓存

音频片段和分析结果默认缓存到系统缓存目录：

| 平台 | 缓存路径 |
|------|----------|
| macOS | `~/Library/Caches/dj-set-curator/` |
| Linux | `~/.cache/dj-set-curator/` |
| Windows | `%LOCALAPPDATA%/dj-set-curator/` |

- `audio_segments/` — 下载的音频片段（保留 7 天，自动清理）
- `analysis_cache.json` — BPM/Key 分析结果（永久缓存，歌曲属性不变）

## 开发指南

### 项目结构

```
dj-set-curator/
├── src/dj_set_curator/
│   ├── __init__.py
│   ├── __main__.py          # python -m 入口
│   ├── cli.py               # CLI 界面
│   ├── mcp_client.py        # MCP Client 封装
│   ├── anchor.py            # 锚点歌曲解析
│   ├── curator.py           # 选曲引擎核心（v0.2.0 过渡评分架构）
│   ├── filters.py           # 预过滤引擎（Camelot Wheel + BPM）
│   ├── transition.py        # Pair-wise 过渡评分 + 贪心序列构建
│   ├── sources.py           # 多源候选采集器（7 个来源）
│   ├── arranger.py          # 能量分析器（librosa RMS）
│   ├── audio_analyzer.py    # 音频分析（BPM/Key，librosa）
│   └── models.py            # 数据模型
├── tests/
│   ├── test_anchor.py
│   ├── test_filters.py
│   └── test_curator.py
├── pyproject.toml
└── README.md
```

### 运行测试

```bash
pytest tests/ -v
```

### 作为模块运行

```bash
python -m dj_set_curator --anchor "Song Name" --name "Playlist"
```

## 注意事项

1. **登录状态**：使用前确保 `cloud-music-mcp-extended` 已完成扫码登录
2. **音频分析**：首次分析歌曲需要下载音频片段（约 5-10 秒/首），分析结果会自动缓存
3. **API 限制**：频繁调用可能被限流，建议合理使用
4. **版权**：音频分析仅使用网易云提供的试听链接，不保存完整音频文件

## 依赖项目

- [cloud-music-mcp-extended](https://github.com/xuzhenyang/cloud-music-mcp-extended) - 网易云音乐 MCP Server

## License

MIT

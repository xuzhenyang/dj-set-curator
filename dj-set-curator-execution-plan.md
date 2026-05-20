# DJ Set Curator - 开发执行计划

## 项目背景

- **cloud-music-mcp-extended**（已完成）：网易云音乐 MCP Server，提供搜索、歌单管理、相似推荐、音频 URL 等工具
- **dj-set-curator**（已完成 v0.3.2）：基于锚点歌曲的智能 DJ 选曲引擎，调用 MCP Server 完成歌单构建

---

## 一、项目结构（实际）

```
dj-set-curator/
├── src/dj_set_curator/
│   ├── __init__.py
│   ├── __main__.py              # python -m 入口
│   ├── cli.py                   # CLI 界面（argparse）
│   ├── config.py                # 配置管理（环境变量/配置文件）
│   ├── mcp_client.py            # MCP Client 封装（纯净，无风控）
│   ├── rate_limited_client.py   # 风控代理（双层锁）
│   ├── anchor.py                # 锚点歌曲解析
│   ├── curator.py               # 选曲引擎核心（orchestrator）
│   ├── filters.py               # 预过滤引擎
│   ├── genre_resolver.py        # 曲风解析器（层级树 + 缓存）
│   ├── transition.py            # Pair-wise 过渡评分 + 贪心序列构建
│   ├── sources.py               # 多源候选采集器（7 个来源）
│   ├── arranger.py              # 能量分析器（librosa 四维）
│   ├── audio_analyzer.py        # 音频分析（BPM/Key，异步下载）
│   ├── deduplicator.py          # 去重工具（ID/名称/锚点）
│   ├── energy_heuristics.py     # 能量启发式估计
│   ├── expansion.py             # 级联扩展器
│   ├── playlist_naming.py       # 歌单命名格式
│   └── models.py                # 数据模型（Song / AnchorSong / ScoredSong）
├── scripts/
│   └── login.py                 # 独立二维码登录脚本
├── tests/
│   ├── test_anchor.py
│   ├── test_filters.py
│   └── test_curator.py
├── pyproject.toml
└── README.md
```

---

## 二、技术栈

- Python 3.10+
- `mcp` SDK（作为 MCP Client）
- `librosa`（音频分析：BPM/Key/能量/结构）
- `argparse`（CLI，实际采用而非 typer）
- `pyyaml`（配置文件）
- `pytest` + `pytest-asyncio`（测试）

---

## 三、已实现模块

### Phase 1：MCP Client 封装 ✅

**文件**: `mcp_client.py`

```python
class CloudMusicMCPClient:
    """封装 cloud-music-mcp-extended 的 MCP 调用"""
    
    async def search_song(self, keyword: str) -> list[Song]
    async def get_song_detail(self, song_id: str) -> dict
    async def get_similar_songs(self, song_id: str, limit: int = 20) -> list[Song]
    async def get_audio_url(self, song_id: str) -> dict  # {url, br, type, duration}
    async def create_playlist(self, name: str, privacy: bool = False) -> str
    async def add_tracks_to_playlist(self, playlist_id: str, track_ids: list)
    async def get_song_wiki(self, song_id: str) -> dict  # 曲风标签
```

**原则**: 保持纯净，不包含任何风控/间隔逻辑。

---

### Phase 2：风控代理 ✅（v0.3.2 新增）

**文件**: `rate_limited_client.py`

```python
class RateLimitedMCPClient:
    """MCP Client 代理：双层风控间隔"""
    
    def __init__(self, real_client, global_interval=0.5, audio_interval=2.0)
    
    # 全局锁 0.5s：所有 API 调用之间间隔
    # audio 专用锁 2.0s：get_audio_url 额外保护
    # __getattr__ 动态拦截所有 async 方法
```

**设计决策**: 代理模式，不修改 mcp_client.py。`curator.py` 在 `build_playlist` 开头创建 `mcp_rl`，所有 `self.mcp` 替换为 `mcp_rl`。

---

### Phase 3：锚点分析 ✅

**文件**: `anchor.py`

- 支持 "Artist - Song" 格式解析
- 支持纯数字 ID 直接查询
- 支持模糊搜索并返回最佳匹配
- 并行解析多个锚点

---

### Phase 4：多源采集 ✅

**文件**: `sources.py`

| 来源 | 机制 | 预期贡献 |
|------|------|----------|
| SimilarSource | 网易云 `simi/song` API | 10-20 首 |
| ArtistTopSource | `GetArtistTracks` API | 15-20 首 |
| CrossArtistSource | `simi/artist` API → 热门歌曲 (8×5) | 20-40 首 |
| StyleSongSource | 曲风标签 `/style-tag/home/song` | 10-15 首 |
| PlaylistSource | 搜索精选歌单 → 提取曲目 | 5-15 首 |
| GenreSearchSource | 曲风层级树 tagId 搜索 / BPM fallback | 5-10 首 |
| AlbumSource | `GetAlbumInfo` API | 0-5 首 |

**采集策略**: 并发采集（`asyncio.gather`）+ 每个 source 30 秒超时保护。

---

### Phase 5：去重与过滤 ✅

**文件**: `deduplicator.py`, `filters.py`

| 过滤层 | 机制 |
|--------|------|
| ID 去重 | 按 song id 去重 |
| 歌曲名去重 | 同名 + 同 artist 视为重复 |
| 语言一致性 | 英文锚点过滤中文候选 |
| 低质内容过滤 | 排除 "DJ版"、"车载版"、"抖音"、"Cover" |
| Artist 连续惩罚 | 同 artist 连续出现扣分递增 |
| 曲风兼容性过滤 | 层级树评分 < 25 大幅降分 |

---

### Phase 6：曲风解析 ✅

**文件**: `genre_resolver.py`

- 网易云官方 644 个曲风标签的三级层级树
- 复合标签自动拆分（`流行-欧美流行` → `流行` + `欧美流行`）
- 兼容性评分：相同=100 / 祖孙=85 / 兄弟=70 / 同根=40 / 不同=10

---

### Phase 7：能量分析 ✅

**文件**: `energy_heuristics.py`, `arranger.py`, `audio_analyzer.py`

| 层级 | 方法 | 覆盖 |
|------|------|------|
| 粗粒度 | `BPM × 0.5` + 歌曲名 heuristics | 所有候选 |
| 精分析 | librosa `beat_track` + `chroma_cqt` | 缺失 BPM/Key 的候选 |
| 精能量 | RMS 响度 + 节奏密度 + 低频占比 + 频谱质心 | 最终入选歌曲 |
| 结构检测 | intro 长度 + breakdown 位置 | 最终入选 + 锚点 |

**缓存策略**:
- 音频片段：系统缓存目录，保留 7 天自动清理
- 分析结果：`analysis_cache.json`，永久缓存

---

### Phase 8：Pair-wise 过渡评分 ✅

**文件**: `transition.py`

| 维度 | 权重 | 规则 |
|------|------|------|
| BPM 过渡 | 30% | ±3% pitch=100, 半速/倍速=85, ±5 BPM=60~100, ±10 BPM=0~60 |
| Key 过渡 | 25% | 同 key=100, +1=90, -1=85, relative=95, ±2=70, >2=10 |
| 能量方向 | 45% | 接近目标能量加分，突变 >30 分扣分 |

**序列构建**: 贪心算法（SequentialSelector），每一步选择"与当前末尾过渡分最高"的下一首，同时匹配目标能量曲线。

---

### Phase 9：动态能量曲线 ✅

| 模式 | 描述 |
|------|------|
| flat | 能量均匀分布 |
| warm-up | 低→高→低（抛物线） |
| peak-mid | 中段能量最高 |
| rollercoaster | 高低交替 |
| climax-end | 逐步攀升到结尾 |

基于锚点能量分布的均值/标准差自适应。

---

### Phase 10：级联扩展 ✅

**文件**: `expansion.py`

候选池不足时，自动用推荐歌曲作为二级锚点继续搜索。

---

### Phase 11：CLI 界面 ✅

**文件**: `cli.py`

使用 `argparse` 而非 `typer`（依赖里保留了 typer 但未实际使用）。

```bash
dj-curator create -a "keshi - WANTCHU" --count 20 --arrange warm-up -v
dj-curator create -a "29732235" --dry-run
dj-curator config --mcp-server /path/to/server
```

---

### Phase 12：歌单命名 ✅

**文件**: `playlist_naming.py`

自动生成 `[DJ Curator] {模式} | {艺人}` 格式。

---

## 四、选曲主流程（curator.py）

```
build_playlist()
├── 1. 解析锚点（anchor.py）
├── 2. 包装风控代理（rate_limited_client.py）
├── 3. 分析锚点 BPM/Key + 精能量（audio_analyzer.py + arranger.py）
├── 4. 预加载曲风层级树（genre_resolver.py）
├── 5. 多源采集候选（sources.py，7 个来源并发）
├── 6. 去重（deduplicator.py）
├── 7. 曲风解析（genre_resolver.py）
├── 8. 级联扩展（expansion.py，如候选不足）
├── 9. 粗粒度能量估计（energy_heuristics.py）
├── 10. 批量音频分析（audio_analyzer.py，串行，受风控代理间隔约束）
├── 11. 预过滤（filters.py）
├── 12. 贪心序列构建（transition.py）
├── 13. 精能量分析（arranger.py，对入选歌曲）
├── 14. 组装最终歌单（锚点 + 选中歌曲）
└── 15. 创建歌单（mcp_rl.create_playlist + add_tracks_to_playlist）
```

**时间预算**（600s 上限）：
- 锚点分析 + 采集：~60s
- 批量音频分析（80 首 × 2.0s）：~160s
- 序列构建 + 精分析：~30s
- 创建歌单：~10s
- 总计：~260s（充裕）

---

## 五、已知限制与降级方案

| 限制 | 降级方案 |
|------|----------|
| BPM/Key 数据缺失 | 粗粒度能量估计（BPM 代理 + heuristics） |
| 音频分析超时（300s） | 自动跳过，剩余候选用 heuristics 能量继续 |
| MCP Server 未登录 | 明确错误提示并引导登录 |
| get_audio_url 风控 | 风控代理双层锁（全局 0.5s + audio 2.0s） |
| 搜索 API 风控 | 多源采集容错，单个 source 失败不影响其他 |

---

## 六、交付物清单

| 交付物 | 路径 | 说明 |
|--------|------|------|
| MCP Client 封装 | `mcp_client.py` | 纯净 MCP 调用 |
| 风控代理 | `rate_limited_client.py` | 双层锁代理 |
| 锚点分析 | `anchor.py` | 解析锚点歌曲 |
| 多源采集 | `sources.py` | 7 个来源并发采集 |
| 去重工具 | `deduplicator.py` | ID/名称/锚点去重 |
| 预过滤引擎 | `filters.py` | Camelot + BPM + 多样性 + 曲风 |
| 曲风解析器 | `genre_resolver.py` | 层级树 + 缓存 |
| 过渡评分 | `transition.py` | Pair-wise + 贪心序列 |
| 能量分析器 | `arranger.py` | librosa 四维 |
| 音频分析 | `audio_analyzer.py` | BPM/Key + 异步下载 |
| 能量启发式 | `energy_heuristics.py` | BPM 代理 + 关键词 |
| 级联扩展 | `expansion.py` | 候选不足自动扩充 |
| 歌单命名 | `playlist_naming.py` | 智能命名格式 |
| 选曲核心 | `curator.py` | 完整 orchestrator |
| CLI 界面 | `cli.py` | 命令行交互 |
| 数据模型 | `models.py` | Song / AnchorSong / ScoredSong |
| 配置管理 | `config.py` | 环境变量/配置文件 |
| 测试套件 | `tests/` | pytest 测试 |
| 项目配置 | `pyproject.toml` | 依赖与脚本入口 |
| 使用文档 | `README.md` | 完整使用指南 |
| 架构文档 | `dj-mcp-architecture.md` | 架构设计 |
| 执行计划 | `dj-set-curator-execution-plan.md` | 本文件 |

---

## 七、版本演进记录

| 版本 | 核心变化 |
|------|----------|
| v0.1.0 | 基础架构：MCP Client + 锚点分析 + 相似推荐 + 排序 |
| v0.2.0 | 多源采集重构：7 个来源 + 曲风层级树 |
| v0.3.0 | Pair-wise 过渡评分 + 动态能量曲线 + 贪心序列构建 |
| v0.3.1 | 批量音频分析 + 歌曲结构检测 + 精能量分析 |
| **v0.3.2** | **风控代理重构**：双层锁从 mcp_client 迁移到 rate_limited_client.py |

---

## 八、后续可能的优化方向

1. **风控策略动态调整**：根据实际测试结果调整 `global_interval` 和 `audio_interval`
2. **批量分析并发优化**：当前串行，可考虑多 worker + 间隔控制
3. **曲风层级树自动更新**：定期从网易云同步最新标签层级
4. **用户反馈闭环**：收集用户对生成歌单的反馈，优化评分权重
5. **支持本地音频文件作为锚点**：直接分析本地文件提取 BPM/Key
6. **输出格式扩展**：支持导出为 M3U/CSV/JSON 等格式

---

*文档版本: 2026-05-20 (同步至 v0.3.2)*
*计划生成时间: 2026-04-25*
*前置依赖: cloud-music-mcp-extended（已完成）*

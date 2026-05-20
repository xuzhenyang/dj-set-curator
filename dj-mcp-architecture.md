# DJ 智能选曲系统架构方案

## 项目拆分

```
+----------------------------------------+     +----------------------------------------+
|  project-1: cloud-music-mcp-extended   |     |  project-2: dj-set-curator             |
|  （网易云 MCP Server）                  |     |  （选曲引擎）                           |
|                                        |     |                                        |
|  · 扫码登录                             |     |  · 接收锚点歌曲                         |
|  · 搜索歌曲                            |     |  · 多源采集候选（7 个来源）             |
|  · 创建歌单                            |     |  · BPM/Key 音频分析                     |
|  · 批量收藏                            |     |  · Pair-wise 过渡评分 + 序列构建          |
|  · 获取歌单                            |     |  · 动态能量曲线                         |
|  · 相似推荐                            |     |  · 风控代理（双层锁）                    |
|  · 歌曲详情                            |     |  · 调用 MCP 创建歌单并收藏              |
|                                        |     |                                        |
|  向 AI Agent / CLI 暴露 MCP 工具        |     |  独立运行，通过 stdio 调用 MCP           |
+----------------------------------------+     +----------------------------------------+
```

---

## 项目一：cloud-music-mcp-extended

基于 [Code-MonkeyZhang/cloud-music-mcp](https://github.com/Code-MonkeyZhang/cloud-music-mcp) fork。

### MCP 工具列表

| 工具名 | 功能 |
|--------|------|
| `cloud_music_search` | 搜索歌曲 |
| `cloud_music_get_song_detail` | 获取歌曲详情（ID、名称、艺人、专辑等基础信息） |
| `cloud_music_get_similar_songs` | 获取相似歌曲推荐 |
| `cloud_music_get_audio_url` | 获取歌曲音频下载链接（用于音频分析） |
| `cloud_music_create_playlist` | 创建歌单，返回歌单 ID |
| `cloud_music_add_tracks` | 批量添加歌曲到歌单 |
| `cloud_music_get_song_wiki` | 获取歌曲音乐百科（曲风标签） |

> **注意**：BPM/Key/Camelot 调性等信息**不由 MCP Server 提供**，由 dj-set-curator 的 `audio_analyzer.py` 通过 librosa 独立分析音频片段获得。

---

## 项目二：dj-set-curator

**当前版本**: v0.3.2

### 核心能力

1. **锚点分析**
   - 用户输入 1-2 首锚点歌曲（歌名或网易云 ID）
   - 解析为标准化的 `AnchorSong` 对象

2. **多源采集（7 个来源并发）**
   - 相似推荐、艺术家热门、相似艺人、曲风标签、歌单挖掘、流派搜索、同专辑
   - 预期候选池 40-80 首

3. **音频分析**
   - 粗粒度能量估计（BPM 代理 + 歌曲名 heuristics）
   - 精分析：librosa `beat_track` + `chroma_cqt` 提取 BPM/Key
   - 入选后精能量：RMS 响度 + 节奏密度 + 低频占比 + 频谱质心
   - 歌曲结构检测：intro 长度和 breakdown 位置

4. **Pair-wise 过渡评分**
   - 不再评分"单曲像不像锚点"，而是评分"下一首能不能接上一首"
   - BPM 兼容：±3% pitch、半速/倍速、±5/±10 BPM 分级
   - Key 过渡：Camelot Wheel 规则
   - 能量衔接：匹配目标能量曲线

5. **动态能量曲线**
   - 5 种模式：flat / warm-up / peak-mid / rollercoaster / climax-end
   - 基于锚点能量分布的均值/标准差自适应

6. **风控代理（双层锁）**
   - `RateLimitedMCPClient` 代理模式，不污染 mcp_client.py
   - 全局锁 0.5s（所有 API 调用之间）
   - audio 专用锁 2.0s（`get_audio_url` 额外保护）

7. **歌单构建**
   - 贪心序列构建（SequentialSelector）
   - 锚点歌曲自动加入最终歌单
   - 自动创建网易云歌单并批量收藏
   - 智能命名：`[DJ Curator] {模式} | {艺人}`

### 项目结构（实际）

```
dj-set-curator/
├── src/dj_set_curator/
│   ├── __init__.py
│   ├── __main__.py              # python -m 入口
│   ├── cli.py                   # CLI 界面（argparse）
│   ├── config.py                # 配置管理
│   ├── mcp_client.py            # MCP Client 封装（纯净，无风控逻辑）
│   ├── rate_limited_client.py   # 风控代理（双层锁）← v0.3.2 新增
│   ├── anchor.py                # 锚点歌曲解析
│   ├── curator.py               # 选曲引擎核心（orchestrator）
│   ├── filters.py               # 预过滤引擎（Camelot + BPM + 多样性 + 曲风）
│   ├── genre_resolver.py        # 曲风解析器（层级树 + 缓存）
│   ├── transition.py            # Pair-wise 过渡评分 + 贪心序列构建
│   ├── sources.py               # 多源候选采集器（7 个来源）
│   ├── arranger.py              # 能量分析器（librosa 四维）
│   ├── audio_analyzer.py        # 音频分析（BPM/Key，librosa，异步下载）
│   ├── deduplicator.py          # 去重工具
│   ├── energy_heuristics.py     # 能量启发式估计
│   ├── expansion.py             # 级联扩展器
│   ├── playlist_naming.py       # 歌单命名格式
│   └── models.py                # 数据模型
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

## 两个项目的协作方式

### 方式：程序内嵌调用（实际采用）

`dj-set-curator` 作为 MCP Client，通过 stdio 启动并调用 `cloud-music-mcp-extended`：

```python
from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.rate_limited_client import RateLimitedMCPClient

async with CloudMusicMCPClient('/tmp/mcp-server-wrapper.py') as mcp:
    # 包装风控代理
    mcp_rl = RateLimitedMCPClient(mcp, global_interval=0.5, audio_interval=2.0)
    
    curator = DJSetCurator(mcp_rl)
    result = await curator.build_playlist(
        anchor_queries=["keshi - WANTCHU"],
        target_count=20
    )
```

---

## 关键设计决策

### 1. 风控不在 MCP Server 内

**原则**：`cloud-music-mcp-extended` 的 `mcp_client.py` 保持纯净，只做原始 MCP 调用 + 基础错误解析。所有风控间隔逻辑放在 `dj-set-curator` 的 `rate_limited_client.py` 代理层。

**原因**：
- MCP Server 是通用工具，不应包含业务层面的风控策略
- dj-curator 了解自身调用模式（批量分析 80+ 次 get_audio_url），能制定更精准的策略
- 双层锁（全局 + audio 专用）能针对不同 API 的配额差异做细粒度控制

### 2. BPM/Key 不由 MCP 提供

网易云 API 返回的歌曲详情**不包含** BPM 和调性信息。dj-set-curator 通过 `librosa` 独立分析音频片段获取：
- 优点：支持任何歌曲，不受平台数据覆盖限制
- 缺点：需要下载音频片段，消耗 API 配额和计算资源
- 缓解：分析结果缓存到 `analysis_cache.json`，永久复用

### 3. 音频分析缓存策略

| 层级 | 策略 | 时效 |
|------|------|------|
| 音频片段 | 系统缓存目录，保留 7 天自动清理 | 7 天 |
| 分析结果 (BPM/Key) | `analysis_cache.json`，永久缓存 | 永久 |
| 曲风标签 | 网易云百科 API，内存缓存 | 会话级 |

---

## 版本演进

| 版本 | 日期 | 核心变化 |
|------|------|----------|
| v0.1.0 | 2026-04 | 基础架构：MCP Client + 锚点分析 + 相似推荐 + 排序 |
| v0.2.0 | 2026-04 | 多源采集重构：7 个来源 + 曲风层级树 |
| v0.3.0 | 2026-05 | Pair-wise 过渡评分 + 动态能量曲线 + 贪心序列构建 |
| v0.3.1 | 2026-05 | 批量音频分析 + 歌曲结构检测 + 精能量分析 |
| **v0.3.2** | 2026-05 | **风控代理重构**：双层锁从 mcp_client 迁移到 rate_limited_client.py |

---

## 风险与注意事项

1. **版权**：只操作歌单收藏，不触碰下载/解密
2. **API 限制**：`get_audio_url` 配额严格，需风控代理保护
3. **音频分析耗时**：80 首候选 × 2.0s 间隔 = 160s，需在 600s 上限内
4. **pyncm 依赖**：原项目使用 pyncm，写操作接口需测试稳定性

---

*文档版本: 2026-05-20 (同步至 v0.3.2)*
*前置依赖: cloud-music-mcp-extended（已完成）*

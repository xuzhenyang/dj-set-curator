# 更新日志

本项目所有重要变更均记录于此。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/spec/v2.0.0.html)。

## [0.3.1] - 2026-04-28

### 新增

- **曲风层级树兼容性计算**：基于网易云官方 644 个曲风标签的三级层级树计算兼容性
  - `StyleHierarchy` 类：管理曲风节点映射（name→node、id→node）
  - 关系评分：相同节点=100 / 祖孙=85 / 兄弟=70 / 同根不同支=40 / 不同根=10
  - 复合标签自动拆分（如 `流行-欧美流行` → `流行` + `欧美流行`）
- **网易云音乐百科 API 集成**：`cloud_music_get_song_wiki` 获取歌曲曲风/情绪标签
- `cloud_music_get_style_list` 工具：获取完整曲风层级树（27 大类 / 275 子类 / 342 细分）
- 曲风缓存：`style_tree.json` + `genre_cache.json` 双缓存机制

### 变更

- **移除 Last.fm 依赖**：`pylast` 完全移除，改为纯网易云原生 API
- `genre_resolver.py` 重写：层级树优先 → fallback 硬编码矩阵 → 内置映射表兜底
- `GenreResolver.prefill()` 自动加载曲风层级树（缓存优先，API 兜底）

## [0.3.0] - 2026-04-27

### 新增

- `Song` 数据类，提供 `from_dict()` / `to_dict()` 工厂方法
- 从 `curator.py` 拆分的独立模块：
  - `deduplicator.py` — ID / 名称 / 锚点去重
  - `energy_heuristics.py` — BPM + 歌名关键词启发式能量估算
  - `expansion.py` — 级联扩展器（二级锚点搜索）
  - `playlist_naming.py` — 歌单自动命名（`[DJ Curator] {模式} | {艺人}`）
- `mcp_client.py` 新增 `get_audio_url()` 方法，支持精能量分析
- `AudioAnalyzer` 新增 `flush_cache()`，批量分析结束后统一持久化缓存

### 变更

- **架构重构**：`curator.py` 从 422 行精简至约 280 行（纯编排器）
- `AnchorSong` 改为继承 `Song`，消除字段重复
- 全模块从 `dict` 迁移至 `Song` / `ScoredSong` 对象
- 锚点解析改为 `asyncio.gather` 并行执行
- `BatchAudioAnalyzer` 缓存写入策略：每首改为批量结束后统一 flush
- `EnergyAnalyzer` 实例在锚点分析和入选后分析之间复用，避免重复下载

### 修复

- **严重**：`urllib.request.urlretrieve` 同步阻塞事件循环 —— 改为 `asyncio.to_thread` 包装（`audio_analyzer.py` + `arranger.py`）
- **严重**：MCP client `connect()` 失败时子进程泄漏 —— 增加级联异常清理
- **主要**：多样性评分硬编码为 `100.0` 完全未生效 —— 改为基于已评分列表动态计算
- **主要**：`_call_tool()` 字符串子串匹配误判（歌曲名含"失败""错误"） —— 改为限定长度 + 具体错误关键词
- **次要**：模块级 `logging.basicConfig()` 污染库用户日志 —— 移至 `cli.main()` 入口
- **次要**：`config.py` 显式传 `--server cloud-music-mcp` 被跳过 —— 判断条件改为 `is not None`
- **次要**：`transition.py` 函数内 `import re` —— 移至模块顶部

## [0.2.1] - 2026-04-25

### 新增

- `--dry-run` 预览模式，只展示候选和预测选曲，不创建歌单
- `DJSetCurator` 状态机（`stage` / `progress` / `message`）
- `config.py` 配置管理，支持 `~/.dj-set-curator/config.yaml` + 环境变量 `DJ_CURATOR_MCP_SERVER`
- 音频分析 120 秒软超时，超时自动跳过剩余歌曲
- MCP client 指数退避重试（2 次）
- `--server` CLI 参数覆盖 MCP server 命令
- 歌单自动命名方案 E：`[DJ Curator] {模式} | {艺人}`
- 独立扫码登录脚本 `scripts/login.py`

### 修复

- **严重**：`asyncio.gather` + `anyio` 并发死锁 —— 改为串行逐源采集 + `asyncio.wait_for(..., 30.0)`
- 网易云 API 拒绝 emoji —— 自动命名中移除 emoji
- `build_playlist` 内重复 `import time` 导致 `UnboundLocalError`

### 变更

- 移除 25 首分析上限，全量候选分析（batch=10 并发）

## [0.2.0] - 2026-04-24

### 新增

- 过渡感知 DJ Set 构建器（v2.0）：
  - `TransitionScorer`：BPM / Key / Energy / Artist 惩罚四维评分
  - `SequentialSelector` 贪心序列构建，支持 5 种能量曲线（`flat` / `warm-up` / `peak-mid` / `rollercoaster` / `climax-end`）
- 多源候选采集（7 个来源）：
  - `SimilarSource` 相似推荐、`ArtistTopSource` 艺人热门、`AlbumSource` 同专辑
  - `DailyRecSource` 每日推荐、`TagSearchSource` 标签搜索、`GenreSearchSource` 流派搜索
  - `CrossArtistSource` 相似艺人（核心来源，keshi → Lauv / Demxntia / The Weeknd）
- 音频分析管线：
  - `AudioAnalyzer`：librosa `beat_track` + `chroma_cqt` 分析 BPM/Key
  - `EnergyAnalyzer`：librosa RMS 精能量分析
  - 缓存系统：`analysis_cache.json` + `audio_segments/`
- `SongFilter` 预过滤（Camelot Wheel + BPM + 动态权重）
- 级联扩展：候选不足时用推荐歌曲作为二级锚点继续搜索
- 锚点歌曲自动置于歌单开头

## [0.1.0] - 2026-04-20

### 新增

- 初始版本
- `typer` + `rich` CLI
- 锚点歌曲解析（ID 或 "艺人 - 歌名" 搜索）
- 基于相似推荐的简单歌单创建
- 基础去重和多样性控制

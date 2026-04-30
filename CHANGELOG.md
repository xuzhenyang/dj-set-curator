# 更新日志

本项目所有重要变更均记录于此。

格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/spec/v2.0.0.html)。

## [Unreleased]

### 修复

- **MCP 连接重复初始化**：`cli.py` 中 `connect()` 被调用两次导致 `stdio_client` context 泄漏，引发 `RuntimeError: Attempted to exit cancel scope in a different task`
- **MCP cleanup 异常处理**：`mcp_client.py` 中 `cleanup()` 对 `__aexit__` 增加 try-except 保护，避免异常级联

### 变更

- **音频分析超时调优**：总时间上限 120s → **300s**，单首超时 20s → **30s**，提升候选池音频分析覆盖率（~52% → ~76%）
- **README 更新**：同步文档中所有 120s 相关描述为 300s

---

## [0.3.2] - 2026-04-28

### 新增

- **多源采集全面重构**：移除冗余来源（DailyRecSource、TagSearchSource），新增高质量来源
  - `StyleSongSource`：利用网易云官方曲风体系（`/api/style-tag/home/song`）获取同风格歌曲
  - `PlaylistSource`：搜索包含锚点艺人的精选歌单，提取其中曲目（人类策展人质量）
  - `CrossArtistSource` 增强：5 艺人/3 首 → **8 艺人/5 首**
  - `ArtistTopSource` 增强：12 首 → **20 首**
- **多维度 DJ 能量分析**：EnergyAnalyzer 从单一 RMS 升级为四维综合
  - RMS 响度 25% + 节奏密度（onset 峰值）35% + 低频能量占比 25% + 频谱质心（亮度）15%
- **动态能量曲线**：SequentialSelector 支持基于锚点能量分布的动态曲线（均值 ± 标准差），替代固定 0-100 范围
- **歌曲结构分析**：`SongStructureAnalyzer` 检测 intro 长度和 breakdown 位置，结果随歌单返回
- **GenreSearchSource 重写**：优先使用曲风层级树（tagId 搜索），fallback 到 BPM 映射
- **集成测试**：新增 `tests/test_integration.py`（12 个测试覆盖新来源和编排器）
- `Song` 模型新增 `structure` 字段

### 变更

- **过滤阈值放松**：`min_score` 30→20，`genre_threshold` 30→25，让 pair-wise 过渡评分承担更多筛选工作
- `AlbumSource` 修复：正确从 `al` 键提取 `album_id`（API 返回 `al` 而非 `album`）
- `MultiSourceCollector` 改为**并发采集**（`asyncio.gather`），7 个来源同时执行，大幅降低等待时间
- 最终能量/结构分析改为**并发执行**（`asyncio.gather`），每首 15 秒超时
- 锚点详情获取改为并发执行
- 删除死代码 `EnergyArcArranger`（动态曲线逻辑已合并到 `SequentialSelector`）

### 修复（Code Review）

- **严重**：`SongFilter` 从不使用加载的 genre hierarchy —— 添加 `set_hierarchy()` 注入
- **严重**：歌单总数超过 `--count`（锚点未计入）—— `effective_target = target_count - len(anchors)`
- **严重**：BPM=0 被当作完美过渡（ratio=1.0 → score=100）—— 改为 `curr_bpm <= 0` 时返回 50
- **主要**：艺人名子字符串匹配误排除（如 "E" 排除 "Eminem"）—— 改为 token 级匹配（按 `&/,/feat./ft.` 分割）
- **主要**：`hasattr(a, "energy")` 恒为 True —— 改为 `a.energy is not None`
- **主要**：`candidate_bpm=0` 时 `bpm_diff` 为 None —— 改为 `candidate_bpm is not None`
- **主要**：`GenreResolver` fallback 评分在分数为 0 时错误穿透 —— 使用 `is not None` 判断
- **主要**：`mcp_client._parse_result` 假设 `content[0].text` 存在 —— 添加 `hasattr` 守卫
- **次要**：`expansion.py` 缺少 `asyncio` 导入 + `get_similar_songs` 未加超时
- **次要**：`CrossArtistSource` 重试无间隔 —— 添加 0.5-1.0s sleep
- **次要**：`Song.to_dict()` 遗漏 `structure` 字段
- **次要**：`SequentialSelector` 目标能量曲线支持动态模式（接收 `anchor_energies`）

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

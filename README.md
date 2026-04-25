# DJ Set Curator 🎧

基于锚点歌曲的智能 DJ 选曲工具，调用网易云音乐 MCP Server 自动构建风格一致的歌单。

## 功能特性

- **锚点驱动**：输入 1-2 首锚点歌曲，自动寻找风格相似的曲目
- **智能评分**：基于 BPM 接近度、调性兼容性（Camelot Wheel）、艺术家关联度综合评分
- **实时音频分析**：通过 librosa 自动分析歌曲 BPM 和调性，无需依赖平台元数据
- **级联扩展**：候选池不足时，自动用推荐歌曲作为二级锚点继续搜索，扩充候选池
- **多样性控制**：支持调整多样性比例，避免歌单过于同质化
- **锚点入单**：锚点歌曲自动加入最终歌单，作为 Set 的核心曲目
- **一键建单**：自动创建网易云歌单并批量收藏入选曲目

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

### 单锚点

```bash
dj-curator --anchor "Aphex Twin - Windowlicker" --name "IDM Set" --count 20
```

### 多锚点

```bash
dj-curator \
  -a "Boards of Canada - Roygbiv" \
  -a "Aphex Twin - Alberto Balsalm" \
  --name "Warp Classics" \
  --count 30
```

### 使用歌曲 ID

```bash
dj-curator -a "29732235" --name "Test Set" -v
```

### 详细输出

添加 `-v` / `--verbose` 参数查看完整的选曲列表和评分：

```bash
dj-curator -a "Radiohead - Everything In Its Right Place" \
  --name "Chill Electronic" \
  --count 15 \
  --verbose
```

### 级联扩展（候选不足时自动扩充）

```bash
dj-curator -a "keshi - WANTCHU" --name "WANTCHU vibe" --count 20 --expand
```

### 关闭级联扩展

```bash
dj-curator -a "周杰伦 - 晴天" --name "纯原推荐" --count 10 --no-expand
```

### 调整多样性

```bash
dj-curator -a "Daft Punk - One More Time" \
  --name "French House" \
  --diversity 0.5 \
  --count 25
```

## CLI 参数说明

| 参数 | 简写 | 默认值 | 说明 |
|------|------|--------|------|
| `--anchor` | `-a` | *必填* | 锚点歌曲，可多次指定 |
| `--name` | `-n` | *必填* | 输出歌单名称 |
| `--count` | `-c` | `20` | 目标歌曲数量 (1-100) |
| `--bpm-tol` | | `5.0` | BPM 容差范围 |
| `--diversity` | `-d` | `0.3` | 多样性比例 (0-1) |
| `--expand` / `--no-expand` | | `True` | 候选不足时启用级联扩展 |
| `--verbose` | `-v` | `False` | 显示详细选曲列表 |
| `--server` | | `cloud-music-mcp` | MCP Server 命令 |

## 配置说明

### BPM 容差

- 默认 `±5 BPM` 为满分兼容区
- 超出容差区分数线性递减
- 通过 librosa 实时分析音频获取 BPM，不依赖平台元数据

### 调性匹配（Camelot Wheel）

| 距离 | 含义 | 评分 |
|------|------|------|
| 0 | 完全匹配 / A-B 互换 | 100 |
| 1 | 相邻兼容 | 80 |
| 2 | 较远但可混音 | 50 |
| 3+ | 不推荐 | 10 |

### 评分权重

可通过代码中的 `SongFilter` 参数自定义：

- `bpm_weight`: BPM 接近度（默认 25%）
- `key_weight`: 调性兼容性（默认 30%）
- `artist_weight`: 艺术家关联度（默认 25%）
- `diversity_weight`: 多样性（默认 20%）

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
│   ├── anchor.py            # 锚点歌曲分析
│   ├── curator.py           # 选曲引擎核心
│   └── filters.py           # 筛选排序引擎
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

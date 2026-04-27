"""曲风解析器 - 网易云音乐百科 API + 自建映射表 + 本地缓存"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from dj_set_curator.models import AnchorSong, Song

logger = logging.getLogger(__name__)

# ---------- 曲风别名标准化 ----------
# 将网易云/Last.fm/常见别名映射到标准标签
GENRE_ALIASES = {
    # 中文标签 → 标准
    "流行": "pop",
    "华语流行": "pop",
    "欧美流行": "pop",
    "韩语流行": "pop",
    "日语流行": "pop",
    "r&b": "r&b",
    "节奏布鲁斯": "r&b",
    "嘻哈说唱": "hip-hop",
    "嘻哈": "hip-hop",
    "说唱": "hip-hop",
    "流行说唱": "hip-hop",
    "trap": "hip-hop",
    "电子": "electronic",
    "电子音乐": "electronic",
    "edm": "electronic",
    "house": "electronic",
    "摇滚": "rock",
    "民谣": "folk",
    "独立音乐": "indie",
    "独立流行": "indie-pop",
    "卧室流行": "bedroom-pop",
    "灵魂乐": "soul",
    "放克": "funk",
    "爵士": "jazz",
    "蓝调": "blues",
    "古典": "classical",
    "金属": "metal",
    "朋克": "punk",
    "雷鬼": "reggae",
    "拉丁": "latin",
    "国风": "chinese-style",
    "古风": "chinese-style",
    " Mandopop": "mandopop",
    "粤语流行": "cantopop",
    # 英文标签 → 标准
    "pop": "pop",
    "indie pop": "indie-pop",
    "bedroom pop": "bedroom-pop",
    "alternative r&b": "r&b",
    "synth-pop": "pop",
    "electropop": "pop",
    "hip-hop": "hip-hop",
    "rap": "hip-hop",
    "west coast": "hip-hop",
    "drill": "hip-hop",
    "mumble rap": "hip-hop",
    "trap": "hip-hop",
    "r&b": "r&b",
    "soul": "soul",
    "funk": "funk",
    "jazz": "jazz",
    "blues": "blues",
    "electronic": "electronic",
    "dance": "electronic",
    "techno": "electronic",
    "dubstep": "electronic",
    "rock": "rock",
    "indie rock": "rock",
    "alternative rock": "rock",
    "metal": "metal",
    "punk": "punk",
    "folk": "folk",
    "country": "country",
    "reggae": "reggae",
    "latin": "latin",
    "classical": "classical",
    "lo-fi": "lo-fi",
    "lofi": "lo-fi",
    "ambient": "ambient",
    "new age": "ambient",
    "mandopop": "mandopop",
    "cantopop": "cantopop",
}


def _normalize_genre(genre: str) -> str:
    """将各种曲风标签标准化"""
    g = genre.lower().strip()
    # 处理 "嘻哈说唱-流行说唱" 这类复合标签，取主类
    if "-" in g:
        g = g.split("-")[0].strip()
    return GENRE_ALIASES.get(g, g)


# ---------- 内置常见艺人曲风映射表（兜底用） ----------
ARTIST_GENRE_MAP = {
    # 锚点相关艺人
    "keshi": ["bedroom-pop", "indie-pop", "r&b"],
    "khalid": ["r&b", "pop"],
    "demxntia": ["bedroom-pop", "indie-pop", "r&b"],
    "lauv": ["pop", "electronic"],
    "bazzi": ["pop", "r&b"],
    "jvke": ["pop"],
    "沙一汀": ["hip-hop"],
    "bazzi": ["pop", "r&b"],
    # Hip-Hop / Rap
    "kendrick lamar": ["hip-hop"],
    "drake": ["hip-hop", "r&b", "pop"],
    "tyga": ["hip-hop"],
    "post malone": ["hip-hop", "pop"],
    "lil nas x": ["hip-hop", "pop"],
    "travis scott": ["hip-hop"],
    "future": ["hip-hop"],
    "chris brown": ["r&b", "hip-hop"],
    "kiana lede": ["r&b", "pop"],
    "blxst": ["hip-hop", "r&b"],
    # Pop
    "justin bieber": ["pop", "r&b"],
    "ariana grande": ["pop", "r&b"],
    "doja cat": ["pop", "hip-hop", "r&b"],
    "the weeknd": ["r&b", "pop"],
    "taylor swift": ["pop", "country"],
    "billie eilish": ["pop", "indie-pop"],
    "bruno mars": ["pop", "funk", "r&b"],
    # R&B / Soul
    "sza": ["r&b", "soul"],
    "frank ocean": ["r&b"],
    "bryson tiller": ["r&b", "hip-hop"],
    "daniel caesar": ["r&b", "soul"],
    "giveon": ["r&b", "soul"],
    "usher": ["r&b", "pop"],
    "beyoncé": ["r&b", "pop"],
    "beyonce": ["r&b", "pop"],
    # Electronic
    "daft punk": ["electronic"],
    "calvin harris": ["electronic"],
    "martin garrix": ["electronic"],
    "the chainsmokers": ["electronic", "pop"],
    "avicii": ["electronic"],
    "zedd": ["electronic", "pop"],
    "dua lipa": ["pop", "electronic"],
    # Rock / Indie
    "radiohead": ["rock"],
    "coldplay": ["rock", "pop"],
    "arctic monkeys": ["rock", "indie"],
    # 中文
    "周杰伦": ["mandopop", "r&b", "pop"],
    "林俊杰": ["mandopop", "pop"],
    "薛之谦": ["mandopop", "pop"],
    "陈奕迅": ["cantopop", "pop"],
    "邓紫棋": ["mandopop", "pop"],
    "李荣浩": ["mandopop", "pop"],
    "毛不易": ["mandopop", "folk"],
    "陶喆": ["mandopop", "r&b"],
    "方大同": ["mandopop", "r&b", "soul"],
    "王力宏": ["mandopop", "r&b", "pop"],
}

# ---------- 歌名关键词推断 ----------
SONG_NAME_GENRE_KEYWORDS = {
    "hip-hop": ["freestyle", "diss", "cypher", "trap", "drill", "mumble"],
    "rap": ["freestyle", "diss", "feat", "remix"],
    "rock": ["rock", "metal", "punk"],
    "electronic": ["remix", "club", "bass", "drop", "edm", "house", "techno"],
    "acoustic": ["acoustic", "piano", "unplugged"],
    "lo-fi": ["lofi", "lo-fi", "chill"],
}

# ---------- 曲风兼容性矩阵（基于标准化标签） ----------
GENRE_COMPATIBILITY = {
    # === 柔和家族（bedroom-pop / indie-pop / r&b / soul） ===
    ("r&b", "r&b"): 100,
    ("r&b", "pop"): 85,
    ("r&b", "indie-pop"): 75,
    ("r&b", "bedroom-pop"): 80,
    ("r&b", "soul"): 85,
    ("pop", "pop"): 100,
    ("pop", "indie-pop"): 80,
    ("pop", "electronic"): 60,
    ("indie-pop", "bedroom-pop"): 85,
    ("bedroom-pop", "bedroom-pop"): 100,
    ("soul", "soul"): 100,
    ("soul", "pop"): 75,
    ("soul", "r&b"): 90,
    ("funk", "r&b"): 80,
    ("funk", "soul"): 85,
    ("funk", "pop"): 70,
    # 柔和 vs Hip-Hop：较低兼容
    ("r&b", "hip-hop"): 30,
    ("bedroom-pop", "hip-hop"): 5,
    ("indie-pop", "hip-hop"): 5,
    ("pop", "hip-hop"): 30,
    ("soul", "hip-hop"): 25,
    # === Hip-Hop 家族 ===
    ("hip-hop", "hip-hop"): 100,
    ("hip-hop", "electronic"): 50,
    ("hip-hop", "pop"): 35,
    ("hip-hop", "r&b"): 30,
    # === Electronic 家族 ===
    ("electronic", "electronic"): 100,
    ("electronic", "pop"): 60,
    ("electronic", "hip-hop"): 50,
    ("electronic", "r&b"): 40,
    # === Rock 家族 ===
    ("rock", "rock"): 100,
    ("rock", "pop"): 50,
    ("rock", "indie"): 70,
    ("indie", "indie"): 100,
    ("indie", "indie-pop"): 80,
    ("indie", "pop"): 60,
    # === Folk / Country ===
    ("folk", "folk"): 100,
    ("folk", "pop"): 50,
    ("country", "country"): 100,
    ("country", "pop"): 55,
    ("country", "folk"): 70,
    # === 中文流行 ===
    ("mandopop", "mandopop"): 100,
    ("cantopop", "cantopop"): 100,
    ("mandopop", "pop"): 70,
    ("cantopop", "pop"): 65,
    ("mandopop", "cantopop"): 60,
    ("chinese-style", "chinese-style"): 100,
    ("chinese-style", "mandopop"): 60,
    # === Jazz / Blues ===
    ("jazz", "jazz"): 100,
    ("jazz", "r&b"): 60,
    ("jazz", "soul"): 65,
    ("blues", "blues"): 100,
    ("blues", "rock"): 50,
    ("blues", "r&b"): 55,
    # === Ambient / Lo-fi ===
    ("ambient", "ambient"): 100,
    ("ambient", "lo-fi"): 80,
    ("lo-fi", "lo-fi"): 100,
    ("lo-fi", "bedroom-pop"): 75,
    ("lo-fi", "indie-pop"): 70,
    ("lo-fi", "r&b"): 60,
}


# ---------- 缓存 ----------
def _get_cache_dir() -> Path:
    # macOS 标准缓存目录
    cache_dir = Path.home() / "Library" / "Caches" / "dj-set-curator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_cache_path() -> Path:
    return _get_cache_dir() / "genre_cache.json"


def _load_cache() -> dict:
    path = _get_cache_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_cache(cache: dict):
    path = _get_cache_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存曲风缓存失败: %s", e)


# ---------- 工具函数 ----------
def _normalize_artist(artist: str) -> str:
    return artist.lower().strip()


def _get_genres_from_map(artist: str) -> Optional[list[str]]:
    """从内置映射表获取曲风"""
    normalized = _normalize_artist(artist)
    return ARTIST_GENRE_MAP.get(normalized)


def _infer_genre_from_song_name(name: str) -> Optional[list[str]]:
    """从歌名关键词推断曲风"""
    lower_name = name.lower()
    genres = []
    for genre, keywords in SONG_NAME_GENRE_KEYWORDS.items():
        for kw in keywords:
            if kw in lower_name:
                genres.append(genre)
                break
    return genres if genres else None


def _extract_genres_from_wiki(wiki_data: dict) -> list[str]:
    """从网易云音乐百科响应中提取曲风标签"""
    genres = []
    tags = []
    
    # wiki_data 格式: {"song_id": "...", "genres": [...], "tags": [...]}
    if isinstance(wiki_data, dict):
        genres = wiki_data.get("genres", [])
        tags = wiki_data.get("tags", [])
    
    # 标准化
    result = []
    for g in genres:
        normalized = _normalize_genre(g)
        if normalized and normalized not in result:
            result.append(normalized)
    
    # 如果 genres 为空但 tags 中有可识别的曲风标签，也纳入
    tag_genre_hints = {
        "甜蜜": "pop",
        "浪漫": "r&b",
        "治愈": "pop",
        "放松": "ambient",
        "伤感": "pop",
        "怀旧": "pop",
        "动感": "electronic",
        "派对": "electronic",
        "舞曲": "electronic",
    }
    for t in tags:
        hint = tag_genre_hints.get(t)
        if hint and hint not in result:
            result.append(hint)
    
    return result


# ---------- 核心类 ----------
class GenreResolver:
    """曲风解析器 - 网易云百科 API + 多层 fallback"""

    def __init__(self, mcp_client=None):
        self._mcp = mcp_client
        self._cache = _load_cache()
        self._dirty = False

    def _cache_key(self, song: Song) -> str:
        """生成缓存键"""
        # 优先使用 song_id，因为同一首歌不同版本可能有不同标签
        if song.id:
            return f"id:{song.id}"
        return f"name:{song.artist}::{song.name}"

    def resolve(self, song: Song) -> list[str]:
        """
        解析歌曲曲风（同步方法，不发起网络请求）
        优先级：
        1. 缓存
        2. 内置映射表
        3. 歌名关键词推断
        """
        key = self._cache_key(song)
        if key in self._cache:
            return self._cache[key]

        # 2. 内置映射表
        genres = _get_genres_from_map(song.artist)
        if genres:
            self._cache[key] = genres
            self._dirty = True
            return genres

        # 3. 歌名关键词推断
        genres = _infer_genre_from_song_name(song.name)
        if genres:
            self._cache[key] = genres
            self._dirty = True
            return genres

        return []

    async def prefill(self, songs: list[Song]):
        """
        批量异步查询歌曲曲风（通过网易云百科 API），结果写入缓存和 song.genre_tags
        """
        if not self._mcp:
            logger.debug("未提供 MCP client，跳过批量曲风预取")
            # 即使没 API，也用 fallback 填充
            for song in songs:
                if not song.genre_tags:
                    song.genre_tags = self.resolve(song)
            return

        # 筛选需要查询的歌曲
        to_fetch = []
        for song in songs:
            key = self._cache_key(song)
            if key not in self._cache and not song.genre_tags:
                to_fetch.append(song)

        if not to_fetch:
            logger.debug("所有歌曲曲风已在缓存中")
            for song in songs:
                if not song.genre_tags:
                    song.genre_tags = self.resolve(song)
            return

        logger.info("正在批量查询 %d 首歌曲的曲风信息...", len(to_fetch))

        # 并发查询（限制并发数避免触发限流）
        semaphore = asyncio.Semaphore(8)

        async def _fetch_one(song: Song):
            async with semaphore:
                try:
                    wiki = await self._mcp.get_song_wiki(song.id)
                    genres = _extract_genres_from_wiki(wiki)
                    if genres:
                        key = self._cache_key(song)
                        self._cache[key] = genres
                        self._dirty = True
                        song.genre_tags = genres
                        return
                except Exception as e:
                    logger.debug("查询歌曲百科失败 %s - %s: %s", song.id, song.name, e)
                # API 失败则 fallback
                song.genre_tags = self.resolve(song)

        await asyncio.gather(*[_fetch_one(s) for s in to_fetch])
        
        # 同步填充已有缓存的
        for song in songs:
            if not song.genre_tags:
                song.genre_tags = self.resolve(song)
        
        self.flush_cache()
        logger.info("曲风预取完成")

    def flush_cache(self):
        """手动刷新缓存到磁盘"""
        if self._dirty:
            _save_cache(self._cache)
            self._dirty = False

    def get_anchor_genres(self, anchors: list[AnchorSong]) -> list[str]:
        """获取所有锚点艺人的曲风集合（去重）"""
        all_genres = set()
        for anchor in anchors:
            if anchor.genre_tags:
                all_genres.update(anchor.genre_tags)
            else:
                genres = self.resolve(
                    Song(id=anchor.id, name=anchor.name, artist=anchor.artist)
                )
                anchor.genre_tags = genres
                all_genres.update(genres)
        return list(all_genres)


# ---------- 兼容性评分 ----------
def genre_compatibility_score(candidate_genres: list[str], anchor_genres: list[str]) -> float:
    """
    计算候选歌曲与锚点的曲风兼容性评分 (0-100)
    - 完全匹配 → 100
    - 兼容（通过矩阵）→ 按矩阵值
    - 不兼容 → 10
    - 无数据 → 50（中性分）
    """
    if not candidate_genres or not anchor_genres:
        return 50.0

    # 标准化
    cg_norm = [_normalize_genre(g) for g in candidate_genres]
    ag_norm = [_normalize_genre(g) for g in anchor_genres]

    best_score = 0.0
    for cg in cg_norm:
        for ag in ag_norm:
            if cg == ag:
                return 100.0
            key = (cg, ag)
            reverse_key = (ag, cg)
            score = GENRE_COMPATIBILITY.get(key) or GENRE_COMPATIBILITY.get(reverse_key)
            if score and score > best_score:
                best_score = score

    return best_score if best_score > 0 else 10.0


def is_genre_compatible(candidate_genres: list[str], anchor_genres: list[str], threshold: float = 30.0) -> bool:
    """判断曲风是否兼容（用于硬过滤）"""
    return genre_compatibility_score(candidate_genres, anchor_genres) >= threshold

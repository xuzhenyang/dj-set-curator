"""曲风解析器 - Last.fm API + 自建映射表 + 本地缓存"""

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dj_set_curator.models import AnchorSong, Song

logger = logging.getLogger(__name__)

# 内置常见艺人曲风映射表（兜底用）
# 来源：Last.fm 社区标签 + 人工校对
ARTIST_GENRE_MAP = {
    # 锚点相关艺人
    "keshi": ["indie pop", "bedroom pop", "r&b"],
    "khalid": ["r&b", "pop", "alternative r&b"],
    "demxntia": ["indie pop", "bedroom pop", "r&b"],
    "lauv": ["pop", "electropop"],
    "bazzi": ["pop", "r&b"],
    "jvke": ["pop"],
    # Hip-Hop / Rap（与锚点风格差异大）
    "kendrick lamar": ["hip-hop", "rap", "west coast"],
    "drake": ["hip-hop", "rap", "r&b", "pop"],
    "tyga": ["hip-hop", "rap"],
    "post malone": ["hip-hop", "rap", "pop"],
    "lil nas x": ["hip-hop", "rap", "pop"],
    "travis scott": ["hip-hop", "rap", "trap"],
    "future": ["hip-hop", "rap", "trap"],
    "chris brown": ["r&b", "hip-hop", "pop"],
    "kiana lede": ["r&b", "pop"],
    # Pop / 主流
    "justin bieber": ["pop", "r&b"],
    "ariana grande": ["pop", "r&b"],
    "doja cat": ["pop", "hip-hop", "r&b"],
    "the weeknd": ["r&b", "pop", "synth-pop"],
    "taylor swift": ["pop", "country"],
    "billie eilish": ["pop", "indie pop"],
    # R&B / Soul
    "sza": ["r&b", "soul"],
    "frank ocean": ["r&b", "alternative r&b"],
    "bryson tiller": ["r&b", "hip-hop"],
    # Electronic / EDM
    "daft punk": ["electronic", "house"],
    "calvin harris": ["electronic", "edm", "house"],
    "martin garrix": ["electronic", "edm", "house"],
    # Rock / Indie
    "radiohead": ["rock", "alternative rock"],
    "coldplay": ["rock", "pop"],
    "arctic monkeys": ["indie rock", "rock"],
    # 中文
    "周杰伦": ["mandopop", "r&b", "pop"],
    "林俊杰": ["mandopop", "pop"],
    "薛之谦": ["mandopop", "pop"],
    "陈奕迅": ["cantopop", "pop"],
}

# 歌名关键词 → 曲风推断
SONG_NAME_GENRE_KEYWORDS = {
    "hip-hop": ["freestyle", "diss", "cypher", "trap", "drill", "mumble"],
    "rap": ["freestyle", "diss", "feat", "remix"],
    "rock": ["rock", "metal", "punk"],
    "electronic": ["remix", "club", "bass", "drop", "edm", "house", "techno"],
    "acoustic": ["acoustic", "piano", "unplugged"],
    "lofi": ["lofi", "lo-fi", "chill"],
}

# 曲风兼容性矩阵
# 值越高表示兼容性越好（0-100）
# 核心原则：同一"氛围家族"高分，不同家族低分
GENRE_COMPATIBILITY = {
    # === 柔和家族（bedroom pop / indie pop / r&b / soul） ===
    ("r&b", "r&b"): 100,
    ("r&b", "pop"): 85,
    ("r&b", "indie pop"): 75,
    ("r&b", "bedroom pop"): 80,
    ("r&b", "alternative r&b"): 90,
    ("r&b", "soul"): 85,
    ("pop", "pop"): 100,
    ("pop", "indie pop"): 80,
    ("pop", "electropop"): 85,
    ("indie pop", "bedroom pop"): 85,
    ("indie pop", "alternative r&b"): 70,
    ("bedroom pop", "alternative r&b"): 75,
    ("soul", "soul"): 100,
    # 柔和 vs Hip-Hop/Rap：极低兼容（氛围差异巨大）
    ("r&b", "hip-hop"): 20,
    ("r&b", "rap"): 15,
    ("bedroom pop", "hip-hop"): 5,
    ("bedroom pop", "rap"): 5,
    ("indie pop", "hip-hop"): 5,
    ("indie pop", "rap"): 5,
    ("alternative r&b", "hip-hop"): 15,
    ("alternative r&b", "rap"): 10,
    ("pop", "hip-hop"): 25,
    ("pop", "rap"): 20,
    # === Hip-Hop 家族 ===
    ("hip-hop", "hip-hop"): 100,
    ("hip-hop", "rap"): 95,
    ("hip-hop", "trap"): 85,
    ("rap", "rap"): 100,
    ("rap", "trap"): 80,
    # === Electronic 家族 ===
    ("electronic", "electronic"): 100,
    ("electronic", "edm"): 90,
    ("electronic", "house"): 85,
    ("electronic", "pop"): 60,
    # === Rock 家族 ===
    ("rock", "rock"): 100,
    ("rock", "indie rock"): 80,
    ("rock", "pop"): 50,
    # === 中文流行 ===
    ("mandopop", "mandopop"): 100,
    ("cantopop", "cantopop"): 100,
    ("mandopop", "pop"): 70,
}


def _get_cache_path() -> Path:
    cache_dir = Path.home() / ".dj-set-curator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "genre_cache.json"


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


class GenreResolver:
    """曲风解析器 - 多层 fallback"""

    def __init__(self, lastfm_api_key: Optional[str] = None):
        self.lastfm_api_key = lastfm_api_key or os.environ.get("LASTFM_API_KEY")
        self._cache = _load_cache()
        self._lastfm = None
        if self.lastfm_api_key:
            try:
                import pylast
                self._lastfm = pylast.LastFMNetwork(api_key=self.lastfm_api_key)
            except Exception as e:
                logger.warning("Last.fm 初始化失败: %s", e)

    def resolve(self, song: Song) -> list[str]:
        """
        解析歌曲曲风，优先级：
        1. 缓存
        2. 内置映射表
        3. Last.fm API
        4. 歌名关键词推断
        """
        cache_key = f"{song.artist}::{song.name}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # 2. 内置映射表
        genres = _get_genres_from_map(song.artist)
        if genres:
            self._cache[cache_key] = genres
            _save_cache(self._cache)
            return genres

        # 3. Last.fm API
        if self._lastfm:
            try:
                genres = self._query_lastfm(song.artist)
                if genres:
                    self._cache[cache_key] = genres
                    _save_cache(self._cache)
                    return genres
            except Exception as e:
                logger.debug("Last.fm 查询失败: %s - %s", song.artist, e)

        # 4. 歌名关键词推断
        genres = _infer_genre_from_song_name(song.name)
        if genres:
            self._cache[cache_key] = genres
            _save_cache(self._cache)
            return genres

        return []

    def _query_lastfm(self, artist: str) -> Optional[list[str]]:
        """查询 Last.fm 获取艺人 Top Tags"""
        if not self._lastfm:
            return None
        top_tags = self._lastfm.get_artist(artist).get_top_tags(limit=5)
        genres = []
        for tag_item in top_tags:
            tag_name = tag_item.item.get_name().lower()
            # 过滤非曲风标签（如 "seen live", "american" 等）
            noise_tags = {"seen live", "american", "british", "canadian", "female vocalists",
                          "male vocalists", "00s", "10s", "20s", "90s", "80s"}
            if tag_name not in noise_tags:
                genres.append(tag_name)
        return genres[:3] if genres else None

    def get_anchor_genres(self, anchors: list[AnchorSong]) -> list[str]:
        """获取所有锚点艺人的曲风集合（去重）"""
        all_genres = set()
        for anchor in anchors:
            genres = self.resolve(
                Song(id=anchor.id, name=anchor.name, artist=anchor.artist)
            )
            all_genres.update(genres)
        return list(all_genres)


def genre_compatibility_score(candidate_genres: list[str], anchor_genres: list[str]) -> float:
    """
    计算候选歌曲与锚点的曲风兼容性评分 (0-100)

    规则：
    - 完全匹配（候选曲风与锚点曲风有交集）→ 100
    - 兼容（通过兼容性矩阵）→ 60-90
    - 不兼容（如 hip-hop vs bedroom pop）→ 0-30
    - 无数据 → 50（中性分，不加分不扣分）
    """
    if not candidate_genres or not anchor_genres:
        return 50.0

    best_score = 0.0
    for cg in candidate_genres:
        for ag in anchor_genres:
            # 直接匹配
            if cg == ag:
                return 100.0
            # 矩阵匹配
            key = (cg, ag)
            reverse_key = (ag, cg)
            score = GENRE_COMPATIBILITY.get(key) or GENRE_COMPATIBILITY.get(reverse_key)
            if score and score > best_score:
                best_score = score

    # 如果没有找到任何匹配，给一个低分
    return best_score if best_score > 0 else 20.0


def is_genre_compatible(candidate_genres: list[str], anchor_genres: list[str], threshold: float = 40.0) -> bool:
    """判断曲风是否兼容（用于硬过滤）"""
    return genre_compatibility_score(candidate_genres, anchor_genres) >= threshold

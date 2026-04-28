"""曲风解析器 - 网易云音乐百科 API + 曲风层级树 + 本地缓存"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Optional

from dj_set_curator.models import AnchorSong, Song

logger = logging.getLogger(__name__)

# ---------- 内置常见艺人曲风映射表（兜底 + 校准用） ----------
ARTIST_GENRE_MAP = {
    # 锚点相关艺人
    "keshi": ["bedroom-pop", "indie-pop", "r&b"],
    "khalid": ["r&b", "pop"],
    "demxntia": ["bedroom-pop", "indie-pop", "r&b"],
    "lauv": ["pop", "electronic"],
    "bazzi": ["pop", "r&b"],
    "jvke": ["pop"],
    "沙一汀": ["hip-hop"],
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


# ---------- 缓存 ----------
def _get_cache_dir() -> Path:
    cache_dir = Path.home() / "Library" / "Caches" / "dj-set-curator"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _get_cache_path() -> Path:
    return _get_cache_dir() / "genre_cache.json"


def _get_style_tree_path() -> Path:
    return _get_cache_dir() / "style_tree.json"


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


def _load_style_tree() -> Optional[list[dict]]:
    path = _get_style_tree_path()
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return None


def _save_style_tree(tree: list[dict]):
    path = _get_style_tree_path()
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tree, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning("保存曲风层级树失败: %s", e)


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


def _split_composite_tag(tag: str) -> list[str]:
    """拆分复合标签，如 '流行-欧美流行' → ['流行', '欧美流行']"""
    if "-" in tag:
        parts = [p.strip() for p in tag.split("-")]
        return parts
    return [tag]


# ---------- 曲风层级树 ----------
class StyleNode:
    """曲风节点"""

    def __init__(self, tag_id: int, name: str, en_name: str, level: int, parent: Optional["StyleNode"] = None):
        self.tag_id = tag_id
        self.name = name
        self.en_name = en_name
        self.level = level
        self.parent = parent
        self.children: list[StyleNode] = []
        self.root: Optional[StyleNode] = None

    def __repr__(self):
        return f"StyleNode({self.tag_id}, {self.name})"


class StyleHierarchy:
    """曲风层级树管理器"""

    def __init__(self, tree_data: Optional[list[dict]] = None):
        self._name_map: dict[str, StyleNode] = {}
        self._id_map: dict[int, StyleNode] = {}
        if tree_data:
            self._build_tree(tree_data)

    def _build_tree(self, tree_data: list[dict]):
        """从 API 数据构建层级树"""
        for root_data in tree_data:
            self._parse_node(root_data, parent=None)

        # 第二次遍历：设置 root 引用
        for node in self._id_map.values():
            if node.level == 1:
                node.root = node
            elif node.parent:
                node.root = node.parent.root

    def _parse_node(self, data: dict, parent: Optional[StyleNode]):
        """递归解析节点"""
        tag_id = data.get("tagId")
        name = data.get("tagName", "")
        en_name = data.get("enName", "")
        level = data.get("level", 1)

        if not tag_id or not name:
            return

        node = StyleNode(tag_id=tag_id, name=name, en_name=en_name, level=level, parent=parent)
        self._id_map[tag_id] = node

        # 用多种键名建立映射，提高查找成功率
        keys = [name, en_name, name.lower(), en_name.lower()]
        for key in keys:
            if key and key not in self._name_map:
                self._name_map[key] = node

        if parent:
            parent.children.append(node)

        for child_data in data.get("childrenTags") or []:
            self._parse_node(child_data, parent=node)

    def find(self, tag_name: str) -> Optional[StyleNode]:
        """按名称查找节点（支持中英文、大小写）"""
        tag_name = tag_name.strip()
        # 直接匹配
        if tag_name in self._name_map:
            return self._name_map[tag_name]
        # 小写匹配
        lower = tag_name.lower()
        if lower in self._name_map:
            return self._name_map[lower]
        return None

    def get_ancestors(self, node: StyleNode) -> list[StyleNode]:
        """获取所有祖先节点（从父到根）"""
        ancestors = []
        current = node.parent
        while current:
            ancestors.append(current)
            current = current.parent
        return ancestors

    def relationship_score(self, tag_a: str, tag_b: str) -> Optional[float]:
        """
        计算两个曲风标签的兼容性分数
        返回 None 表示无法通过层级树计算（需要 fallback）
        """
        node_a = self.find(tag_a)
        node_b = self.find(tag_b)

        if not node_a or not node_b:
            return None

        # 完全相同
        if node_a.tag_id == node_b.tag_id:
            return 100.0

        # 检查祖孙关系（a 是 b 的祖先或后代）
        ancestors_a = {n.tag_id for n in self.get_ancestors(node_a)}
        ancestors_b = {n.tag_id for n in self.get_ancestors(node_b)}

        if node_a.tag_id in ancestors_b or node_b.tag_id in ancestors_a:
            return 85.0

        # 同一父节点（兄弟）
        if node_a.parent and node_b.parent and node_a.parent.tag_id == node_b.parent.tag_id:
            return 70.0

        # 同一根节点（同一 level 1 大类下的不同分支）
        if node_a.root and node_b.root and node_a.root.tag_id == node_b.root.tag_id:
            return 40.0

        # 完全不同根
        return 10.0

    def is_loaded(self) -> bool:
        return len(self._name_map) > 0


# ---------- 兼容性评分（层级树优先） ----------
# 硬编码矩阵作为 fallback
GENRE_COMPATIBILITY_FALLBACK = {
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
    ("r&b", "hip-hop"): 30,
    ("bedroom-pop", "hip-hop"): 5,
    ("indie-pop", "hip-hop"): 5,
    ("pop", "hip-hop"): 30,
    ("soul", "hip-hop"): 25,
    ("hip-hop", "hip-hop"): 100,
    ("hip-hop", "electronic"): 50,
    ("hip-hop", "pop"): 35,
    ("hip-hop", "r&b"): 30,
    ("electronic", "electronic"): 100,
    ("electronic", "pop"): 60,
    ("electronic", "hip-hop"): 50,
    ("electronic", "r&b"): 40,
    ("rock", "rock"): 100,
    ("rock", "pop"): 50,
    ("rock", "indie"): 70,
    ("indie", "indie"): 100,
    ("indie", "indie-pop"): 80,
    ("indie", "pop"): 60,
    ("folk", "folk"): 100,
    ("folk", "pop"): 50,
    ("country", "country"): 100,
    ("country", "pop"): 55,
    ("country", "folk"): 70,
    ("mandopop", "mandopop"): 100,
    ("cantopop", "cantopop"): 100,
    ("mandopop", "pop"): 70,
    ("cantopop", "pop"): 65,
    ("mandopop", "cantopop"): 60,
    ("chinese-style", "chinese-style"): 100,
    ("chinese-style", "mandopop"): 60,
    ("jazz", "jazz"): 100,
    ("jazz", "r&b"): 60,
    ("jazz", "soul"): 65,
    ("blues", "blues"): 100,
    ("blues", "rock"): 50,
    ("blues", "r&b"): 55,
    ("ambient", "ambient"): 100,
    ("ambient", "lo-fi"): 80,
    ("lo-fi", "lo-fi"): 100,
    ("lo-fi", "bedroom-pop"): 75,
    ("lo-fi", "indie-pop"): 70,
    ("lo-fi", "r&b"): 60,
}


def _normalize_genre(genre: str) -> str:
    """将各种曲风标签标准化到 fallback 矩阵的键"""
    g = genre.lower().strip()
    aliases = {
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
        "mandopop": "mandopop",
        "粤语流行": "cantopop",
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
        "cantopop": "cantopop",
    }
    return aliases.get(g, g)


def _fallback_score(tag_a: str, tag_b: str) -> Optional[float]:
    """硬编码矩阵评分 fallback"""
    a = _normalize_genre(tag_a)
    b = _normalize_genre(tag_b)
    if a == b:
        return 100.0
    key = (a, b)
    reverse = (b, a)
    val = GENRE_COMPATIBILITY_FALLBACK.get(key)
    if val is not None:
        return val
    return GENRE_COMPATIBILITY_FALLBACK.get(reverse)


def genre_compatibility_score(
    candidate_genres: list[str],
    anchor_genres: list[str],
    hierarchy: Optional[StyleHierarchy] = None,
) -> float:
    """
    计算候选歌曲与锚点的曲风兼容性评分 (0-100)
    优先使用层级树，fallback 到硬编码矩阵
    """
    if not candidate_genres or not anchor_genres:
        return 50.0

    # 拆分复合标签（如 "流行-欧美流行" → "流行", "欧美流行"）
    cg_tags = []
    for g in candidate_genres:
        cg_tags.extend(_split_composite_tag(g))

    ag_tags = []
    for g in anchor_genres:
        ag_tags.extend(_split_composite_tag(g))

    best_score = 0.0
    tree_hits = 0
    fallback_hits = 0

    for cg in cg_tags:
        for ag in ag_tags:
            score = None

            # 1. 尝试层级树
            if hierarchy and hierarchy.is_loaded():
                score = hierarchy.relationship_score(cg, ag)
                if score is not None:
                    tree_hits += 1

            # 2. Fallback 到硬编码矩阵
            if score is None:
                score = _fallback_score(cg, ag)
                if score is not None:
                    fallback_hits += 1

            # 3. 如果都没命中，给中性偏低的分数
            if score is None:
                score = 15.0

            if score > best_score:
                best_score = score

    # 如果完全没命中任何已知标签，给中性分
    if best_score == 0.0:
        return 50.0

    return best_score


def is_genre_compatible(
    candidate_genres: list[str],
    anchor_genres: list[str],
    hierarchy: Optional[StyleHierarchy] = None,
    threshold: float = 30.0,
) -> bool:
    """判断曲风是否兼容（用于硬过滤）"""
    return genre_compatibility_score(candidate_genres, anchor_genres, hierarchy) >= threshold


# ---------- 从 Wiki 提取曲风 ----------
def _extract_genres_from_wiki(wiki_data: dict) -> list[str]:
    """从网易云音乐百科响应中提取曲风标签（保留原始标签，不标准化）"""
    genres = []
    tags = []

    if isinstance(wiki_data, dict):
        genres = wiki_data.get("genres", [])
        tags = wiki_data.get("tags", [])

    # 保留原始标签（复合标签如 "流行-欧美流行" 会被后续拆分）
    result = []
    for g in genres:
        g = g.strip()
        if g and g not in result:
            result.append(g)

    # tags 中可识别的曲风暗示
    tag_genre_hints = {
        "甜蜜": "流行",
        "浪漫": "R&B",
        "治愈": "流行",
        "放松": "氛围电子",
        "伤感": "流行",
        "怀旧": "流行",
        "动感": "电子",
        "派对": "电子",
        "舞曲": "电子",
    }
    for t in tags:
        hint = tag_genre_hints.get(t)
        if hint and hint not in result:
            result.append(hint)

    return result


# ---------- 核心类 ----------
class GenreResolver:
    """曲风解析器 - 网易云百科 API + 曲风层级树 + 多层 fallback"""

    def __init__(self, mcp_client=None):
        self._mcp = mcp_client
        self._cache = _load_cache()
        self._dirty = False
        self._hierarchy = StyleHierarchy()

    @property
    def hierarchy(self) -> StyleHierarchy:
        return self._hierarchy

    async def load_style_hierarchy(self):
        """异步加载曲风层级树（优先缓存，其次 API）"""
        # 1. 尝试从缓存加载
        cached = _load_style_tree()
        if cached:
            self._hierarchy = StyleHierarchy(cached)
            logger.info("曲风层级树已从缓存加载，共 %d 个节点", len(self._hierarchy._name_map))
            return

        if not self._mcp:
            logger.warning("未提供 MCP client，无法加载曲风层级树")
            return

        # 2. 从 API 获取
        try:
            tree_data = await self._mcp.get_style_list()
            if tree_data:
                self._hierarchy = StyleHierarchy(tree_data)
                _save_style_tree(tree_data)
                logger.info("曲风层级树已从 API 加载，共 %d 个节点", len(self._hierarchy._name_map))
            else:
                logger.warning("API 返回空曲风层级树")
        except Exception as e:
            logger.warning("加载曲风层级树失败: %s", e)

    def _cache_key(self, song: Song) -> str:
        """生成缓存键"""
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
        # 确保层级树已加载
        if not self._hierarchy.is_loaded():
            await self.load_style_hierarchy()

        if not self._mcp:
            logger.debug("未提供 MCP client，跳过批量曲风预取")
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
                song.genre_tags = self.resolve(song)

        await asyncio.gather(*[_fetch_one(s) for s in to_fetch])

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

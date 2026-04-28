"""多源候选采集器 - 从网易云多个渠道收集候选歌曲

DJ 视角的来源设计：
- 同专辑、同艺人、相似艺人 = 核心来源（风格一致性高）
- 曲风标签、相似推荐、歌单 = 扩展来源（引入新鲜血液）
- 避免冗余：不重复获取同一艺人的歌曲
"""

import asyncio
import logging
import re
from typing import Optional

from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.models import Song

logger = logging.getLogger(__name__)


class CandidateSource:
    """候选来源基类"""

    def __init__(self, mcp_client: CloudMusicMCPClient):
        self.mcp = mcp_client

    async def collect(self, anchor: dict) -> list[Song]:
        """返回候选歌曲列表 [Song]"""
        raise NotImplementedError

    @staticmethod
    def _has_chinese(text: str) -> bool:
        return bool(re.search(r'[\u4e00-\u9fff]', text))

    @staticmethod
    def _language_match(anchor_name: str, candidate_name: str) -> bool:
        anchor_has_cn = CandidateSource._has_chinese(anchor_name)
        cand_has_cn = CandidateSource._has_chinese(candidate_name)
        if not anchor_has_cn and cand_has_cn:
            return False
        return True

    @staticmethod
    def _is_low_quality(name: str) -> bool:
        """检测低质内容"""
        lowq = ["dj版", "车载版", "抖音", "cover", "伴奏", "铃声", "慢速版", "加速版"]
        return any(kw in name.lower() for kw in lowq)

    @staticmethod
    def _songs_to_objects(raw_songs: list[dict]) -> list[Song]:
        """将原始歌曲数据转为 Song 对象"""
        result = []
        for s in raw_songs:
            try:
                result.append(
                    Song(
                        id=str(s.get("id", "")),
                        name=s.get("name", "未知"),
                        artist=s.get("artist", "未知"),
                    )
                )
            except Exception:
                pass
        return result


class SimilarSource(CandidateSource):
    """网易云相似推荐源"""

    async def collect(self, anchor: dict) -> list[Song]:
        song_id = str(anchor.get("id", ""))
        if not song_id:
            return []
        logger.info("相似推荐源: 获取 '%s' 的相似推荐...", anchor.get("name", song_id))
        similar = await self.mcp.get_similar_songs(song_id, limit=30)
        logger.info("相似推荐源: '%s' 获得 %d 首", anchor.get("name", song_id), len(similar))
        return self._songs_to_objects(similar)


class ArtistTopSource(CandidateSource):
    """艺术家热门歌曲源"""

    async def collect(self, anchor: dict) -> list[Song]:
        artist_id = anchor.get("artist_id")
        artist_name = anchor.get("artist", "")
        if not artist_id:
            detail = await self.mcp.get_song_detail(str(anchor.get("id", "")))
            artist_id = detail.get("artist_id") if isinstance(detail, dict) else None
            if not artist_id:
                logger.warning("艺术家源: 无法获取 '%s' 的 artist_id", artist_name)
                return []

        logger.info("艺术家源: 获取 '%s'(ID:%s) 的热门歌曲...", artist_name, artist_id)
        tracks = await self.mcp.get_artist_tracks(str(artist_id), limit=20)
        logger.info("艺术家源: '%s' 获得 %d 首", artist_name, len(tracks))
        return self._songs_to_objects(tracks)


class AlbumSource(CandidateSource):
    """同专辑歌曲源 - DJ 金矿"""

    async def collect(self, anchor: dict) -> list[Song]:
        album_id = anchor.get("album_id")
        song_name = anchor.get("name", "")
        if not album_id:
            detail = await self.mcp.get_song_detail(str(anchor.get("id", "")))
            album_id = detail.get("album_id") if isinstance(detail, dict) else None
            if not album_id:
                logger.warning("专辑源: 无法获取 '%s' 的 album_id", song_name)
                return []

        logger.info("专辑源: 获取专辑(ID:%s) 的歌曲...", album_id)
        tracks = await self.mcp.get_album_songs(str(album_id))
        anchor_id = str(anchor.get("id", ""))
        tracks = [t for t in tracks if str(t.id) != anchor_id]
        logger.info("专辑源: 获得 %d 首（不含锚点）", len(tracks))
        return tracks


class CrossArtistSource(CandidateSource):
    """跨艺术家搜索源 - 使用网易云相似艺人 API 寻找风格相近的其他 artist
    
    DJ 视角：这是最有价值的来源之一。相似艺人的作品往往：
    - 同一制作人圈子
    - 同一厂牌
    - BPM/Key/音色风格一致
    """

    MAX_ARTISTS = 8
    TRACKS_PER_ARTIST = 5
    TIMEOUT_SIMILAR = 20.0
    TIMEOUT_TRACKS = 12.0

    async def collect(self, anchor: dict) -> list[Song]:
        artist_id = anchor.get("artist_id")
        artist_name = anchor.get("artist", "")
        anchor_name = anchor.get("name", "")
        if not artist_id:
            detail = await self.mcp.get_song_detail(str(anchor.get("id", "")))
            artist_id = detail.get("artist_id") if isinstance(detail, dict) else None
            if not artist_id:
                logger.warning("跨艺术家源: 无法获取 '%s' 的 artist_id", artist_name)
                return []

        all_tracks = []
        seen_ids = set()
        anchor_artist = artist_name.lower()

        # 获取相似艺人（带重试）
        similar_artists = []
        for attempt in range(2):
            try:
                similar_artists = await asyncio.wait_for(
                    self.mcp.get_similar_artists(str(artist_id)),
                    timeout=self.TIMEOUT_SIMILAR,
                )
                if similar_artists:
                    break
            except asyncio.TimeoutError:
                logger.warning("跨艺术家源: 获取相似艺人超时 (attempt %d)", attempt + 1)
            except Exception as e:
                logger.warning("跨艺术家源: 获取相似艺人失败 - %s", e)

        if not similar_artists:
            logger.warning("跨艺术家源: 无相似艺人")
            return []

        logger.info("跨艺术家源: '%s' 有 %d 个相似艺人", artist_name, len(similar_artists))

        target_artists = [sa for sa in similar_artists[:self.MAX_ARTISTS] if sa.get("id")]
        logger.info("跨艺术家源: 并发获取 %d 个相似艺人的热门歌曲...", len(target_artists))

        async def fetch_artist_tracks(sa):
            for attempt in range(2):
                try:
                    return await asyncio.wait_for(
                        self.mcp.get_artist_tracks(str(sa["id"]), limit=self.TRACKS_PER_ARTIST),
                        timeout=self.TIMEOUT_TRACKS,
                    )
                except asyncio.TimeoutError:
                    if attempt == 0:
                        continue
                    logger.debug("跨艺术家源: 获取艺人 %s 歌曲超时", sa.get("name"))
                except Exception:
                    pass
            return []

        results = await asyncio.gather(*[fetch_artist_tracks(sa) for sa in target_artists])

        for tracks in results:
            for t in tracks:
                tid = str(t.id)
                t_artist = t.artist.lower()
                t_name = t.name
                if tid and tid not in seen_ids and anchor_artist not in t_artist:
                    if self._language_match(anchor_name, t_name):
                        if not self._is_low_quality(t_name):
                            seen_ids.add(tid)
                            all_tracks.append(t)

        logger.info("跨艺术家源: 共获得 %d 首（不同 artist）", len(all_tracks))
        return all_tracks


class StyleSongSource(CandidateSource):
    """曲风标签歌曲源 - 利用网易云官方曲风体系获取同风格歌曲
    
    需要 StyleHierarchy 来映射曲风名称 → tagId
    """

    def __init__(self, mcp_client: CloudMusicMCPClient, hierarchy=None):
        super().__init__(mcp_client)
        self.hierarchy = hierarchy

    async def collect(self, anchor: dict) -> list[Song]:
        if not self.hierarchy or not self.hierarchy.is_loaded():
            logger.debug("曲风歌曲源: 层级树未加载，跳过")
            return []

        # 获取锚点歌曲的 wiki 信息来提取曲风标签
        song_id = str(anchor.get("id", ""))
        anchor_name = anchor.get("name", "")
        if not song_id:
            return []

        try:
            wiki = await asyncio.wait_for(self.mcp.get_song_wiki(song_id), timeout=10.0)
            genres = wiki.get("genres", [])
        except Exception:
            logger.debug("曲风歌曲源: 获取 wiki 失败")
            return []

        if not genres:
            return []

        # 从曲风标签映射到 tagId，获取同曲风歌曲
        all_tracks = []
        seen_ids = {song_id}
        anchor_artist = anchor.get("artist", "").lower()

        for g in genres:
            # 尝试直接查找
            node = self.hierarchy.find(g)
            if not node and "-" in g:
                # 复合标签拆分
                for part in [p.strip() for p in g.split("-") if p.strip()]:
                    node = self.hierarchy.find(part)
                    if node:
                        break

            if not node:
                continue

            logger.info("曲风歌曲源: 曲风 '%s' → tagId=%s，获取同曲风歌曲...", g, node.tag_id)
            try:
                tracks = await asyncio.wait_for(
                    self.mcp.get_style_songs(str(node.tag_id), size=15, sort=0),
                    timeout=15.0,
                )
                for t in tracks:
                    tid = str(t.get("id", ""))
                    t_name = t.get("name", "")
                    t_artist = t.get("artist", "").lower()
                    if tid and tid not in seen_ids and anchor_artist not in t_artist:
                        if self._language_match(anchor_name, t_name):
                            if not self._is_low_quality(t_name):
                                seen_ids.add(tid)
                                all_tracks.append(
                                    Song(id=tid, name=t_name, artist=t.get("artist", "未知"))
                                )
            except Exception as e:
                logger.warning("曲风歌曲源: 获取 tagId=%s 失败 - %s", node.tag_id, e)

        logger.info("曲风歌曲源: 共获得 %d 首", len(all_tracks))
        return all_tracks


class GenreSearchSource(CandidateSource):
    """流派搜索源 - Fallback：基于 BPM 推断流派关键词搜索"""

    BPM_GENRE_MAP = [
        (0, 80, ["欧美 R&B", "欧美 soul", "抒情", "lofi"]),
        (80, 110, ["欧美 indie", "欧美流行", "欧美民谣"]),
        (110, 130, ["电子", "舞曲", "house", "disco"]),
        (130, 150, ["电子", "techno", "edm", "电音"]),
        (150, 999, ["drum and bass", "hardstyle", "速核"]),
    ]

    async def collect(self, anchor: dict) -> list[Song]:
        bpm = anchor.get("bpm")
        artist = anchor.get("artist", "")
        anchor_name = anchor.get("name", "")
        if not bpm:
            logger.info("流派源: 锚点无 BPM 数据，跳过")
            return []

        genres = []
        for low, high, g_list in self.BPM_GENRE_MAP:
            if low <= bpm < high:
                genres = g_list
                break

        if not genres:
            return []

        all_tracks = []
        seen_ids = set()
        anchor_artist = artist.lower()

        # 取前 2 个流派关键词，增加覆盖率
        for genre in genres[:2]:
            query = f"{genre} 热门"
            logger.info("流派源: 搜索 '%s'...", query)
            try:
                tracks = await self.mcp.search_song(query)
                for t in tracks[:8]:
                    tid = str(t.id)
                    t_artist = t.artist.lower()
                    t_name = t.name
                    if tid and tid not in seen_ids and anchor_artist not in t_artist:
                        if self._language_match(anchor_name, t_name):
                            if not self._is_low_quality(t_name):
                                seen_ids.add(tid)
                                all_tracks.append(t)
            except Exception as e:
                logger.warning("流派源: '%s' 搜索失败 - %s", query, e)

        logger.info("流派源: 共获得 %d 首（跨流派）", len(all_tracks))
        return all_tracks


class PlaylistSource(CandidateSource):
    """歌单来源 - 搜索包含锚点艺人/风格的相关歌单，获取歌单中的歌曲
    
    DJ 视角：用户创建的"R&B 深夜电台""卧室流行精选"等歌单
    是人类策展人精心编排的，风格一致性极高
    """

    async def collect(self, anchor: dict) -> list[Song]:
        artist = anchor.get("artist", "")
        anchor_name = anchor.get("name", "")
        if not artist:
            return []

        # 搜索策略：艺人名 + 精选/歌单
        queries = [
            f"{artist} 精选",
            f"{artist} 歌单",
        ]

        all_playlists = []
        seen_pl_ids = set()
        for query in queries:
            try:
                playlists = await asyncio.wait_for(
                    self.mcp.search_playlist(query, limit=5),
                    timeout=15.0,
                )
                for pl in playlists:
                    pl_id = str(pl.get("id", ""))
                    if pl_id and pl_id not in seen_pl_ids:
                        seen_pl_ids.add(pl_id)
                        all_playlists.append(pl)
            except Exception as e:
                logger.debug("歌单源: 搜索 '%s' 失败 - %s", query, e)

        if not all_playlists:
            logger.info("歌单源: 未找到相关歌单")
            return []

        logger.info("歌单源: 找到 %d 个相关歌单", len(all_playlists))

        # 取前 3 个歌单，获取歌曲
        target_playlists = all_playlists[:3]
        all_tracks = []
        seen_ids = set()
        anchor_artist = artist.lower()
        anchor_id = str(anchor.get("id", ""))

        for pl in target_playlists:
            pl_id = str(pl.get("id", ""))
            pl_name = pl.get("name", "")
            logger.info("歌单源: 获取歌单 '%s' 的歌曲...", pl_name)
            try:
                tracks = await asyncio.wait_for(
                    self.mcp.get_playlist_songs(pl_id, limit=20),
                    timeout=15.0,
                )
                for t in tracks:
                    tid = str(t.get("id", ""))
                    t_name = t.get("name", "")
                    t_artist = t.get("artist", "").lower()
                    if tid and tid not in seen_ids and tid != anchor_id:
                        if anchor_artist not in t_artist:
                            if self._language_match(anchor_name, t_name):
                                if not self._is_low_quality(t_name):
                                    seen_ids.add(tid)
                                    all_tracks.append(
                                        Song(id=tid, name=t_name, artist=t.get("artist", "未知"))
                                    )
            except Exception as e:
                logger.warning("歌单源: 获取歌单 %s 失败 - %s", pl_id, e)

        logger.info("歌单源: 共获得 %d 首", len(all_tracks))
        return all_tracks


class MultiSourceCollector:
    """多源候选采集器 - 整合所有来源"""

    def __init__(self, mcp_client: CloudMusicMCPClient, hierarchy=None):
        self.mcp = mcp_client
        self.sources = [
            AlbumSource(mcp_client),        # 同专辑 - DJ 金矿
            SimilarSource(mcp_client),      # 相似推荐
            ArtistTopSource(mcp_client),    # 艺人热门
            CrossArtistSource(mcp_client),  # 相似艺人 - 核心价值
            StyleSongSource(mcp_client, hierarchy),  # 曲风标签歌曲
            PlaylistSource(mcp_client),     # 歌单
            GenreSearchSource(mcp_client),  # 流派搜索 - Fallback
        ]

    @staticmethod
    def _deduplicate(songs: list[Song]) -> list[Song]:
        seen = set()
        result = []
        for song in songs:
            sid = str(song.id)
            if sid and sid not in seen:
                seen.add(sid)
                result.append(song)
        return result

    async def collect(self, anchors: list[dict]) -> list[Song]:
        all_candidates = []
        per_source_stats = {}

        for anchor in anchors:
            anchor_name = anchor.get("name", "未知")
            logger.info("为多源采集锚点: %s", anchor_name)

            for source in self.sources:
                source_name = source.__class__.__name__
                try:
                    logger.info("来源 %s: 开始采集...", source_name)
                    tracks = await asyncio.wait_for(source.collect(anchor), timeout=30.0)
                    all_candidates.extend(tracks)
                    per_source_stats[source_name] = per_source_stats.get(source_name, 0) + len(tracks)
                    logger.info("来源 %s: 采集完成 (%d 首)", source_name, len(tracks))
                except asyncio.TimeoutError:
                    logger.warning("来源 %s 采集超时", source_name)
                except Exception as e:
                    logger.warning("来源 %s 采集失败: %s", source_name, e)

        unique_candidates = self._deduplicate(all_candidates)
        logger.info("多源采集完成: %d 首候选（去重前 %d 首）", len(unique_candidates), len(all_candidates))
        for source_name, count in sorted(per_source_stats.items(), key=lambda x: -x[1]):
            logger.info("  - %s: %d 首", source_name, count)
        return unique_candidates

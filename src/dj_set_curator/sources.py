"""多源候选采集器 - 从网易云多个渠道收集候选歌曲"""

import asyncio
import logging
import re
from typing import Optional

from dj_set_curator.mcp_client import CloudMusicMCPClient

logger = logging.getLogger(__name__)


class CandidateSource:
    """候选来源基类"""

    def __init__(self, mcp_client: CloudMusicMCPClient):
        self.mcp = mcp_client

    async def collect(self, anchor: dict) -> list[dict]:
        """返回候选歌曲列表 [{id, name, artist}]"""
        raise NotImplementedError

    @staticmethod
    def _has_chinese(text: str) -> bool:
        """检查文本是否包含中文字符"""
        return bool(re.search(r'[\u4e00-\u9fff]', text))

    @staticmethod
    def _language_match(anchor_name: str, candidate_name: str) -> bool:
        """
        简单的语言一致性检查
        如果锚点是英文歌名，候选也应该是英文歌名（减少跨语言噪音）
        """
        anchor_has_cn = CandidateSource._has_chinese(anchor_name)
        cand_has_cn = CandidateSource._has_chinese(candidate_name)
        # 锚点和候选语言一致时返回 True
        # 特殊情况：都允许（不做严格限制），只排除"锚点英文但候选明显中文"的情况
        if not anchor_has_cn and cand_has_cn:
            return False
        return True


class SimilarSource(CandidateSource):
    """网易云相似推荐源"""

    async def collect(self, anchor: dict) -> list[dict]:
        song_id = str(anchor.get("id", ""))
        if not song_id:
            return []
        logger.info("相似推荐源: 获取 '%s' 的相似推荐...", anchor.get("name", song_id))
        similar = await self.mcp.get_similar_songs(song_id, limit=30)
        logger.info("相似推荐源: '%s' 获得 %d 首", anchor.get("name", song_id), len(similar))
        return similar


class ArtistTopSource(CandidateSource):
    """艺术家热门歌曲源"""

    async def collect(self, anchor: dict) -> list[dict]:
        artist_id = anchor.get("artist_id")
        artist_name = anchor.get("artist", "")
        if not artist_id:
            # 尝试通过搜索获取 artist_id
            logger.debug("艺术家源: 尝试搜索 '%s' 获取 artist_id...", artist_name)
            detail = await self.mcp.get_song_detail(str(anchor.get("id", "")))
            artist_id = detail.get("artist_id") if isinstance(detail, dict) else None
            if not artist_id:
                logger.warning("艺术家源: 无法获取 '%s' 的 artist_id", artist_name)
                return []

        logger.info("艺术家源: 获取 '%s'(ID:%s) 的热门歌曲...", artist_name, artist_id)
        tracks = await self.mcp.get_artist_tracks(str(artist_id), limit=12)
        logger.info("艺术家源: '%s' 获得 %d 首", artist_name, len(tracks))
        return tracks


class AlbumSource(CandidateSource):
    """同专辑歌曲源"""

    async def collect(self, anchor: dict) -> list[dict]:
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
        # 移除锚点歌曲本身
        anchor_id = str(anchor.get("id", ""))
        tracks = [t for t in tracks if str(t.get("id", "")) != anchor_id]
        logger.info("专辑源: 获得 %d 首（不含锚点）", len(tracks))
        return tracks


class DailyRecSource(CandidateSource):
    """每日推荐源"""

    async def collect(self, anchor: dict) -> list[dict]:
        logger.info("每日推荐源: 获取今日推荐...")
        # 每日推荐目前没有直接 MCP 工具，通过搜索风格标签模拟
        # 使用锚点艺术家的风格搜索
        artist = anchor.get("artist", "")
        name = anchor.get("name", "")
        if not artist:
            return []
        # 搜索 "artist_name" 的热门歌曲（类似 ArtistTopSource 但用搜索）
        query = f"{artist}"
        logger.info("每日推荐源: 搜索 '%s' 热门歌曲...", query)
        try:
            tracks = await self.mcp.search_song(query)
            # 只取前 15 首，且排除锚点本身
            anchor_id = str(anchor.get("id", ""))
            tracks = [t for t in tracks[:8] if str(t.get("id", "")) != anchor_id]
            logger.info("每日推荐源: 获得 %d 首", len(tracks))
            return tracks
        except Exception as e:
            logger.warning("每日推荐源: 搜索失败 - %s", e)
            return []


class TagSearchSource(CandidateSource):
    """标签搜索源 - 用风格标签搜索"""

    async def collect(self, anchor: dict) -> list[dict]:
        artist = anchor.get("artist", "")
        name = anchor.get("name", "")
        if not artist:
            return []
        # 尝试几种搜索组合
        queries = [
            f"{artist} 热门",
        ]
        all_tracks = []
        seen_ids = set()
        for query in queries:
            logger.info("标签搜索源: 搜索 '%s'...", query)
            try:
                tracks = await self.mcp.search_song(query)
                for t in tracks[:5]:
                    tid = str(t.get("id", ""))
                    if tid and tid not in seen_ids:
                        seen_ids.add(tid)
                        all_tracks.append(t)
            except Exception as e:
                logger.warning("标签搜索源: '%s' 搜索失败 - %s", query, e)
        # 排除锚点
        anchor_id = str(anchor.get("id", ""))
        all_tracks = [t for t in all_tracks if str(t.get("id", "")) != anchor_id]
        logger.info("标签搜索源: 共获得 %d 首", len(all_tracks))
        return all_tracks


class GenreSearchSource(CandidateSource):
    """流派搜索源 - 基于 BPM 推断流派并搜索相关歌曲"""

    # BPM → 可能流派关键词映射（使用中文关键词，网易云对英文流派搜索效果差）
    BPM_GENRE_MAP = [
        (0, 80, ["欧美 R&B", "欧美 soul", "抒情", "lofi"]),
        (80, 110, ["欧美 indie", "欧美流行", "欧美民谣"]),
        (110, 130, ["电子", "舞曲", "house", "disco"]),
        (130, 150, ["电子", "techno", "edm", "电音"]),
        (150, 999, [" drum and bass", "hardstyle", "速核"]),
    ]

    async def collect(self, anchor: dict) -> list[dict]:
        bpm = anchor.get("bpm")
        artist = anchor.get("artist", "")
        anchor_name = anchor.get("name", "")
        if not bpm:
            logger.info("流派源: 锚点无 BPM 数据，跳过")
            return []

        # 根据 BPM 选择流派关键词
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

        # 搜索每个流派关键词 + "热门"
        for genre in genres[:1]:  # 只取第 1 个流派，避免过多噪音
            query = f"{genre} 热门"
            logger.info("流派源: 搜索 '%s'...", query)
            try:
                tracks = await self.mcp.search_song(query)
                for t in tracks[:5]:
                    tid = str(t.get("id", ""))
                    t_artist = t.get("artist", "").lower()
                    t_name = t.get("name", "")
                    # 过滤条件：
                    # 1. 排除锚点 artist
                    # 2. 语言一致性（避免跨语言噪音）
                    # 3. 排除明显非音乐内容（DJ版、车载版等低质内容）
                    if tid and tid not in seen_ids and anchor_artist not in t_artist:
                        if self._language_match(anchor_name, t_name):
                            if not any(kw in t_name.lower() for kw in ["dj版", "车载版", "抖音", "cover"]):
                                seen_ids.add(tid)
                                all_tracks.append(t)
            except Exception as e:
                logger.warning("流派源: '%s' 搜索失败 - %s", query, e)

        logger.info("流派源: 共获得 %d 首（跨流派）", len(all_tracks))
        return all_tracks


class CrossArtistSource(CandidateSource):
    """跨艺术家搜索源 - 使用网易云相似艺人 API 寻找风格相近的其他 artist"""

    async def collect(self, anchor: dict) -> list[dict]:
        artist_id = anchor.get("artist_id")
        artist_name = anchor.get("artist", "")
        anchor_name = anchor.get("name", "")
        if not artist_id:
            # 尝试通过搜索获取 artist_id
            detail = await self.mcp.get_song_detail(str(anchor.get("id", "")))
            artist_id = detail.get("artist_id") if isinstance(detail, dict) else None
            if not artist_id:
                logger.warning("跨艺术家源: 无法获取 '%s' 的 artist_id", artist_name)
                return []

        all_tracks = []
        seen_ids = set()
        anchor_artist = artist_name.lower()

        # 1. 获取相似艺人
        logger.info("跨艺术家源: 获取 '%s'(ID:%s) 的相似艺人...", artist_name, artist_id)
        try:
            similar_artists = await asyncio.wait_for(
                self.mcp.get_similar_artists(str(artist_id)), timeout=15.0
            )
        except asyncio.TimeoutError:
            logger.warning("跨艺术家源: 获取相似艺人超时")
            return []
        except Exception as e:
            logger.warning("跨艺术家源: 获取相似艺人失败 - %s", e)
            return []

        logger.info("跨艺术家源: '%s' 有 %d 个相似艺人", artist_name, len(similar_artists))

        # 2. 并发获取相似艺人的热门歌曲（取前 5 个相似艺人，每个 3 首）
        target_artists = [sa for sa in similar_artists[:5] if sa.get("id")]
        logger.info("跨艺术家源: 并发获取 %d 个相似艺人的热门歌曲...", len(target_artists))

        async def fetch_artist_tracks(sa):
            try:
                return await asyncio.wait_for(
                    self.mcp.get_artist_tracks(str(sa["id"]), limit=3), timeout=10.0
                )
            except asyncio.TimeoutError:
                logger.debug("跨艺术家源: 获取艺人 %s 歌曲超时", sa.get("name"))
                return []
            except Exception:
                return []

        # 并发请求所有相似艺人的歌曲
        results = await asyncio.gather(*[fetch_artist_tracks(sa) for sa in target_artists])

        for tracks in results:
            for t in tracks:
                tid = str(t.get("id", ""))
                t_artist = t.get("artist", "").lower()
                t_name = t.get("name", "")
                # 过滤条件：
                # 1. 排除锚点 artist
                # 2. 去重
                # 3. 语言一致性
                # 4. 排除低质内容
                if tid and tid not in seen_ids and anchor_artist not in t_artist:
                    if self._language_match(anchor_name, t_name):
                        if not any(kw in t_name.lower() for kw in ["dj版", "车载版", "抖音", "cover"]):
                            seen_ids.add(tid)
                            all_tracks.append(t)

        logger.info("跨艺术家源: 共获得 %d 首（不同 artist）", len(all_tracks))
        return all_tracks


class MultiSourceCollector:
    """多源候选采集器 - 整合所有来源"""

    def __init__(self, mcp_client: CloudMusicMCPClient):
        self.mcp = mcp_client
        self.sources = [
            SimilarSource(mcp_client),
            ArtistTopSource(mcp_client),
            AlbumSource(mcp_client),
            DailyRecSource(mcp_client),
            TagSearchSource(mcp_client),
            GenreSearchSource(mcp_client),
            CrossArtistSource(mcp_client),
        ]

    @staticmethod
    def _deduplicate(songs: list[dict]) -> list[dict]:
        """按 song id 去重"""
        seen = set()
        result = []
        for song in songs:
            sid = str(song.get("id", ""))
            if sid and sid not in seen:
                seen.add(sid)
                result.append(song)
        return result

    async def collect(self, anchors: list[dict]) -> list[dict]:
        """
        从多个来源收集候选歌曲（所有来源并发执行）

        Args:
            anchors: 锚点歌曲列表 [{id, name, artist, artist_id, album_id}]

        Returns:
            去重后的候选歌曲列表
        """
        all_candidates = []
        per_source_stats = {}

        for anchor in anchors:
            anchor_name = anchor.get("name", "未知")
            logger.info("为多源采集锚点: %s", anchor_name)

            # 并发执行所有来源的采集（每个 source 最多 30 秒）
            async def collect_from_source(source):
                source_name = source.__class__.__name__
                try:
                    tracks = await asyncio.wait_for(source.collect(anchor), timeout=30.0)
                    return source_name, tracks
                except asyncio.TimeoutError:
                    logger.warning("来源 %s 采集超时", source_name)
                    return source_name, []
                except Exception as e:
                    logger.warning("来源 %s 采集失败: %s", source_name, e)
                    return source_name, []

            results = await asyncio.gather(*[collect_from_source(s) for s in self.sources])

            for source_name, tracks in results:
                all_candidates.extend(tracks)
                per_source_stats[source_name] = per_source_stats.get(source_name, 0) + len(tracks)

        # 去重
        unique = self._deduplicate(all_candidates)
        logger.info(
            "多源采集完成: 原始 %d 首, 去重后 %d 首 | 各源统计: %s",
            len(all_candidates), len(unique), per_source_stats,
        )
        return unique

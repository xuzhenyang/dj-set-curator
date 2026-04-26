"""多源候选采集器 - 从网易云多个渠道收集候选歌曲"""

import logging
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
        tracks = await self.mcp.get_artist_tracks(str(artist_id), limit=20)
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
            tracks = [t for t in tracks[:15] if str(t.get("id", "")) != anchor_id]
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
                for t in tracks[:10]:
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
        从多个来源收集候选歌曲

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

            for source in self.sources:
                source_name = source.__class__.__name__
                try:
                    tracks = await source.collect(anchor)
                    all_candidates.extend(tracks)
                    per_source_stats[source_name] = per_source_stats.get(source_name, 0) + len(tracks)
                except Exception as e:
                    logger.warning("来源 %s 采集失败: %s", source_name, e)

        # 去重
        unique = self._deduplicate(all_candidates)
        logger.info(
            "多源采集完成: 原始 %d 首, 去重后 %d 首 | 各源统计: %s",
            len(all_candidates), len(unique), per_source_stats,
        )
        return unique

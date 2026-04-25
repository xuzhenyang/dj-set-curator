"""选曲引擎核心 - 整合锚点分析、相似推荐、筛选排序"""

import logging
from typing import Optional

from dj_set_curator.anchor import AnchorAnalyzer
from dj_set_curator.filters import SongFilter
from dj_set_curator.models import AnchorSong, ScoredSong
from dj_set_curator.mcp_client import CloudMusicMCPClient

logger = logging.getLogger(__name__)


class DJSetCurator:
    """DJ 选曲引擎"""

    def __init__(
        self,
        mcp_client: CloudMusicMCPClient,
        filter_config: Optional[dict] = None,
    ):
        self.mcp = mcp_client
        self.anchor_analyzer = AnchorAnalyzer()
        self.filter = SongFilter(**(filter_config or {}))

    @staticmethod
    def _deduplicate(songs: list[dict]) -> list[dict]:
        """按 song id 去重，保留第一次出现的顺序"""
        seen = set()
        result = []
        for song in songs:
            sid = str(song.get("id", ""))
            if sid and sid not in seen:
                seen.add(sid)
                result.append(song)
        return result

    @staticmethod
    def _remove_anchors(candidates: list[dict], anchors: list[AnchorSong]) -> list[dict]:
        """从候选列表中移除锚点歌曲本身"""
        anchor_ids = {a.id for a in anchors}
        return [c for c in candidates if str(c.get("id", "")) not in anchor_ids]

    async def build_playlist(
        self,
        anchor_queries: list[str],
        playlist_name: str,
        target_count: int = 20,
        diversity_ratio: float = 0.3,
    ) -> dict:
        """
        构建歌单的主流程

        Args:
            anchor_queries: 锚点歌曲列表（1-2 首）
            playlist_name: 输出歌单名称
            target_count: 目标歌曲数量
            diversity_ratio: 多样性比例（0-1），控制是否混入非相似歌曲

        Returns:
            {
                "playlist_id": str,
                "playlist_name": str,
                "anchors": list[AnchorSong],
                "selected_songs": list[ScoredSong],
                "stats": {
                    "total_candidates": int,
                    "filtered_count": int,
                    "avg_score": float
                }
            }
        """
        if not anchor_queries:
            raise ValueError("至少需要提供一个锚点歌曲")

        # 1. 解析锚点
        logger.info("正在解析 %d 个锚点歌曲...", len(anchor_queries))
        anchors = await self.anchor_analyzer.resolve_multiple(anchor_queries, self.mcp)
        logger.info("锚点解析完成: %s", anchors)

        # 2. 获取相似推荐（每个锚点分别获取）
        all_candidates = []
        for anchor in anchors:
            logger.info("获取锚点 '%s' 的相似推荐...", anchor.name)
            similar = await self.mcp.get_similar_songs(anchor.id, limit=30)
            logger.info("锚点 '%s' 获得 %d 首相似推荐", anchor.name, len(similar))
            all_candidates.extend(similar)

        # 3. 去重 + 移除锚点本身
        unique_candidates = self._deduplicate(all_candidates)
        unique_candidates = self._remove_anchors(unique_candidates, anchors)
        logger.info("去重后候选歌曲: %d 首", len(unique_candidates))

        if not unique_candidates:
            raise RuntimeError("未获取到任何候选歌曲，请检查锚点歌曲是否有效或 MCP Server 登录状态")

        # 4. 评分排序
        scored = self.filter.score_candidates(unique_candidates, anchors)

        # 5. 应用多样性：按 diversity_ratio 混入一些分数较低但风格不同的歌曲
        selected = self._apply_diversity(scored, target_count, diversity_ratio)

        if not selected:
            raise RuntimeError("筛选后没有符合条件的歌曲")

        # 6. 创建歌单并添加
        logger.info("正在创建歌单 '%s'...", playlist_name)
        playlist_id = await self.mcp.create_playlist(playlist_name)
        logger.info("歌单创建成功，ID: %s", playlist_id)

        track_ids = [str(s.song["id"]) for s in selected]
        await self.mcp.add_tracks_to_playlist(playlist_id, track_ids)
        logger.info("已添加 %d 首歌曲到歌单", len(track_ids))

        avg_score = sum(s.score for s in selected) / len(selected) if selected else 0

        return {
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "anchors": anchors,
            "selected_songs": selected,
            "stats": {
                "total_candidates": len(unique_candidates),
                "filtered_count": len(selected),
                "avg_score": round(avg_score, 2),
            },
        }

    def _apply_diversity(
        self,
        scored: list[ScoredSong],
        target_count: int,
        diversity_ratio: float,
    ) -> list[ScoredSong]:
        """
        应用多样性策略

        - (1 - diversity_ratio) 比例取 Top 高分歌曲
        - diversity_ratio 比例从剩余候选中取（避免全部同质化）
        """
        if not scored:
            return []

        diversity_count = int(target_count * diversity_ratio)
        top_count = target_count - diversity_count

        selected = scored[:top_count]

        # 从剩余候选中选取不与已选重复艺术家的歌曲作为多样性补充
        remaining = scored[top_count:]
        used_artists = {s.song.get("artist", "").lower() for s in selected}

        for candidate in remaining:
            if len(selected) >= target_count:
                break
            artist = candidate.song.get("artist", "").lower()
            if artist not in used_artists:
                selected.append(candidate)
                used_artists.add(artist)

        # 如果多样性补充不够，用剩余高分补齐
        if len(selected) < target_count:
            idx = top_count
            while len(selected) < target_count and idx < len(scored):
                if scored[idx] not in selected:
                    selected.append(scored[idx])
                idx += 1

        return selected

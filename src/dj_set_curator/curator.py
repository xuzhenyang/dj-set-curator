"""选曲引擎核心 - 整合锚点分析、相似推荐、筛选排序"""

import logging
from typing import Optional

from dj_set_curator.anchor import AnchorAnalyzer
from dj_set_curator.audio_analyzer import AudioAnalyzer
from dj_set_curator.arranger import EnergyArcArranger
from dj_set_curator.filters import SongFilter
from dj_set_curator.models import AnchorSong, ScoredSong
from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.sources import MultiSourceCollector

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

    async def _expand_candidates(
        self,
        candidates: list[dict],
        anchors: list[AnchorSong],
        target_count: int,
        max_extra_anchors: int = 3,
        limit_per_anchor: int = 20,
    ) -> list[dict]:
        """
        级联扩展：用已选候选中的 top 歌曲作为二级锚点，获取更多相似推荐

        Args:
            candidates: 当前候选池
            anchors: 原始锚点（用于排除）
            target_count: 目标歌曲数量
            max_extra_anchors: 最多用多少首二级锚点
            limit_per_anchor: 每个二级锚点请求的相似推荐数量
        """
        if len(candidates) >= target_count:
            return candidates

        # 从当前候选中选取前 N 首作为二级锚点
        # 优先选不同艺术家的，避免同质化
        extra_anchor_ids = set()
        extra_anchors = []
        used_artists = set()

        for song in candidates:
            sid = str(song.get("id", ""))
            artist = song.get("artist", "").lower()
            # 排除原始锚点
            if sid in {a.id for a in anchors}:
                continue
            # 避免重复艺术家，增加多样性
            if artist in used_artists and len(extra_anchors) >= 2:
                continue
            if sid not in extra_anchor_ids:
                extra_anchor_ids.add(sid)
                extra_anchors.append(song)
                used_artists.add(artist)
                if len(extra_anchors) >= max_extra_anchors:
                    break

        if not extra_anchors:
            logger.warning("无可用二级锚点进行扩展")
            return candidates

        logger.info(
            "候选池不足 (%d < %d)，启用级联扩展，使用 %d 个二级锚点: %s",
            len(candidates), target_count, len(extra_anchors),
            [f"{s.get('name')}({s.get('id')})" for s in extra_anchors],
        )

        all_new = list(candidates)  # 保留原有候选
        for song in extra_anchors:
            logger.info("获取二级锚点 '%s' 的相似推荐...", song.get("name", "未知"))
            similar = await self.mcp.get_similar_songs(str(song["id"]), limit=limit_per_anchor)
            logger.info("二级锚点 '%s' 获得 %d 首相似推荐", song.get("name", "未知"), len(similar))
            all_new.extend(similar)

        # 去重 + 移除原始锚点
        unique = self._deduplicate(all_new)
        unique = self._remove_anchors(unique, anchors)
        logger.info("级联扩展后候选歌曲: %d 首", len(unique))
        return unique

    async def build_playlist(
        self,
        anchor_queries: list[str],
        playlist_name: str,
        target_count: int = 20,
        diversity_ratio: float = 0.3,
        enable_expand: bool = True,
        arrange_mode: str = "flat",
    ) -> dict:
        """
        构建歌单的主流程

        Args:
            anchor_queries: 锚点歌曲列表（1-2 首）
            playlist_name: 输出歌单名称
            target_count: 目标歌曲数量
            diversity_ratio: 多样性比例（0-1），控制是否混入非相似歌曲
            enable_expand: 是否启用级联扩展（候选不足时用二级锚点扩充）
            arrange_mode: 能量曲线编排模式 (flat/warm-up/peak-mid/rollercoaster/climax-end)

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

        # 1.5 分析锚点歌曲的 BPM/Key（用于后续评分参考）
        analyzer = AudioAnalyzer(self.mcp)
        for anchor in anchors:
            if anchor.bpm is None or anchor.key is None:
                analysis = await analyzer.analyze_song(anchor.id)
                if analysis:
                    if anchor.bpm is None:
                        anchor.bpm = analysis.get("bpm")
                    if anchor.key is None:
                        anchor.key = analysis.get("camelot") or analysis.get("key")
                    logger.info(
                        "锚点音频分析: %s - BPM=%s Key=%s",
                        anchor.name, anchor.bpm, anchor.key,
                    )

        # 2. 多源采集候选歌曲
        collector = MultiSourceCollector(self.mcp)
        # 将 AnchorSong 转换为 dict 格式，并补充 artist_id / album_id
        anchor_dicts = []
        for a in anchors:
            ad = {"id": a.id, "name": a.name, "artist": a.artist}
            try:
                detail = await self.mcp.get_song_detail(a.id)
                if isinstance(detail, dict):
                    ad["artist_id"] = detail.get("artist_id")
                    ad["album_id"] = detail.get("album_id")
            except Exception as e:
                logger.warning("获取锚点详情失败: %s - %s", a.name, e)
            anchor_dicts.append(ad)
        all_candidates = await collector.collect(anchor_dicts)

        # 3. 去重 + 移除锚点本身
        unique_candidates = self._remove_anchors(all_candidates, anchors)
        logger.info("去重后候选歌曲: %d 首", len(unique_candidates))

        if not unique_candidates:
            raise RuntimeError("未获取到任何候选歌曲，请检查锚点歌曲是否有效或 MCP Server 登录状态")

        # 4. 级联扩展（如启用且候选不足）
        if enable_expand and len(unique_candidates) < target_count:
            unique_candidates = await self._expand_candidates(
                unique_candidates, anchors, target_count
            )

        # 5. 音频分析：为候选歌曲补充 BPM/Key 数据
        analyzer = AudioAnalyzer(self.mcp)
        for candidate in unique_candidates:
            cid = str(candidate.get("id", ""))
            if not cid:
                continue
            # 只有缺失 BPM/Key 时才分析
            has_bpm = "bpm" in candidate and candidate["bpm"] is not None
            has_key = "key" in candidate and candidate["key"] is not None
            if not has_bpm or not has_key:
                analysis = await analyzer.analyze_song(cid)
                if analysis:
                    if not has_bpm:
                        candidate["bpm"] = analysis.get("bpm")
                    if not has_key:
                        candidate["key"] = analysis.get("camelot") or analysis.get("key")
                    logger.info(
                        "音频分析: %s - BPM=%s Key=%s",
                        candidate.get("name", "未知"),
                        analysis.get("bpm"),
                        analysis.get("key"),
                    )

        # 6. 评分排序
        scored = self.filter.score_candidates(unique_candidates, anchors)

        # 7. 应用多样性：按 diversity_ratio 混入一些分数较低但风格不同的歌曲
        selected = self._apply_diversity(scored, target_count, diversity_ratio)

        if not selected:
            raise RuntimeError("筛选后没有符合条件的歌曲")

        # 8. 能量曲线编排
        if arrange_mode != "flat":
            arranger = EnergyArcArranger(self.mcp, arc_mode=arrange_mode)
            selected = await arranger.arrange(
                selected, bpm_tolerance=self.filter.bpm_tolerance
            )

        # 7. 组装最终歌单：锚点歌曲 + 选中歌曲（去重，锚点放前面）
        anchor_ids = [a.id for a in anchors]
        selected_ids = [str(s.song["id"]) for s in selected]
        # 过滤掉已选中的锚点（避免重复）
        selected_ids = [sid for sid in selected_ids if sid not in anchor_ids]
        track_ids = anchor_ids + selected_ids

        # 8. 创建歌单并添加
        logger.info("正在创建歌单 '%s'...", playlist_name)
        playlist_id = await self.mcp.create_playlist(playlist_name)
        logger.info("歌单创建成功，ID: %s", playlist_id)

        await self.mcp.add_tracks_to_playlist(playlist_id, track_ids)
        logger.info("已添加 %d 首歌曲到歌单（含 %d 首锚点）", len(track_ids), len(anchor_ids))

        avg_score = sum(s.score for s in selected) / len(selected) if selected else 0
        total_tracks = len(track_ids)

        return {
            "playlist_id": playlist_id,
            "playlist_name": playlist_name,
            "anchors": anchors,
            "selected_songs": selected,
            "stats": {
                "total_candidates": len(unique_candidates),
                "filtered_count": total_tracks,
                "selected_count": len(selected_ids),
                "anchor_count": len(anchor_ids),
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

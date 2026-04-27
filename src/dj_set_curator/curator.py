"""选曲引擎核心 - 整合锚点分析、多源采集、过渡评分、序列构建"""

import asyncio
import logging
import time
from typing import Optional

from dj_set_curator.anchor import AnchorAnalyzer
from dj_set_curator.audio_analyzer import AudioAnalyzer, BatchAudioAnalyzer
from dj_set_curator.deduplicator import Deduplicator
from dj_set_curator.energy_heuristics import estimate_energy
from dj_set_curator.expansion import CascadeExpander
from dj_set_curator.filters import SongFilter
from dj_set_curator.models import AnchorSong, ScoredSong, Song
from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.playlist_naming import format_playlist_name
from dj_set_curator.sources import MultiSourceCollector
from dj_set_curator.transition import SequentialSelector, TransitionScorer

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
        self._status = {"stage": "idle", "progress": 0, "message": ""}

    def get_status(self) -> dict:
        """获取当前构建状态"""
        return self._status.copy()

    async def build_playlist(
        self,
        anchor_queries: list[str],
        playlist_name: Optional[str] = None,
        target_count: int = 20,
        diversity_ratio: float = 0.8,
        enable_expand: bool = True,
        arrange_mode: str = "flat",
        dry_run: bool = False,
    ) -> dict:
        """
        构建歌单的主流程（v2.0 - Transition-based selection）

        流程：锚点 -> 多源采集 -> 粗粒度能量估计 -> 预过滤 -> 贪心序列构建 -> 创建歌单
        """
        total_start = time.time()
        self._status = {
            "stage": "connecting",
            "progress": 5,
            "message": "连接 MCP Server...",
        }
        if not anchor_queries:
            raise ValueError("至少需要提供一个锚点歌曲")

        # 1. 解析锚点
        t0 = time.time()
        logger.info("正在解析 %d 个锚点歌曲...", len(anchor_queries))
        anchors = await self.anchor_analyzer.resolve_multiple(anchor_queries, self.mcp)
        self._status = {
            "stage": "anchors",
            "progress": 15,
            "message": f"解析锚点 ({len(anchors)}首)",
        }
        logger.info("[计时] 解析锚点: %.2fs", time.time() - t0)

        # 生成最终歌单名称（方案 E）
        final_name = format_playlist_name(playlist_name, anchors, arrange_mode)
        logger.info("歌单名称: %s", final_name)

        # 2. 分析锚点 BPM/Key + 精能量分析
        t0 = time.time()
        analyzer = AudioAnalyzer(self.mcp, max_analysis_duration=15.0)
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
                        anchor.name,
                        anchor.bpm,
                        anchor.key,
                    )
        # 尝试精分析锚点能量（复用同一个 EnergyAnalyzer 实例）
        from dj_set_curator.arranger import EnergyAnalyzer

        energy_analyzer = EnergyAnalyzer(self.mcp)
        for anchor in anchors:
            if anchor.bpm is not None:
                anchor_energy = anchor.bpm * 0.5
                try:
                    precise = await energy_analyzer.analyze_energy(anchor.id)
                    if precise is not None:
                        anchor_energy = precise
                except Exception:
                    pass
                anchor.energy = anchor_energy

        # 3. 多源采集候选歌曲
        t0 = time.time()
        collector = MultiSourceCollector(self.mcp)
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
        self._status = {
            "stage": "collection",
            "progress": 30,
            "message": f"多源采集 ({len(all_candidates)}首候选)",
        }
        logger.info("[计时] 多源采集: %.2fs", time.time() - t0)

        # 4. 去重 + 移除锚点本身 + 歌曲名去重
        t0 = time.time()
        unique_candidates = Deduplicator.by_id(all_candidates)
        unique_candidates = Deduplicator.remove_anchors(unique_candidates, anchors)
        unique_candidates = Deduplicator.by_name(unique_candidates)
        logger.info("去重后候选歌曲: %d 首", len(unique_candidates))
        logger.info("[计时] 去重: %.2fs", time.time() - t0)

        if not unique_candidates:
            raise RuntimeError(
                "未获取到任何候选歌曲，请检查锚点歌曲是否有效或 MCP Server 登录状态"
            )

        # 5. 级联扩展（如启用且候选不足）
        if enable_expand and len(unique_candidates) < target_count:
            expander = CascadeExpander(self.mcp)
            unique_candidates = await expander.expand(
                unique_candidates, anchors, target_count
            )

        # 6. 粗粒度能量估计（所有候选）+ 音频分析（全量并发）
        t_analysis_start = time.time()

        # 先给所有候选加上能量估计
        for candidate in unique_candidates:
            candidate.energy = estimate_energy(candidate)

        # 按能量接近锚点平均值排序（优先分析能量匹配度高的）
        anchor_energies = [
            a.energy for a in anchors if hasattr(a, "energy") and a.energy is not None
        ]
        anchor_avg_energy = sum(anchor_energies) / max(len(anchor_energies), 1)
        unique_candidates.sort(key=lambda x: abs((x.energy or 50) - anchor_avg_energy))

        def _update_status(stage, progress, message):
            self._status = {"stage": stage, "progress": progress, "message": message}

        batch_analyzer = BatchAudioAnalyzer(analyzer, status_callback=_update_status)
        analyzed_count, skipped_count = await batch_analyzer.analyze_songs_batch(
            unique_candidates, t_analysis_start, time_limit=120.0
        )
        analyzer.flush_cache()  # 批量分析结束后统一持久化缓存

        logger.info(
            "音频分析完成: %d/%d 首成功, %d 首跳过, 耗时 %.1fs",
            analyzed_count,
            len(
                [
                    s
                    for s in unique_candidates
                    if s.bpm is None or s.key is None
                ]
            ),
            skipped_count,
            time.time() - t_analysis_start,
        )

        # 7. 预过滤：用 SongFilter 过滤掉低分候选
        t0 = time.time()
        scored = self.filter.score_candidates(unique_candidates, anchors)
        min_score = self.filter.min_score
        filtered = [s for s in scored if s.score >= min_score]
        logger.info(
            "预过滤: %d 首候选中 %d 首通过 (min_score=%s)",
            len(scored),
            len(filtered),
            min_score,
        )
        logger.info("[计时] 预过滤: %.2fs", time.time() - t0)

        if not filtered:
            logger.warning("预过滤后无候选，放宽限制使用全部候选")
            filtered = scored

        # 提取过滤后的候选 Song
        filtered_candidates = [s.song for s in filtered]

        # 8. 贪心序列构建（核心改变）
        t0 = time.time()
        scorer = TransitionScorer(bpm_tolerance=self.filter.bpm_tolerance)
        selector = SequentialSelector(scorer, arrange_mode=arrange_mode)
        selected = selector.select(filtered_candidates, anchors, target_count)
        self._status = {
            "stage": "selection",
            "progress": 80,
            "message": f"选曲构建 ({len(selected)}首)",
        }
        logger.info("[计时] 贪心序列构建: %.2fs", time.time() - t0)

        if not selected:
            raise RuntimeError("筛选后没有符合条件的歌曲")

        # 9. 对最终入选歌曲做精能量分析（可选，提升质量）
        for s in selected:
            sid = str(s.song.id)
            try:
                precise_energy = await energy_analyzer.analyze_energy(sid)
                if precise_energy is not None:
                    s.song.energy = precise_energy
            except Exception:
                pass  # 保持 heuristics 能量

        # 10. 组装最终歌单：锚点歌曲 + 选中歌曲（去重，锚点放前面）
        anchor_ids = [a.id for a in anchors]
        selected_ids = [str(s.song.id) for s in selected]
        selected_ids = [sid for sid in selected_ids if sid not in anchor_ids]
        track_ids = anchor_ids + selected_ids

        # 11. 创建歌单并添加
        if not dry_run:
            t0 = time.time()
            self._status = {
                "stage": "creating",
                "progress": 95,
                "message": "创建歌单...",
            }
            logger.info("正在创建歌单 '%s'...", final_name)
            playlist_id = await self.mcp.create_playlist(final_name)
            logger.info("歌单创建成功，ID: %s", playlist_id)

            await self.mcp.add_tracks_to_playlist(playlist_id, track_ids)
            logger.info(
                "已添加 %d 首歌曲到歌单（含 %d 首锚点）",
                len(track_ids),
                len(anchor_ids),
            )
            logger.info("[计时] 创建歌单: %.2fs", time.time() - t0)
        else:
            playlist_id = None
            logger.info("[dry_run] 跳过创建歌单")

        self._status = {"stage": "done", "progress": 100, "message": "完成"}
        logger.info("[计时] 总计: %.2fs", time.time() - total_start)

        avg_score = sum(s.score for s in selected) / len(selected) if selected else 0
        total_tracks = len(track_ids)

        return {
            "playlist_id": playlist_id,
            "playlist_name": final_name,
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

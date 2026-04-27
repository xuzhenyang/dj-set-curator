"""选曲引擎核心 - 整合锚点分析、多源采集、过渡评分、序列构建"""

import asyncio
import logging
import time
from typing import Optional

from dj_set_curator.anchor import AnchorAnalyzer
from dj_set_curator.audio_analyzer import AudioAnalyzer
from dj_set_curator.filters import SongFilter
from dj_set_curator.models import AnchorSong, ScoredSong
from dj_set_curator.mcp_client import CloudMusicMCPClient
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

    @staticmethod
    def _deduplicate_by_name(candidates: list[dict]) -> list[dict]:
        """按歌曲名+艺术家去重（忽略 ID，同名同 artist 视为重复）"""
        seen = set()
        result = []
        for song in candidates:
            name = song.get("name", "").lower().strip()
            artist = song.get("artist", "").lower().strip()
            key = f"{name}::{artist}"
            if key and key not in seen:
                seen.add(key)
                result.append(song)
        return result

    @staticmethod
    def _energy_heuristic(song: dict) -> float:
        """粗粒度能量估计 - 基于 BPM + 歌曲名 heuristics"""
        energy = 50.0

        # BPM 代理能量
        bpm = song.get("bpm")
        if bpm is not None and bpm > 0:
            energy = bpm * 0.5  # 70 BPM → 35, 140 BPM → 70

        name = song.get("name", "").lower()

        # 高能量关键词
        high_energy_keywords = ["remix", "club", "bass", "drop", "hard", "bounce",
                                 "extended", "mix", "edit", "dance", "up", "party"]
        for kw in high_energy_keywords:
            if kw in name:
                energy += 8
                break  # 只加一次

        # 低能量关键词
        low_energy_keywords = ["acoustic", "piano", "sleep", "slow", "soft",
                               "calm", "quiet", "ambient", "chill", "ballad"]
        for kw in low_energy_keywords:
            if kw in name:
                energy -= 12
                break  # 只减一次

        return max(10, min(95, energy))

    async def _expand_candidates(
        self,
        candidates: list[dict],
        anchors: list[AnchorSong],
        target_count: int,
        max_extra_anchors: int = 3,
        limit_per_anchor: int = 20,
    ) -> list[dict]:
        """级联扩展：用已选候选中的 top 歌曲作为二级锚点，获取更多相似推荐"""
        if len(candidates) >= target_count:
            return candidates

        extra_anchor_ids = set()
        extra_anchors = []
        used_artists = set()

        for song in candidates:
            sid = str(song.get("id", ""))
            artist = song.get("artist", "").lower()
            if sid in {a.id for a in anchors}:
                continue
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
            "候选池不足 (%d < %d)，启用级联扩展，使用 %d 个二级锚点",
            len(candidates), target_count, len(extra_anchors),
        )

        all_new = list(candidates)
        for song in extra_anchors:
            logger.info("获取二级锚点 '%s' 的相似推荐...", song.get("name", "未知"))
            similar = await self.mcp.get_similar_songs(str(song["id"]), limit=limit_per_anchor)
            logger.info("二级锚点 '%s' 获得 %d 首相似推荐", song.get("name", "未知"), len(similar))
            all_new.extend(similar)

        unique = self._deduplicate(all_new)
        unique = self._remove_anchors(unique, anchors)
        logger.info("级联扩展后候选歌曲: %d 首", len(unique))
        return unique

    def _format_playlist_name(self, playlist_name: Optional[str], anchors: list, arrange_mode: str) -> str:
        """生成方案 E 格式的歌单名称"""
        mode_display = {
            "flat": "Flat",
            "warm-up": "Warm Up",
            "peak-mid": "Peak Mid",
            "rollercoaster": "Rollercoaster",
            "climax-end": "Climax End",
        }.get(arrange_mode, arrange_mode.title())

        if playlist_name:
            return f"🎧 DJ Curator · {playlist_name} · {mode_display}"

        artists = [a.artist for a in anchors if getattr(a, "artist", None)]
        if len(artists) >= 2:
            artist_str = f"{artists[0]} × {artists[1]}"
        elif len(artists) == 1:
            artist_str = artists[0]
        else:
            artist_str = "Mix"

        return f"🎧 DJ Curator · {mode_display} · {artist_str}"

    async def build_playlist(
        self,
        anchor_queries: list[str],
        playlist_name: Optional[str] = None,
        target_count: int = 20,
        diversity_ratio: float = 0.8,
        enable_expand: bool = True,
        arrange_mode: str = "flat",
    ) -> dict:
        """
        构建歌单的主流程（v2.0 - Transition-based selection）

        流程：锚点 → 多源采集 → 粗粒度能量估计 → 预过滤 → 贪心序列构建 → 创建歌单
        """
        total_start = time.time()
        if not anchor_queries:
            raise ValueError("至少需要提供一个锚点歌曲")

        # 1. 解析锚点
        t0 = time.time()
        logger.info("正在解析 %d 个锚点歌曲...", len(anchor_queries))
        anchors = await self.anchor_analyzer.resolve_multiple(anchor_queries, self.mcp)
        logger.info("[计时] 解析锚点: %.2fs", time.time() - t0)

        # 生成最终歌单名称（方案 E）
        final_name = self._format_playlist_name(playlist_name, anchors, arrange_mode)
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
                        anchor.name, anchor.bpm, anchor.key,
                    )
            # 锚点能量 = librosa 精分析 或 BPM 代理
            if anchor.bpm is not None:
                anchor_energy = anchor.bpm * 0.5
                # 尝试精分析
                try:
                    audio_info = await self.mcp.get_audio_url(anchor.id)
                    url = audio_info.get("url")
                    if url:
                        # 复用 AudioAnalyzer 的下载和分析
                        from dj_set_curator.arranger import EnergyAnalyzer
                        ea = EnergyAnalyzer(self.mcp)
                        precise = await ea.analyze_energy(anchor.id)
                        if precise is not None:
                            anchor_energy = precise
                except Exception:
                    pass
                # 动态添加 energy 属性
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
        logger.info("[计时] 多源采集: %.2fs", time.time() - t0)

        # 4. 去重 + 移除锚点本身 + 歌曲名去重
        t0 = time.time()
        unique_candidates = self._deduplicate(all_candidates)
        unique_candidates = self._remove_anchors(unique_candidates, anchors)
        unique_candidates = self._deduplicate_by_name(unique_candidates)
        logger.info("去重后候选歌曲: %d 首", len(unique_candidates))
        logger.info("[计时] 去重: %.2fs", time.time() - t0)

        if not unique_candidates:
            raise RuntimeError("未获取到任何候选歌曲，请检查锚点歌曲是否有效或 MCP Server 登录状态")

        # 5. 级联扩展（如启用且候选不足）
        if enable_expand and len(unique_candidates) < target_count:
            unique_candidates = await self._expand_candidates(
                unique_candidates, anchors, target_count
            )

        # 6. 粗粒度能量估计（所有候选）+ 音频分析（全量并发）
        t_analysis_start = time.time()

        # 先给所有候选加上能量估计
        for candidate in unique_candidates:
            candidate["energy"] = self._energy_heuristic(candidate)

        # 对所有缺失 BPM/Key 的候选做音频分析（不限制数量）
        to_analyze = []
        for candidate in unique_candidates:
            cid = str(candidate.get("id", ""))
            has_bpm = candidate.get("bpm") is not None
            has_key = candidate.get("key") is not None
            if not has_bpm or not has_key:
                to_analyze.append((candidate, cid, has_bpm, has_key))

        logger.info("音频分析: %d 首候选中 %d 首需要分析（全量）", len(unique_candidates), len(to_analyze))

        async def _analyze_one(item):
            candidate, cid, has_bpm, has_key = item
            try:
                analysis = await analyzer.analyze_song(cid)
                if analysis:
                    if not has_bpm:
                        candidate["bpm"] = analysis.get("bpm")
                    if not has_key:
                        candidate["key"] = analysis.get("camelot") or analysis.get("key")
                    if analysis.get("bpm"):
                        candidate["energy"] = analysis["bpm"] * 0.5
                    return True
            except Exception:
                pass
            return False

        # 并发分析（最多 10 个并发）
        analyzed_count = 0
        batch_size = 10
        total_batches = (len(to_analyze) + batch_size - 1) // batch_size
        for i in range(0, len(to_analyze), batch_size):
            batch = to_analyze[i:i+batch_size]
            batch_num = i // batch_size + 1
            results = await asyncio.gather(*[_analyze_one(item) for item in batch])
            analyzed_count += sum(1 for r in results if r)
            logger.info(
                "[进度] 音频分析 %d/%d 批完成 (%d/%d 首), 成功 %d 首",
                batch_num, total_batches, min(i + batch_size, len(to_analyze)), len(to_analyze), analyzed_count
            )

        logger.info("音频分析完成: %d/%d 首成功，耗时 %.1fs", analyzed_count, len(to_analyze), time.time() - t_analysis_start)

        # 7. 预过滤：用 SongFilter 过滤掉低分候选
        t0 = time.time()
        scored = self.filter.score_candidates(unique_candidates, anchors)
        min_score = self.filter.min_score
        filtered = [s for s in scored if s.score >= min_score]
        logger.info("预过滤: %d 首候选中 %d 首通过 (min_score=%s)", len(scored), len(filtered), min_score)
        logger.info("[计时] 预过滤: %.2fs", time.time() - t0)

        if not filtered:
            logger.warning("预过滤后无候选，放宽限制使用全部候选")
            filtered = scored

        # 提取过滤后的候选 dict
        filtered_candidates = [s.song for s in filtered]

        # 8. 贪心序列构建（核心改变）
        t0 = time.time()
        scorer = TransitionScorer(bpm_tolerance=self.filter.bpm_tolerance)
        selector = SequentialSelector(scorer, arrange_mode=arrange_mode)
        selected = selector.select(filtered_candidates, anchors, target_count)
        logger.info("[计时] 贪心序列构建: %.2fs", time.time() - t0)

        if not selected:
            raise RuntimeError("筛选后没有符合条件的歌曲")

        # 9. 对最终入选歌曲做精能量分析（可选，提升质量）
        for s in selected:
            sid = str(s.song.get("id", ""))
            try:
                from dj_set_curator.arranger import EnergyAnalyzer
                ea = EnergyAnalyzer(self.mcp)
                precise_energy = await ea.analyze_energy(sid)
                if precise_energy is not None:
                    s.song["energy"] = precise_energy
            except Exception:
                pass  # 保持 heuristics 能量

        # 10. 组装最终歌单：锚点歌曲 + 选中歌曲（去重，锚点放前面）
        anchor_ids = [a.id for a in anchors]
        selected_ids = [str(s.song["id"]) for s in selected]
        selected_ids = [sid for sid in selected_ids if sid not in anchor_ids]
        track_ids = anchor_ids + selected_ids

        # 11. 创建歌单并添加
        t0 = time.time()
        logger.info("正在创建歌单 '%s'...", final_name)
        playlist_id = await self.mcp.create_playlist(final_name)
        logger.info("歌单创建成功，ID: %s", playlist_id)

        await self.mcp.add_tracks_to_playlist(playlist_id, track_ids)
        logger.info("已添加 %d 首歌曲到歌单（含 %d 首锚点）", len(track_ids), len(anchor_ids))
        logger.info("[计时] 创建歌单: %.2fs", time.time() - t0)
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

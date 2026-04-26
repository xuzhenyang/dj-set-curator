"""能量曲线编排器 - DJ Set 能量曲线编排与 BPM 渐变"""

import logging
import os
import urllib.request
from typing import Optional

import librosa
import numpy as np

from dj_set_curator.audio_analyzer import get_audio_segments_dir
from dj_set_curator.models import ScoredSong

logger = logging.getLogger(__name__)


class EnergyAnalyzer:
    """能量分析器 - 分析歌曲响度/能量"""

    def __init__(self, mcp_client):
        self.mcp = mcp_client

    def _download_audio(self, song_id: str, url: str) -> str:
        """下载音频片段到缓存目录"""
        segments_dir = get_audio_segments_dir()
        local_path = os.path.join(segments_dir, f"{song_id}.mp3")
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path
        urllib.request.urlretrieve(url, local_path)
        return local_path

    async def analyze_energy(self, song_id: str) -> Optional[float]:
        """分析歌曲能量（0-100），返回 None 表示失败"""
        try:
            audio_info = await self.mcp.get_audio_url(song_id)
            url = audio_info.get("url")
            if not url:
                return None
            local_path = self._download_audio(song_id, url)
            y, sr = librosa.load(local_path, duration=30)
            rms = librosa.feature.rms(y=y)[0]
            energy = float(np.mean(rms))
            # 归一化到 0-100（基于经验阈值）
            normalized = min(100, max(0, energy * 5000))
            return round(normalized, 1)
        except Exception as e:
            logger.warning("能量分析失败: %s - %s", song_id, e)
            return None


class EnergyArcArranger:
    """能量曲线编排器"""

    # 预设编排模式: (名称, 描述, 能量分布函数)
    ARC_MODES = {
        "flat": {
            "name": "均匀",
            "desc": "能量均匀分布，适合背景播放",
            "curve": lambda n, i: 50.0,
        },
        "warm-up": {
            "name": "渐进",
            "desc": "从低到高再回落，适合派对开场",
            "curve": lambda n, i: 30 + 40 * (1 - abs(2 * i / max(n - 1, 1) - 1)),
        },
        "peak-mid": {
            "name": "中段高潮",
            "desc": "中段能量最高，适合演出核心时段",
            "curve": lambda n, i: 30 + 50 * max(0, 1 - abs(2 * i / max(n - 1, 1) - 1.2)),
        },
        "rollercoaster": {
            "name": "起伏",
            "desc": "高低交替，适合活跃氛围",
            "curve": lambda n, i: 40 + 30 * (1 if i % 2 == 0 else -1) * (1 - i / max(n, 1)),
        },
        "climax-end": {
            "name": "结尾高潮",
            "desc": "能量逐步攀升，适合收尾高潮",
            "curve": lambda n, i: 30 + 50 * (i / max(n - 1, 1)),
        },
    }

    def __init__(self, mcp_client, arc_mode: str = "flat"):
        self.mcp = mcp_client
        self.energy_analyzer = EnergyAnalyzer(mcp_client)
        self.arc_mode = arc_mode if arc_mode in self.ARC_MODES else "flat"

    async def _analyze_songs_energy(self, songs: list[ScoredSong]) -> dict[str, float]:
        """批量分析歌曲能量，返回 {song_id: energy_score}"""
        energies = {}
        for s in songs:
            sid = str(s.song.get("id", ""))
            if not sid:
                continue
            energy = await self.energy_analyzer.analyze_energy(sid)
            if energy is not None:
                energies[sid] = energy
            else:
                # 无法分析时给一个中等值
                energies[sid] = 50.0
        return energies

    def _get_target_curve(self, count: int) -> list[float]:
        """生成目标能量曲线"""
        mode = self.ARC_MODES[self.arc_mode]
        return [mode["curve"](count, i) for i in range(count)]

    def _arrange_by_curve(
        self,
        songs: list[ScoredSong],
        energies: dict[str, float],
        bpm_tolerance: float = 5.0,
    ) -> list[ScoredSong]:
        """
        根据目标能量曲线编排歌曲顺序

        算法：贪心匹配 - 每一步选择能量最接近目标值且 BPM/Key 兼容的歌曲
        """
        if not songs:
            return []

        count = len(songs)
        target_curve = self._get_target_curve(count)
        arranged = []
        remaining = list(songs)

        for i, target_energy in enumerate(target_curve):
            if not remaining:
                break

            # 计算每首剩余歌曲的匹配分
            best_song = None
            best_score = -9999

            for candidate in remaining:
                sid = str(candidate.song.get("id", ""))
                song_energy = energies.get(sid, 50.0)

                # 能量匹配分（越接近目标越好）
                energy_diff = abs(song_energy - target_energy)
                energy_score = 100 - energy_diff

                # BPM 过渡分（与上一首的 BPM 差越小越好）
                bpm_score = 100
                if arranged and candidate.bpm_diff is not None:
                    prev_bpm = arranged[-1].song.get("bpm")
                    if prev_bpm is not None:
                        curr_bpm = candidate.song.get("bpm")
                        if curr_bpm is not None:
                            bpm_diff = abs(curr_bpm - prev_bpm)
                            if bpm_diff <= bpm_tolerance:
                                bpm_score = 100 - (bpm_diff / bpm_tolerance) * 50
                            elif bpm_diff <= bpm_tolerance * 2:
                                bpm_score = 50 - ((bpm_diff - bpm_tolerance) / bpm_tolerance) * 50
                            else:
                                bpm_score = 0

                # 调性过渡分
                key_score = 100
                if arranged and candidate.key_distance is not None:
                    # 如果 key_distance 已知且较小，加分
                    key_score = 100 - (candidate.key_distance or 3) * 20

                # 综合分：能量匹配 50% + BPM 过渡 30% + 调性 20%
                total_score = energy_score * 0.5 + bpm_score * 0.3 + key_score * 0.2

                if total_score > best_score:
                    best_score = total_score
                    best_song = candidate

            if best_song:
                arranged.append(best_song)
                remaining.remove(best_song)

        # 如果还有剩余歌曲，追加到末尾
        arranged.extend(remaining)
        return arranged

    async def arrange(
        self,
        songs: list[ScoredSong],
        bpm_tolerance: float = 5.0,
    ) -> list[ScoredSong]:
        """
        编排歌曲顺序

        Args:
            songs: 已评分排序的歌曲列表
            bpm_tolerance: BPM 过渡容差

        Returns:
            按能量曲线编排后的歌曲列表
        """
        if self.arc_mode == "flat" or len(songs) <= 2:
            # 均匀模式或歌曲太少，保持原顺序
            return songs

        logger.info("能量曲线编排: 模式=%s, 歌曲数=%d", self.arc_mode, len(songs))
        energies = await self._analyze_songs_energy(songs)
        arranged = self._arrange_by_curve(songs, energies, bpm_tolerance)
        logger.info("能量曲线编排完成")
        return arranged

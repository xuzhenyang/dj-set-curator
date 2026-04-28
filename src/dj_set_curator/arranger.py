"""能量曲线编排器 - DJ Set 能量曲线编排与 BPM 渐变"""

import asyncio
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
    """能量分析器 - 多维度 DJ 能量分析

    综合四个维度计算"DJ 能量"分数：
    1. RMS 响度（整体响度）
    2. 节奏密度（鼓点/节拍密度）
    3. 低频能量占比（bass/sub-bass 占比）
    4. 频谱质心（亮度/尖锐度）
    """

    def __init__(self, mcp_client):
        self.mcp = mcp_client

    def _download_audio_sync(self, song_id: str, url: str) -> str:
        """同步下载音频片段到缓存目录（在 to_thread 中执行）"""
        segments_dir = get_audio_segments_dir()
        local_path = os.path.join(segments_dir, f"{song_id}.mp3")
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            return local_path
        urllib.request.urlretrieve(url, local_path)
        return local_path

    async def _download_audio(self, song_id: str, url: str) -> str:
        """异步下载音频片段（不阻塞事件循环）"""
        return await asyncio.to_thread(self._download_audio_sync, song_id, url)

    def _analyze_features_sync(self, local_path: str) -> Optional[dict]:
        """同步分析音频特征（在 to_thread 中执行）"""
        try:
            y, sr = librosa.load(local_path, duration=30)

            # 1. RMS 响度
            rms = librosa.feature.rms(y=y)[0]
            rms_score = min(100, max(0, float(np.mean(rms)) * 5000))

            # 2. 节奏密度（onset 检测）
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            # 计算 onset 的峰值密度（每秒多少个明显的节拍）
            onset_peaks = librosa.util.peak_pick(onset_env, pre_max=3, post_max=3, pre_avg=3, post_avg=5, delta=0.1, wait=5)
            duration = librosa.get_duration(y=y, sr=sr)
            density = len(onset_peaks) / max(duration, 1)
            # 归一化：0-5 peaks/sec → 0-100
            density_score = min(100, max(0, density * 20))

            # 3. 低频能量占比
            # 使用 STFT 分离频段
            stft = np.abs(librosa.stft(y))
            freqs = librosa.fft_frequencies(sr=sr)
            low_mask = freqs < 250  # < 250Hz = bass/sub-bass
            low_energy = np.sum(stft[low_mask, :])
            total_energy = np.sum(stft)
            low_ratio = low_energy / total_energy if total_energy > 0 else 0
            low_score = min(100, max(0, low_ratio * 300))

            # 4. 频谱质心（亮度）
            centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
            # 典型值：女声 ~500-1500Hz，电子音乐 ~3000-8000Hz
            mean_centroid = float(np.mean(centroid))
            # 归一化：500-8000Hz → 0-100
            brightness_score = min(100, max(0, (mean_centroid - 500) / 75))

            return {
                "rms": round(rms_score, 1),
                "density": round(density_score, 1),
                "low_freq": round(low_score, 1),
                "brightness": round(brightness_score, 1),
            }
        except Exception as e:
            logger.warning("特征分析失败: %s", e)
            return None

    async def analyze_energy(self, song_id: str) -> Optional[float]:
        """分析歌曲 DJ 能量（0-100），返回 None 表示失败"""
        try:
            audio_info = await self.mcp.get_audio_url(song_id)
            url = audio_info.get("url")
            if not url:
                return None
            local_path = await self._download_audio(song_id, url)
            features = await asyncio.to_thread(self._analyze_features_sync, local_path)
            if features is None:
                return None

            # 加权综合 DJ 能量
            # RMS 25% + 节奏密度 35% + 低频占比 25% + 亮度 15%
            dj_energy = (
                features["rms"] * 0.25
                + features["density"] * 0.35
                + features["low_freq"] * 0.25
                + features["brightness"] * 0.15
            )
            return round(dj_energy, 1)
        except Exception as e:
            logger.warning("能量分析失败: %s - %s", song_id, e)
            return None


class EnergyArcArranger:
    """能量曲线编排器 - 基于锚点能量分布的动态曲线"""

    # 曲线形状定义（相对偏移，-1 到 +1）
    # 实际能量 = anchor_mean + shape * anchor_std
    ARC_SHAPES = {
        "flat": {
            "name": "均匀",
            "desc": "能量均匀分布，适合背景播放",
            "shape": lambda n, i: 0.0,
        },
        "warm-up": {
            "name": "渐进",
            "desc": "从低到高再回落，适合派对开场",
            "shape": lambda n, i: -0.8 + 1.6 * (1 - abs(2 * i / max(n - 1, 1) - 1)),
        },
        "peak-mid": {
            "name": "中段高潮",
            "desc": "中段能量最高，适合演出核心时段",
            "shape": lambda n, i: -0.6 + 1.6 * max(0, 1 - abs(2 * i / max(n - 1, 1) - 1.2)),
        },
        "rollercoaster": {
            "name": "起伏",
            "desc": "高低交替，适合活跃氛围",
            "shape": lambda n, i: 0.3 * (1 if i % 2 == 0 else -1) * (1 - i / max(n, 1)),
        },
        "climax-end": {
            "name": "结尾高潮",
            "desc": "能量逐步攀升，适合收尾高潮",
            "shape": lambda n, i: -0.8 + 1.6 * (i / max(n - 1, 1)),
        },
    }

    def __init__(self, mcp_client, arc_mode: str = "flat"):
        self.mcp = mcp_client
        self.energy_analyzer = EnergyAnalyzer(mcp_client)
        self.arc_mode = arc_mode if arc_mode in self.ARC_SHAPES else "flat"

    async def _analyze_songs_energy(self, songs: list[ScoredSong]) -> dict[str, float]:
        """批量分析歌曲能量，返回 {song_id: energy_score}"""
        energies = {}
        for s in songs:
            sid = str(s.song.id)
            if not sid:
                continue
            energy = await self.energy_analyzer.analyze_energy(sid)
            if energy is not None:
                energies[sid] = energy
            else:
                energies[sid] = 50.0
        return energies

    def _get_target_curve(self, count: int, anchor_mean: float = 50.0, anchor_std: float = 15.0) -> list[float]:
        """生成目标能量曲线（基于锚点能量分布的动态曲线）"""
        mode = self.ARC_SHAPES[self.arc_mode]
        # 确保 std 不会太小（至少 10 分的变化空间）
        std = max(anchor_std, 10.0)
        return [
            max(10.0, min(95.0, anchor_mean + mode["shape"](count, i) * std))
            for i in range(count)
        ]

    def _arrange_by_curve(
        self,
        songs: list[ScoredSong],
        energies: dict[str, float],
        bpm_tolerance: float = 5.0,
        anchor_energies: Optional[list[float]] = None,
    ) -> list[ScoredSong]:
        """
        根据目标能量曲线编排歌曲顺序

        算法：贪心匹配 - 每一步选择能量最接近目标值且 BPM/Key 兼容的歌曲
        """
        if not songs:
            return []

        count = len(songs)
        # 动态曲线：基于锚点能量分布
        if anchor_energies and len(anchor_energies) > 0:
            anchor_mean = float(np.mean(anchor_energies))
            anchor_std = float(np.std(anchor_energies))
        else:
            # 无锚点数据时使用所有候选的能量分布
            vals = list(energies.values()) if energies else [50.0]
            anchor_mean = float(np.mean(vals))
            anchor_std = float(np.std(vals)) if len(vals) > 1 else 15.0

        target_curve = self._get_target_curve(count, anchor_mean, anchor_std)
        logger.info(
            "动态能量曲线: 均值=%.1f, 标准差=%.1f, 范围=[%.1f, %.1f]",
            anchor_mean, anchor_std, min(target_curve), max(target_curve),
        )
        arranged = []
        remaining = list(songs)

        for i, target_energy in enumerate(target_curve):
            if not remaining:
                break

            # 计算每首剩余歌曲的匹配分
            best_song = None
            best_score = -9999

            for candidate in remaining:
                sid = str(candidate.song.id)
                song_energy = energies.get(sid, 50.0)

                # 能量匹配分（越接近目标越好）
                energy_diff = abs(song_energy - target_energy)
                energy_score = 100 - energy_diff

                # BPM 过渡分（与上一首的 BPM 差越小越好）
                bpm_score = 100
                if arranged and candidate.bpm_diff is not None:
                    prev_bpm = arranged[-1].song.bpm
                    if prev_bpm is not None:
                        curr_bpm = candidate.song.bpm
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


class SongStructureAnalyzer:
    """歌曲结构分析器 - 简化版 intro/outro/breakdown 检测

    DJ 视角：
    - 干净的 intro/outro 是混音的关键
    - breakdown 的位置影响能量曲线编排
    """

    def __init__(self, mcp_client):
        self.mcp = mcp_client
        self.energy_analyzer = EnergyAnalyzer(mcp_client)

    def _analyze_structure_sync(self, local_path: str) -> Optional[dict]:
        """同步分析歌曲结构"""
        try:
            y, sr = librosa.load(local_path, duration=60)  # 分析前 60 秒
            duration = librosa.get_duration(y=y, sr=sr)

            # 1. 使用 onset 强度检测段落变化
            onset_env = librosa.onset.onset_strength(y=y, sr=sr)
            # 平滑处理
            onset_smooth = np.convolve(onset_env, np.ones(10)/10, mode='same')

            # 2. 分段（每 4 秒一段）
            hop = int(sr * 4)
            segments = []
            for i in range(0, len(y), hop):
                seg = y[i:i+hop]
                if len(seg) < hop // 2:
                    break
                rms = np.sqrt(np.mean(seg**2))
                segments.append(float(rms))

            if len(segments) < 4:
                return None

            # 3. 检测 intro（前几段能量明显低于平均）
            avg_rms = np.mean(segments)
            intro_segments = 0
            for seg_rms in segments[:8]:
                if seg_rms < avg_rms * 0.6:
                    intro_segments += 1
                else:
                    break
            intro_sec = min(intro_segments * 4, 20)

            # 4. 检测 breakdown（能量骤降然后回升）
            breakdowns = []
            for i in range(1, len(segments) - 1):
                if segments[i] < avg_rms * 0.5 and segments[i-1] > avg_rms * 0.7 and segments[i+1] > avg_rms * 0.7:
                    breakdowns.append(i * 4)

            return {
                "duration": round(duration, 1),
                "intro_sec": intro_sec,
                "has_breakdown": len(breakdowns) > 0,
                "breakdown_sec": breakdowns[:2],  # 最多报告 2 个
                "segment_rms": [round(r, 3) for r in segments[:15]],
            }
        except Exception as e:
            logger.warning("结构分析失败: %s", e)
            return None

    async def analyze(self, song_id: str) -> Optional[dict]:
        """异步分析歌曲结构"""
        try:
            audio_info = await self.mcp.get_audio_url(song_id)
            url = audio_info.get("url")
            if not url:
                return None
            local_path = await self.energy_analyzer._download_audio(song_id, url)
            return await asyncio.to_thread(self._analyze_structure_sync, local_path)
        except Exception as e:
            logger.warning("歌曲结构分析失败: %s - %s", song_id, e)
            return None

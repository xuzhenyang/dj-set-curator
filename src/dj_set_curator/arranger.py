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

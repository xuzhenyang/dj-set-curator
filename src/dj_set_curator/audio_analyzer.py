"""音频分析模块 - 使用 librosa 分析 BPM 和调性"""

import asyncio
import json
import logging
import os
import platform
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Optional

import librosa
import numpy as np

from dj_set_curator.models import Song

logger = logging.getLogger(__name__)


def get_cache_dir() -> str:
    """获取缓存根目录，支持环境变量覆盖"""
    # 优先使用环境变量
    if env_dir := os.environ.get("DJ_SET_CURATOR_CACHE_DIR"):
        cache_dir = Path(env_dir)
    else:
        system = platform.system()
        if system == "Darwin":  # macOS
            base = Path.home() / "Library" / "Caches"
        elif system == "Linux":
            base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        elif system == "Windows":
            base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        else:
            base = Path(tempfile.gettempdir())
        cache_dir = base / "dj-set-curator"

    cache_dir.mkdir(parents=True, exist_ok=True)
    return str(cache_dir)


def get_audio_segments_dir() -> str:
    """音频片段存放目录"""
    segments_dir = Path(get_cache_dir()) / "audio_segments"
    segments_dir.mkdir(parents=True, exist_ok=True)
    return str(segments_dir)


def get_analysis_cache_path() -> str:
    """分析结果缓存文件路径"""
    return os.path.join(get_cache_dir(), "analysis_cache.json")


def clean_audio_segments(max_age_days: int = 7):
    """清理过期的音频片段"""
    segments_dir = Path(get_audio_segments_dir())
    if not segments_dir.exists():
        return
    now = time.time()
    cleaned = 0
    for f in segments_dir.iterdir():
        if f.is_file() and (now - f.stat().st_mtime) > max_age_days * 86400:
            f.unlink()
            cleaned += 1
    if cleaned:
        logger.info("清理了 %d 个过期音频片段", cleaned)


class AudioAnalyzer:
    """音频分析器 - 下载音频片段并分析 BPM/调性"""

    def __init__(
        self,
        mcp_client,
        max_analysis_duration: float = 30.0,
        enable_cache: bool = True,
    ):
        self.mcp = mcp_client
        self.max_analysis_duration = max_analysis_duration
        self.enable_cache = enable_cache
        self._cache = self._load_cache()

    def _load_cache(self) -> dict:
        """加载分析结果缓存"""
        path = get_analysis_cache_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                logger.warning("加载分析缓存失败: %s", e)
        return {}

    def _save_cache(self):
        """保存分析结果缓存"""
        if not self.enable_cache:
            return
        path = get_analysis_cache_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(self._cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning("保存分析缓存失败: %s", e)

    def _get_cached(self, song_id: str) -> Optional[dict]:
        """从缓存读取分析结果"""
        if not self.enable_cache:
            return None
        entry = self._cache.get(str(song_id))
        if entry:
            # 缓存不过期（歌曲的 BPM/Key 不会变）
            return entry
        return None

    def _set_cached(self, song_id: str, result: dict):
        """写入分析结果缓存（内存中，不立即写磁盘）"""
        if not self.enable_cache:
            return
        self._cache[str(song_id)] = result

    def flush_cache(self):
        """将缓存持久化到磁盘"""
        self._save_cache()

    def _download_audio_sync(self, song_id: str, url: str) -> str:
        """同步下载音频片段到缓存目录（在 to_thread 中执行）"""
        segments_dir = get_audio_segments_dir()
        local_path = os.path.join(segments_dir, f"{song_id}.mp3")

        # 如果已存在且不是空文件，直接复用
        if os.path.exists(local_path) and os.path.getsize(local_path) > 0:
            logger.debug("复用已缓存音频片段: %s", local_path)
            return local_path

        logger.info("正在下载音频片段: %s -> %s", song_id, local_path)
        urllib.request.urlretrieve(url, local_path)
        logger.info("下载完成: %s (%d bytes)", song_id, os.path.getsize(local_path))
        return local_path

    async def _download_audio(self, song_id: str, url: str) -> str:
        """异步下载音频片段（不阻塞事件循环）"""
        return await asyncio.to_thread(self._download_audio_sync, song_id, url)

    def _analyze_file(self, audio_path: str) -> dict:
        """使用 librosa 分析本地音频文件"""
        logger.debug("开始分析音频: %s", audio_path)
        y, sr = librosa.load(audio_path, duration=self.max_analysis_duration)

        # BPM 分析
        tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
        if isinstance(tempo, np.ndarray):
            tempo = float(tempo.item())

        # 调性分析 (Krumhansl-Schmuckler)
        chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
        chroma_avg = np.mean(chroma, axis=1)

        keys = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
        major_profile = np.array(
            [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
        )
        minor_profile = np.array(
            [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
        )
        major_profile /= np.linalg.norm(major_profile)
        minor_profile /= np.linalg.norm(minor_profile)
        chroma_avg_norm = chroma_avg / (np.linalg.norm(chroma_avg) + 1e-10)

        best_key = None
        best_score = -1
        best_mode = None

        for mode_name, profile in [("major", major_profile), ("minor", minor_profile)]:
            for shift in range(12):
                shifted = np.roll(profile, shift)
                score = np.dot(chroma_avg_norm, shifted)
                if score > best_score:
                    best_score = score
                    best_key = keys[shift]
                    best_mode = mode_name

        camelot_map = {
            "C major": "8B",
            "G major": "9B",
            "D major": "10B",
            "A major": "11B",
            "E major": "12B",
            "B major": "1B",
            "F# major": "2B",
            "Db major": "3B",
            "Ab major": "4B",
            "Eb major": "5B",
            "Bb major": "6B",
            "F major": "7B",
            "A minor": "8A",
            "E minor": "9A",
            "B minor": "10A",
            "F# minor": "11A",
            "C# minor": "12A",
            "G# minor": "1A",
            "D# minor": "2A",
            "Bb minor": "3A",
            "F minor": "4A",
            "C minor": "5A",
            "G minor": "6A",
            "D minor": "7A",
        }

        full_key = f"{best_key} {best_mode}"
        camelot = camelot_map.get(full_key)

        result = {
            "bpm": round(tempo, 1),
            "key": full_key,
            "camelot": camelot,
            "confidence": round(float(best_score), 3),
        }
        logger.debug("分析结果: %s", result)
        return result

    async def analyze_song(self, song_id: str) -> Optional[dict]:
        """
        分析指定歌曲的 BPM 和调性

        Returns:
            {"bpm": float, "key": str, "camelot": str} or None
        """
        song_id = str(song_id)

        # 1. 检查缓存
        cached = self._get_cached(song_id)
        if cached:
            logger.info("使用缓存分析结果: %s (BPM=%s, Key=%s)", song_id, cached.get("bpm"), cached.get("key"))
            return cached

        # 2. 获取音频 URL
        try:
            audio_info = await self.mcp.get_audio_url(song_id)
        except Exception as e:
            logger.warning("获取音频 URL 失败: %s - %s", song_id, e)
            return None

        url = audio_info.get("url")
        if not url:
            logger.warning("歌曲 %s 无可用音频链接", song_id)
            return None

        # 3. 下载并分析
        try:
            local_path = await self._download_audio(song_id, url)
            result = await asyncio.to_thread(self._analyze_file, local_path)
            self._set_cached(song_id, result)
            logger.info(
                "音频分析完成: %s -> BPM=%s, Key=%s, Camelot=%s",
                song_id, result["bpm"], result["key"], result.get("camelot"),
            )
            return result
        except Exception as e:
            logger.warning("音频分析失败: %s - %s", song_id, e)
            return None


class BatchAudioAnalyzer:
    """批量音频分析器"""

    def __init__(self, audio_analyzer, status_callback=None):
        self.analyzer = audio_analyzer
        self._status = status_callback

    async def analyze_songs_batch(
        self,
        songs: list[Song],
        t_analysis_start: float,
        time_limit: float = 120.0,
    ) -> tuple[int, int]:
        """批量分析歌曲 BPM/Key，返回 (成功数, 跳过数)"""
        to_analyze = []
        for song in songs:
            cid = str(song.id)
            has_bpm = song.bpm is not None
            has_key = song.key is not None
            if not has_bpm or not has_key:
                to_analyze.append((song, cid, has_bpm, has_key))

        if not to_analyze:
            return 0, 0

        async def _analyze_one(item):
            song, cid, has_bpm, has_key = item
            try:
                analysis = await asyncio.wait_for(
                    self.analyzer.analyze_song(cid),
                    timeout=20.0,
                )
                if analysis:
                    if not has_bpm:
                        song.bpm = analysis.get("bpm")
                    if not has_key:
                        song.key = analysis.get("camelot") or analysis.get("key")
                    if analysis.get("bpm"):
                        song.energy = analysis["bpm"] * 0.5
                    return True
            except asyncio.TimeoutError:
                logger.warning("音频分析超时: %s", cid)
            except Exception:
                pass
            return False

        analyzed_count = 0
        skipped_count = 0
        batch_size = 10
        total_batches = (len(to_analyze) + batch_size - 1) // batch_size
        for i in range(0, len(to_analyze), batch_size):
            if time.time() - t_analysis_start > time_limit:
                skipped_count = len(to_analyze) - i
                logger.warning(
                    "[进度] 音频分析达到 %ds 上限，跳过剩余 %d 首",
                    int(time_limit), skipped_count,
                )
                break

            batch = to_analyze[i:i + batch_size]
            batch_num = i // batch_size + 1
            results = await asyncio.gather(*[_analyze_one(item) for item in batch])
            analyzed_count += sum(1 for r in results if r)
            if self._status:
                self._status(
                    stage="analysis",
                    progress=30 + int(40 * analyzed_count / max(len(to_analyze), 1)),
                    message=f"音频分析 ({analyzed_count}/{len(to_analyze)}首)",
                )
            logger.info(
                "[进度] 音频分析 %d/%d 批完成 (%d/%d 首), 成功 %d 首, 已用 %.1fs",
                batch_num,
                total_batches,
                min(i + batch_size, len(to_analyze)),
                len(to_analyze),
                analyzed_count,
                time.time() - t_analysis_start,
            )

        return analyzed_count, skipped_count

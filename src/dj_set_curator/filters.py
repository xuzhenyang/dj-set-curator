"""筛选排序引擎 - Camelot Wheel + BPM 评分"""

import logging
import re
from typing import Optional

from dj_set_curator.models import AnchorSong

logger = logging.getLogger(__name__)

# Camelot Wheel 调性映射表（标准音乐调性 → Camelot 编码）
CAMELOT_WHEEL = {
    # Major keys (B)
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
    # Minor keys (A)
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

# 反向映射：Camelot 编码 → 调性列表
CAMELOT_TO_KEYS: dict[str, list[str]] = {}
for key, camelot in CAMELOT_WHEEL.items():
    CAMELOT_TO_KEYS.setdefault(camelot, []).append(key)


from dj_set_curator.models import ScoredSong


class SongFilter:
    """歌曲筛选器 - 基于 BPM、调性、艺术家关联度评分"""

    def __init__(
        self,
        bpm_tolerance: float = 5.0,
        key_match_priority: bool = True,
        min_score: float = 30.0,
        # 可配置的权重
        bpm_weight: float = 0.25,
        key_weight: float = 0.30,
        artist_weight: float = 0.25,
        diversity_weight: float = 0.20,
    ):
        self.bpm_tolerance = bpm_tolerance
        self.key_match_priority = key_match_priority
        self.min_score = min_score
        self.bpm_weight = bpm_weight
        self.key_weight = key_weight
        self.artist_weight = artist_weight
        self.diversity_weight = diversity_weight

    def _extract_bpm(self, song: dict) -> Optional[float]:
        """从歌曲信息中提取 BPM（如有）"""
        # 网易云 API 通常不直接返回 BPM，但预留扩展
        for key in ["bpm", "BPM", "tempo"]:
            if key in song and song[key] is not None:
                try:
                    return float(song[key])
                except (ValueError, TypeError):
                    continue
        return None

    def _extract_key(self, song: dict) -> Optional[str]:
        """从歌曲信息中提取调性（如有）"""
        for key in ["key", "tonality", "camelot", "Key"]:
            if key in song and song[key] is not None:
                val = str(song[key]).strip()
                if val:
                    return val
        return None

    def _bpm_score(self, candidate_bpm: Optional[float], anchor_bpms: list[float]) -> float:
        """BPM 匹配度评分 (0-100)"""
        if candidate_bpm is None or not anchor_bpms:
            return 50.0  # 无数据时给中等分

        min_diff = min(abs(candidate_bpm - ab) for ab in anchor_bpms)

        if min_diff <= 1.0:
            return 100.0
        if min_diff <= self.bpm_tolerance:
            # 线性衰减
            return 100.0 - (min_diff / self.bpm_tolerance) * 50.0
        if min_diff <= self.bpm_tolerance * 2:
            return 50.0 - ((min_diff - self.bpm_tolerance) / self.bpm_tolerance) * 50.0
        return 0.0

    def _normalize_key(self, key: str) -> str:
        """标准化调性表示"""
        key = key.strip()
        # 如果是 Camelot 编码直接返回
        if re_match := re.match(r"^(\d{1,2})([ABab])$", key):
            num = int(re_match.group(1))
            letter = re_match.group(2).upper()
            return f"{num}{letter}"
        # 尝试从标准调性映射
        lookup = key.lower()
        for full_key, camelot in CAMELOT_WHEEL.items():
            if full_key.lower() == lookup:
                return camelot
        return key

    def _key_distance(self, key1: Optional[str], key2: Optional[str]) -> int:
        """
        计算两个调性的 Camelot Wheel 距离
        0 = 完全匹配 / A-B 互换兼容
        1 = 相邻兼容（±1 数字）
        2 = 较远但仍可混音（+/- 2 数字，或相对）
        3+ = 不推荐混音
        """
        if key1 is None or key2 is None:
            return 999  # 未知调性视为极不兼容

        c1 = self._normalize_key(key1)
        c2 = self._normalize_key(key2)

        if not re.match(r"^\d{1,2}[AB]$", c1) or not re.match(r"^\d{1,2}[AB]$", c2):
            # 无法解析，返回中等距离
            return 3

        if c1 == c2:
            return 0

        # A/B 互换兼容（同数字）
        num1, letter1 = int(c1[:-1]), c1[-1]
        num2, letter2 = int(c2[:-1]), c2[-1]

        if num1 == num2 and letter1 != letter2:
            return 0

        # 计算数字环距离（1-12 循环）
        diff = abs(num1 - num2)
        ring_diff = min(diff, 12 - diff)

        # 相同字母（同为 A 或同为 B）
        if letter1 == letter2:
            if ring_diff == 1:
                return 1
            if ring_diff == 2:
                return 2
            return 3

        # 不同字母且数字不同
        if ring_diff == 1:
            return 2  # 相邻但不 A/B 互换，稍远
        return 3

    def _key_score(self, candidate_key: Optional[str], anchor_keys: list[str]) -> float:
        """调性兼容度评分 (0-100)"""
        if candidate_key is None or not anchor_keys:
            return 50.0

        distances = [self._key_distance(candidate_key, ak) for ak in anchor_keys]
        min_dist = min(distances)

        if min_dist == 0:
            return 100.0
        if min_dist == 1:
            return 80.0
        if min_dist == 2:
            return 50.0
        return 10.0

    def _artist_score(self, candidate: dict, anchors: list[AnchorSong]) -> float:
        """艺术家关联度评分 (0-100)"""
        candidate_artists = candidate.get("artist", "").lower()
        for anchor in anchors:
            if anchor.artist.lower() in candidate_artists:
                return 100.0
            if candidate_artists in anchor.artist.lower():
                return 100.0
        # 无直接关联给基础分
        return 30.0

    def _diversity_score(self, candidate: dict, selected_so_far: list[ScoredSong]) -> float:
        """多样性评分 - 避免与已选歌曲过于重复 (0-100)"""
        if not selected_so_far:
            return 100.0

        candidate_name = candidate.get("name", "").lower()
        candidate_artist = candidate.get("artist", "").lower()

        # 检查是否已有同名或同艺术家
        for s in selected_so_far:
            if s.song.get("name", "").lower() == candidate_name:
                return 10.0
            if s.song.get("artist", "").lower() == candidate_artist:
                return 50.0

        return 100.0

    def score_candidates(
        self,
        candidates: list[dict],
        anchors: list[AnchorSong],
    ) -> list[ScoredSong]:
        """
        对候选歌曲进行综合评分并排序

        评分维度：
        1. BPM 接近度
        2. 调性兼容性（Camelot Wheel）
        3. 艺术家关联度
        4. 多样性

        动态权重：当某个维度数据缺失时，自动将该权重重新分配给其他可用维度
        """
        anchor_bpms = [a.bpm for a in anchors if a.bpm is not None]
        anchor_keys = [a.key for a in anchors if a.key is not None]
        has_bpm_data = len(anchor_bpms) > 0
        has_key_data = len(anchor_keys) > 0

        # 计算动态权重
        available_weights = {}
        if has_bpm_data:
            available_weights["bpm"] = self.bpm_weight
        if has_key_data:
            available_weights["key"] = self.key_weight
        available_weights["artist"] = self.artist_weight
        available_weights["diversity"] = self.diversity_weight

        total_weight = sum(available_weights.values())
        weights = {k: v / total_weight for k, v in available_weights.items()}

        # 方案 C：限制 Artist 权重上限为 40%，防止同艺术家垄断
        max_artist_ratio = 0.40
        if weights.get("artist", 0) > max_artist_ratio:
            excess = weights["artist"] - max_artist_ratio
            weights["artist"] = max_artist_ratio
            # 将超出部分平分给其他维度
            other_keys = [k for k in weights if k != "artist"]
            if other_keys:
                for k in other_keys:
                    weights[k] += excess / len(other_keys)

        logger.debug(
            "动态权重: BPM=%s%%, Key=%s%%, Artist=%s%%, Diversity=%s%%",
            round(weights.get("bpm", 0) * 100),
            round(weights.get("key", 0) * 100),
            round(weights["artist"] * 100),
            round(weights["diversity"] * 100),
        )

        scored = []
        for candidate in candidates:
            candidate_bpm = self._extract_bpm(candidate)
            candidate_key = self._extract_key(candidate)

            bpm_s = self._bpm_score(candidate_bpm, anchor_bpms) if has_bpm_data else 0
            key_s = self._key_score(candidate_key, anchor_keys) if has_key_data else 0
            artist_s = self._artist_score(candidate, anchors)

            # 多样性在排序时逐步计算，这里先给满分
            div_s = 100.0

            total = (
                bpm_s * weights.get("bpm", 0)
                + key_s * weights.get("key", 0)
                + artist_s * weights["artist"]
                + div_s * weights["diversity"]
            )

            reasons = []
            if candidate_bpm is not None and anchor_bpms:
                min_bpm_diff = min(abs(candidate_bpm - ab) for ab in anchor_bpms)
                reasons.append(f"BPM差{min_bpm_diff:.0f}")
            if candidate_key is not None and anchor_keys:
                min_key_dist = min(self._key_distance(candidate_key, ak) for ak in anchor_keys)
                if min_key_dist == 0:
                    reasons.append("调性匹配")
                elif min_key_dist == 1:
                    reasons.append("调性兼容")
            if artist_s >= 100:
                reasons.append("同艺术家")

            scored.append(
                ScoredSong(
                    song=candidate,
                    score=round(total, 1),
                    bpm_diff=candidate_bpm - anchor_bpms[0] if candidate_bpm and anchor_bpms else None,
                    key_distance=min(self._key_distance(candidate_key, ak) for ak in anchor_keys) if candidate_key and anchor_keys else None,
                    match_reasons=reasons,
                )
            )

        # 按分数降序排列
        scored.sort(key=lambda x: x.score, reverse=True)
        return scored

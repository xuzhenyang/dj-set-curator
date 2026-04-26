"""过渡评分引擎 - DJ-proven 的 pair-wise transition scoring"""

import logging
from typing import Optional

from dj_set_curator.filters import SongFilter
from dj_set_curator.models import AnchorSong, ScoredSong

logger = logging.getLogger(__name__)


class TransitionScorer:
    """计算相邻两首歌曲之间的过渡质量评分"""

    def __init__(self, bpm_tolerance: float = 5.0):
        self.bpm_tolerance = bpm_tolerance
        self.filter = SongFilter(bpm_tolerance=bpm_tolerance)

    # ───────────────────────────────────────────────
    # BPM 过渡评分
    # ───────────────────────────────────────────────
    def bpm_transition_score(
        self, curr_bpm: Optional[float], next_bpm: Optional[float]
    ) -> float:
        """
        BPM 过渡兼容性评分 (0-100)

        DJ 实践：
        - ±3% pitch 调整是标准操作 = 完美兼容
        - 半速/倍速混音很常见（70BPM ↔ 140BPM）= 高兼容
        - ±5 BPM 内可通过 pitch 微调 = 可接受
        - 超过 ±10 BPM 需要技巧 = 困难
        """
        if curr_bpm is None or next_bpm is None:
            return 50.0  # 无数据时给中等分

        # 1. 直接匹配 ±3%
        ratio = next_bpm / curr_bpm if curr_bpm > 0 else 1.0
        if 0.97 <= ratio <= 1.03:
            return 100.0

        # 2. 半速/倍速兼容（×2 或 ÷2，±5% 容差）
        half_double_ratio = next_bpm / (curr_bpm * 2) if curr_bpm > 0 else 1.0
        if 0.95 <= half_double_ratio <= 1.05:
            return 85.0
        half_double_ratio_inv = next_bpm / (curr_bpm / 2) if curr_bpm > 0 else 1.0
        if 0.95 <= half_double_ratio_inv <= 1.05:
            return 85.0

        # 3. ±5 BPM 内
        diff = abs(next_bpm - curr_bpm)
        if diff <= self.bpm_tolerance:
            return 100.0 - (diff / self.bpm_tolerance) * 40.0

        # 4. ±10 BPM 内
        if diff <= self.bpm_tolerance * 2:
            return 60.0 - ((diff - self.bpm_tolerance) / self.bpm_tolerance) * 40.0

        # 5. 超过 ±10 BPM
        if diff <= self.bpm_tolerance * 3:
            return 20.0 - ((diff - self.bpm_tolerance * 2) / self.bpm_tolerance) * 20.0

        return 0.0

    # ───────────────────────────────────────────────
    # Key 过渡评分（DJ-proven Camelot 规则）
    # ───────────────────────────────────────────────
    def key_transition_score(
        self, curr_key: Optional[str], next_key: Optional[str]
    ) -> float:
        """
        Key 过渡兼容性评分 (0-100)

        DJ 实践（Camelot Wheel）：
        - 同 key / 相对大小调 = 最完美（能量不变）
        - +1 顺时针 = 能量提升（DJ 常用 energy boost）
        - -1 逆时针 = 能量下降（smooth cool-down）
        - ±2 = 仍可混音，有轻微张力
        - ±7（纯五度）= 非常和谐的过渡
        - 其他 = 不推荐
        """
        if curr_key is None or next_key is None:
            return 50.0

        import re

        distance = self.filter._key_distance(curr_key, next_key)

        if distance == 0:
            # 区分完全同 key 和 relative minor/major
            c_norm = self.filter._normalize_key(curr_key)
            a_norm = self.filter._normalize_key(next_key)
            if c_norm == a_norm:
                return 100.0
            else:
                return 95.0  # relative minor/major（A↔B）

        # distance > 2 时统一给低分（避免误判不和谐的 key 关系）
        if distance > 2:
            return 10.0

        # distance == 1 或 2，计算方向
        c_norm = self.filter._normalize_key(curr_key)
        a_norm = self.filter._normalize_key(next_key)

        if not re.match(r"^\d{1,2}[AB]$", c_norm) or not re.match(r"^\d{1,2}[AB]$", a_norm):
            return 30.0  # 无法解析

        num1, letter1 = int(c_norm[:-1]), c_norm[-1]
        num2, letter2 = int(a_norm[:-1]), a_norm[-1]
        diff = (num2 - num1) % 12
        if diff > 6:
            diff -= 12  # 取最短路径方向

        if distance == 1:
            # ±1：DJ 最常用的过渡
            return 90.0 if diff > 0 else 85.0  # +1 比 -1 稍微更"积极"

        if distance == 2:
            return 70.0

        return 10.0

    # ───────────────────────────────────────────────
    # 能量过渡评分
    # ───────────────────────────────────────────────
    def energy_transition_score(
        self,
        curr_energy: Optional[float],
        next_energy: Optional[float],
        target_energy: Optional[float],
    ) -> float:
        """
        能量过渡评分 (0-100)

        同时考虑：
        1. 与目标能量的接近度（越接近越好）
        2. 与当前能量的突变惩罚（突变 >30 分扣分）
        """
        if next_energy is None:
            return 50.0

        # 1. 目标匹配分
        target_score = 100.0
        if target_energy is not None:
            target_diff = abs(next_energy - target_energy)
            target_score = max(0, 100.0 - target_diff * 2)

        # 2. 突变惩罚
        mutation_penalty = 0.0
        if curr_energy is not None:
            energy_jump = abs(next_energy - curr_energy)
            if energy_jump > 30:
                mutation_penalty = (energy_jump - 30) * 1.5

        return max(0, target_score - mutation_penalty)

    # ───────────────────────────────────────────────
    # Artist 过渡惩罚（防止同艺术家连续出现）
    # ───────────────────────────────────────────────
    def artist_transition_penalty(
        self, curr_artist: str, next_artist: str, consecutive_same: int = 0
    ) -> float:
        """
        同艺术家连续出现的惩罚分（从总分中扣除）

        consecutive_same: 已连续出现多少首同艺术家歌曲
        """
        if curr_artist.lower() != next_artist.lower():
            return 0.0

        # 同艺术家惩罚递增（更激进，连续>2首时大幅扣分）
        penalties = {0: 0, 1: 8, 2: 25, 3: 55, 4: 80}
        return penalties.get(consecutive_same, 85)

    # ───────────────────────────────────────────────
    # 综合过渡评分
    # ───────────────────────────────────────────────
    def score_transition(
        self,
        curr_song: dict,
        next_song: dict,
        target_energy: Optional[float] = None,
        consecutive_same_artist: int = 0,
    ) -> dict:
        """
        计算从 curr_song 到 next_song 的综合过渡评分

        Returns:
            {
                "total": float,           # 综合分 (0-100)
                "bpm": float,             # BPM 过渡分
                "key": float,             # Key 过渡分
                "energy": float,          # 能量过渡分
                "artist_penalty": float,  # 艺术家惩罚
            }
        """
        bpm_s = self.bpm_transition_score(
            curr_song.get("bpm"), next_song.get("bpm")
        )
        key_s = self.key_transition_score(
            curr_song.get("key"), next_song.get("key")
        )
        energy_s = self.energy_transition_score(
            curr_song.get("energy"), next_song.get("energy"), target_energy
        )

        artist_penalty = self.artist_transition_penalty(
            curr_song.get("artist", ""),
            next_song.get("artist", ""),
            consecutive_same_artist,
        )

        # 综合权重：BPM 30% + Key 25% + Energy 45%（提高能量权重，让曲线更明显）
        total = bpm_s * 0.30 + key_s * 0.25 + energy_s * 0.45 - artist_penalty
        total = max(0, min(100, total))

        return {
            "total": round(total, 1),
            "bpm": round(bpm_s, 1),
            "key": round(key_s, 1),
            "energy": round(energy_s, 1),
            "artist_penalty": artist_penalty,
        }


class SequentialSelector:
    """贪心序列构建器 - 从锚点开始逐步选择最佳下一首"""

    # 预设能量曲线（与 arranger.py 保持一致）
    ARC_CURVES = {
        "flat": lambda n, i: 50.0,
        "warm-up": lambda n, i: 30 + 40 * (1 - abs(2 * i / max(n - 1, 1) - 1)),
        "peak-mid": lambda n, i: 30 + 50 * max(0, 1 - abs(2 * i / max(n - 1, 1) - 1.2)),
        "rollercoaster": lambda n, i: 40 + 30 * (1 if i % 2 == 0 else -1) * (1 - i / max(n, 1)),
        "climax-end": lambda n, i: 30 + 50 * (i / max(n - 1, 1)),
    }

    def __init__(self, scorer: TransitionScorer, arrange_mode: str = "flat"):
        self.scorer = scorer
        self.arrange_mode = arrange_mode if arrange_mode in self.ARC_CURVES else "flat"

    def _get_target_energies(self, count: int) -> list[float]:
        """生成目标能量曲线"""
        curve_fn = self.ARC_CURVES[self.arrange_mode]
        return [curve_fn(count, i) for i in range(count)]

    def select(
        self,
        candidates: list[dict],
        anchors: list[AnchorSong],
        target_count: int,
    ) -> list[ScoredSong]:
        """
        贪心构建序列

        Args:
            candidates: 预过滤后的候选歌曲（含 bpm/key/energy）
            anchors: 锚点歌曲（作为序列起点）
            target_count: 目标推荐歌曲数量

        Returns:
            按播放顺序排列的 ScoredSong 列表
        """
        if not candidates:
            return []

        target_energies = self._get_target_energies(target_count)
        arranged = []

        # 将锚点转换为 dict 格式作为序列起点
        current_songs = []
        for a in anchors:
            current_songs.append({
                "id": a.id,
                "name": a.name,
                "artist": a.artist,
                "bpm": a.bpm,
                "key": a.key,
                "energy": getattr(a, "energy", None) or (a.bpm * 0.5 if a.bpm else 50.0),
            })

        remaining = list(candidates)
        used_ids = {a.id for a in anchors}

        # 记录 artist 连续出现次数
        artist_consecutive = {}
        for a in anchors:
            artist_consecutive[a.artist.lower()] = artist_consecutive.get(a.artist.lower(), 0) + 1

        for step in range(target_count):
            if not remaining:
                break

            target_energy = target_energies[step] if step < len(target_energies) else 50.0
            current = current_songs[-1] if current_songs else None

            best_candidate = None
            best_score = -9999
            best_details = None

            for cand in remaining:
                cid = str(cand.get("id", ""))
                if cid in used_ids:
                    continue

                # 确保候选有 energy 字段
                if "energy" not in cand or cand["energy"] is None:
                    cand["energy"] = cand.get("bpm", 100) * 0.5 if cand.get("bpm") else 50.0

                if current:
                    cand_artist = cand.get("artist", "").lower()
                    consec = artist_consecutive.get(cand_artist, 0)

                    details = self.scorer.score_transition(
                        current, cand, target_energy, consec
                    )
                    score = details["total"]
                else:
                    # 第一步没有 current，用目标能量接近度
                    energy = cand.get("energy", 50.0)
                    score = max(0, 100 - abs(energy - target_energy) * 2)
                    details = {"total": score, "bpm": 50, "key": 50, "energy": score, "artist_penalty": 0}

                if score > best_score:
                    best_score = score
                    best_candidate = cand
                    best_details = details

            if best_candidate:
                arranged.append(
                    ScoredSong(
                        song=best_candidate,
                        score=round(best_score, 1),
                        bpm_diff=best_candidate.get("bpm", 0) - (current.get("bpm", 0) if current else 0),
                        key_distance=None,
                        match_reasons=self._build_reasons(best_details),
                    )
                )
                used_ids.add(str(best_candidate.get("id", "")))
                remaining.remove(best_candidate)
                current_songs.append(best_candidate)

                # 更新 artist 连续计数
                artist = best_candidate.get("artist", "").lower()
                artist_consecutive[artist] = artist_consecutive.get(artist, 0) + 1
                # 重置其他 artist 的计数
                for k in list(artist_consecutive.keys()):
                    if k != artist:
                        artist_consecutive[k] = 0

        return arranged

    @staticmethod
    def _build_reasons(details: dict) -> list[str]:
        """根据过渡评分详情构建匹配原因"""
        reasons = []
        if details.get("bpm", 0) >= 85:
            reasons.append("BPM兼容")
        elif details.get("bpm", 0) >= 60:
            reasons.append("BPM可接")

        if details.get("key", 0) >= 85:
            reasons.append("调性完美")
        elif details.get("key", 0) >= 70:
            reasons.append("调性和谐")
        elif details.get("key", 0) >= 50:
            reasons.append("调性可混")

        if details.get("energy", 0) >= 80:
            reasons.append("能量匹配")

        if details.get("artist_penalty", 0) == 0:
            pass  # 不同 artist，不特别标注

        return reasons if reasons else ["过渡可用"]

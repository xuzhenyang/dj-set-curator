"""能量启发式估计模块"""

from dj_set_curator.models import Song


def estimate_energy(song: Song) -> float:
    """粗粒度能量估计 - 基于 BPM + 歌曲名 heuristics"""
    energy = 50.0

    # BPM 代理能量
    bpm = song.bpm
    if bpm is not None and bpm > 0:
        energy = bpm * 0.5  # 70 BPM -> 35, 140 BPM -> 70

    name = song.name.lower()

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

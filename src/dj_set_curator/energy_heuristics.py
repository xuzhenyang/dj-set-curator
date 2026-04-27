"""能量启发式估计 - 基于 BPM + 歌曲名关键词"""


def estimate(song: dict) -> float:
    """粗粒度能量估计"""
    energy = 50.0

    # BPM 代理能量
    bpm = song.get("bpm")
    if bpm is not None and bpm > 0:
        energy = bpm * 0.5

    name = song.get("name", "").lower()

    # 高能量关键词
    high_energy = ["remix", "club", "bass", "drop", "hard", "bounce",
                   "extended", "mix", "edit", "dance", "up", "party"]
    for kw in high_energy:
        if kw in name:
            energy += 8
            break

    # 低能量关键词
    low_energy = ["acoustic", "piano", "sleep", "slow", "soft",
                  "calm", "quiet", "ambient", "chill", "ballad"]
    for kw in low_energy:
        if kw in name:
            energy -= 12
            break

    return max(10, min(95, energy))

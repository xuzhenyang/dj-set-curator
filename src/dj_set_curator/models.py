"""基础数据模型"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AnchorSong:
    """锚点歌曲标准化数据结构"""

    id: str
    name: str
    artist: str
    bpm: Optional[float] = None
    key: Optional[str] = None
    energy: Optional[float] = None
    genre: Optional[str] = None

    def __repr__(self) -> str:
        return f"AnchorSong({self.name} - {self.artist}, id={self.id})"


@dataclass
class ScoredSong:
    """带评分的候选歌曲"""

    song: dict
    score: float
    bpm_diff: Optional[float] = None
    key_distance: Optional[int] = None
    match_reasons: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"ScoredSong({self.song.get('name')} - score={self.score:.1f})"

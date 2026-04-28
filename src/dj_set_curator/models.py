"""基础数据模型"""

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GenreInfo:
    """曲风信息"""
    tags: list[str] = field(default_factory=list)
    source: str = ""  # "builtin", "lastfm", "keywords", "unknown"


@dataclass
class Song:
    """候选歌曲标准化数据结构"""

    id: str
    name: str
    artist: str
    bpm: Optional[float] = None
    key: Optional[str] = None
    energy: Optional[float] = None
    genre_tags: list[str] = field(default_factory=list)
    structure: Optional[dict] = None  # intro_sec, breakdown_sec, outro_sec 等

    @classmethod
    def from_dict(cls, data: dict) -> "Song":
        """从 API dict 创建 Song"""
        return cls(
            id=str(data.get("id", "")),
            name=data.get("name", "未知"),
            artist=data.get("artist", "未知"),
            bpm=data.get("bpm"),
            key=data.get("key"),
            energy=data.get("energy"),
            genre_tags=data.get("genre_tags", []),
        )

    def to_dict(self) -> dict:
        """转为 dict（兼容旧代码过渡）"""
        return {
            "id": self.id,
            "name": self.name,
            "artist": self.artist,
            "bpm": self.bpm,
            "key": self.key,
            "energy": self.energy,
            "genre_tags": self.genre_tags,
        }

    def __repr__(self) -> str:
        return f"Song({self.name} - {self.artist}, id={self.id})"


@dataclass
class AnchorSong(Song):
    """锚点歌曲 - 继承 Song，语义上表示用户指定的起点歌曲"""

    def __repr__(self) -> str:
        return f"AnchorSong({self.name} - {self.artist}, id={self.id})"


@dataclass
class ScoredSong:
    """带评分的候选歌曲"""

    song: Song
    score: float
    bpm_diff: Optional[float] = None
    key_distance: Optional[int] = None
    match_reasons: list[str] = field(default_factory=list)

    def __repr__(self) -> str:
        return f"ScoredSong({self.song.name} - score={self.score:.1f})"

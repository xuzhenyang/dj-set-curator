"""去重模块 - 按 ID、名称和锚点过滤歌曲"""

from dj_set_curator.models import AnchorSong, Song


class Deduplicator:
    """歌曲去重器"""

    @staticmethod
    def by_id(songs: list[Song]) -> list[Song]:
        """按 song id 去重，保留第一次出现的顺序"""
        seen = set()
        result = []
        for song in songs:
            sid = str(song.id)
            if sid and sid not in seen:
                seen.add(sid)
                result.append(song)
        return result

    @staticmethod
    def by_name(songs: list[Song]) -> list[Song]:
        """按歌曲名+艺术家去重（忽略 ID，同名同 artist 视为重复）"""
        seen = set()
        result = []
        for song in songs:
            name = song.name.lower().strip()
            artist = song.artist.lower().strip()
            key = f"{name}::{artist}"
            if key and key not in seen:
                seen.add(key)
                result.append(song)
        return result

    @staticmethod
    def remove_anchors(songs: list[Song], anchors: list[AnchorSong]) -> list[Song]:
        """从候选列表中移除锚点歌曲本身"""
        anchor_ids = {a.id for a in anchors}
        return [s for s in songs if str(s.id) not in anchor_ids]

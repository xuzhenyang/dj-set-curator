"""歌单命名模块"""

from typing import Optional

from dj_set_curator.models import AnchorSong


def format_playlist_name(playlist_name: Optional[str], anchors: list[AnchorSong], arrange_mode: str) -> str:
    """生成方案 E 格式的歌单名称（网易云 API 不支持 Emoji）"""
    mode_display = {
        "flat": "Flat",
        "warm-up": "Warm Up",
        "peak-mid": "Peak Mid",
        "rollercoaster": "Rollercoaster",
        "climax-end": "Climax End",
    }.get(arrange_mode, arrange_mode.title())

    if playlist_name:
        return f"[DJ Curator] {playlist_name} | {mode_display}"

    artists = [a.artist for a in anchors if getattr(a, "artist", None)]
    if len(artists) >= 2:
        artist_str = f"{artists[0]} x {artists[1]}"
    elif len(artists) == 1:
        artist_str = artists[0]
    else:
        artist_str = "Mix"

    return f"[DJ Curator] {mode_display} | {artist_str}"

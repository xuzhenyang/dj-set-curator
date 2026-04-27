"""锚点歌曲分析模块"""

import re
from dataclasses import dataclass
from typing import Optional

from dj_set_curator.mcp_client import CloudMusicMCPClient
from dj_set_curator.models import AnchorSong, Song


class AnchorAnalyzer:
    """锚点歌曲分析器"""

    @staticmethod
    def _is_song_id(query: str) -> bool:
        """判断 query 是否为纯数字的歌曲 ID"""
        return query.strip().isdigit()

    @staticmethod
    def _parse_artist_song(query: str) -> tuple[str, str]:
        """解析 'Artist - Song Name' 格式"""
        if " - " in query:
            parts = query.split(" - ", 1)
            return parts[0].strip(), parts[1].strip()
        # 尝试其他分隔符
        for sep in [" – ", "—", "-"]:
            if sep in query:
                parts = query.split(sep, 1)
                return parts[0].strip(), parts[1].strip()
        return "", query.strip()

    async def resolve_anchor(
        self, query: str, mcp_client: CloudMusicMCPClient
    ) -> AnchorSong:
        """
        解析用户输入的锚点歌曲

        query 可以是：
        - 网易云歌曲 ID（纯数字）
        - "Artist - Song Name" 格式
        - 歌曲名（模糊搜索取第一个结果）
        """
        query = query.strip()
        if not query:
            raise ValueError("锚点歌曲查询不能为空")

        # 1. 纯数字 ID
        if self._is_song_id(query):
            # 先尝试直接获取歌曲详情
            detail = await mcp_client.get_song_detail(query)
            if detail and detail.get("id"):
                return AnchorSong(
                    id=str(detail["id"]),
                    name=detail.get("name", "未知"),
                    artist=detail.get("artist", "未知"),
                )
            # 降级：通过搜索确认
            songs = await mcp_client.search_song(query)
            for song in songs:
                if str(song.id) == query:
                    return AnchorSong(
                        id=str(song.id),
                        name=song.name,
                        artist=song.artist,
                    )
            # 全部失败，直接信任用户输入的 ID
            return AnchorSong(
                id=query,
                name=f"ID:{query}",
                artist="未知",
            )

        # 2. "Artist - Song" 格式 或 纯歌曲名
        songs = await mcp_client.search_song(query)
        if not songs:
            raise ValueError(f"搜索 '{query}' 未找到结果")

        # 如果有 Artist - Song 格式，尝试匹配最佳结果
        artist_hint, song_hint = self._parse_artist_song(query)
        best_match = songs[0]

        if artist_hint:
            # 尝试找到艺术家匹配度更高的结果
            for song in songs:
                if artist_hint.lower() in song.artist.lower():
                    best_match = song
                    break

        return AnchorSong(
            id=str(best_match.id),
            name=best_match.name,
            artist=best_match.artist,
        )

    async def resolve_multiple(
        self, queries: list[str], mcp_client: CloudMusicMCPClient
    ) -> list[AnchorSong]:
        """批量解析多个锚点"""
        anchors = []
        for query in queries:
            anchor = await self.resolve_anchor(query, mcp_client)
            anchors.append(anchor)
        return anchors

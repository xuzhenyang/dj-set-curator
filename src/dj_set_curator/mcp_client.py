"""MCP Client 封装 - 与 cloud-music-mcp-extended 交互"""

import asyncio
import json
import logging
from typing import Any, Optional

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from dj_set_curator.models import Song

logger = logging.getLogger(__name__)


class CloudMusicMCPClient:
    """封装 cloud-music-mcp-extended 的 MCP 调用"""

    def __init__(
        self,
        server_command: str = "cloud-music-mcp",
        server_args: Optional[list[str]] = None,
    ):
        self.server_command = server_command
        self.server_args = server_args or []
        self.session: Optional[ClientSession] = None
        self._stdio_context = None
        self._client_context = None

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.cleanup()

    async def connect(self):
        """通过 stdio 启动 MCP Server 并建立会话"""
        server_params = StdioServerParameters(
            command=self.server_command,
            args=self.server_args,
            env=None,
        )
        self._client_context = stdio_client(server_params)
        read, write = await self._client_context.__aenter__()

        self._stdio_context = ClientSession(read, write)
        self.session = await self._stdio_context.__aenter__()
        await self.session.initialize()
        logger.info("MCP Client connected to %s", self.server_command)

    async def cleanup(self):
        """清理资源，关闭会话"""
        if self._stdio_context:
            await self._stdio_context.__aexit__(None, None, None)
            self._stdio_context = None
        if self._client_context:
            await self._client_context.__aexit__(None, None, None)
            self._client_context = None
        self.session = None
        logger.info("MCP Client disconnected")

    def _parse_result(self, result: Any) -> Any:
        """解析 MCP tool 返回的结果"""
        if hasattr(result, "content") and result.content:
            # 取第一个 content 的 text
            text = result.content[0].text
            # 尝试解析为 JSON
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return result

    async def _call_tool(self, tool_name: str, arguments: dict, max_retries: int = 2) -> Any:
        """底层工具调用，带重试和错误处理"""
        if not self.session:
            raise RuntimeError("MCP Client 未连接，请先调用 connect()")

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                logger.debug("Calling tool %s with args %s (attempt %d/%d)", tool_name, arguments, attempt + 1, max_retries + 1)
                result = await self.session.call_tool(tool_name, arguments=arguments)
                parsed = self._parse_result(result)

                # 检查是否返回错误文本
                if isinstance(parsed, str):
                    if "未登录" in parsed:
                        logger.warning("Tool %s returned '未登录': %s", tool_name, parsed)
                        return parsed
                    if "失败" in parsed or "错误" in parsed:
                        logger.warning("Tool %s returned error-like response: %s", tool_name, parsed)

                return parsed

            except Exception as e:
                logger.warning("Tool %s error (attempt %d/%d): %s", tool_name, attempt + 1, max_retries + 1, e)
                last_error = str(e)

            if attempt < max_retries:
                wait_time = 2 ** attempt
                logger.info("Retrying %s in %ds...", tool_name, wait_time)
                await asyncio.sleep(wait_time)

        logger.error("Tool %s failed after %d attempts: %s", tool_name, max_retries + 1, last_error)
        raise RuntimeError(f"{tool_name} 调用失败: {last_error}")

    async def check_status(self) -> dict:
        """检查登录状态"""
        result = await self._call_tool("cloud_music_status", {})
        if isinstance(result, str):
            logged_in = "已登录" in result
            return {"logged_in": logged_in, "message": result}
        return {"logged_in": False, "message": str(result)}

    async def search_song(self, keyword: str) -> list[Song]:
        """搜索歌曲，返回 Song 列表"""
        result = await self._call_tool("cloud_music_search", {"keyword": keyword})
        if isinstance(result, list):
            return [Song.from_dict(d) for d in result]
        if isinstance(result, str):
            # 可能是错误信息
            raise RuntimeError(f"搜索失败: {result}")
        return []

    async def get_similar_songs(self, song_id: str, limit: int = 20) -> list[Song]:
        """获取相似歌曲列表"""
        result = await self._call_tool(
            "cloud_music_get_similar_songs",
            {"song_id": str(song_id), "limit": limit},
        )
        # MCP server 返回格式化文本，需要解析
        if isinstance(result, str):
            return self._parse_similar_songs_text(result)
        if isinstance(result, list):
            return [Song.from_dict(d) for d in result]
        if isinstance(result, dict) and "songs" in result:
            return [Song.from_dict(d) for d in result["songs"]]
        return []

    @staticmethod
    def _parse_similar_songs_text(text: str) -> list[Song]:
        """解析相似推荐返回的格式化文本"""
        songs = []
        for line in text.split("\n"):
            line = line.strip()
            if not line or line.startswith("🔍"):
                continue
            # 格式: "1. Song Name - Artist (ID: 12345)"
            # 移除序号前缀
            if ". " in line:
                line = line.split(". ", 1)[1]
            if " (ID: " in line and line.endswith(")"):
                meta_part = line.rsplit(" (ID: ", 1)
                name_artist = meta_part[0]
                song_id = meta_part[1][:-1]  # 去掉尾部 )
                if " - " in name_artist:
                    name, artist = name_artist.split(" - ", 1)
                else:
                    name = name_artist
                    artist = "未知"
                songs.append(Song.from_dict({"id": song_id, "name": name.strip(), "artist": artist.strip()}))
        return songs

    async def create_playlist(self, name: str, privacy: bool = False) -> str:
        """创建歌单，返回 playlist_id"""
        result = await self._call_tool(
            "cloud_music_create_playlist",
            {"name": name, "privacy": privacy},
        )
        if isinstance(result, str):
            if "ID:" in result:
                # 从 "✅ 歌单创建成功: 'name' (ID: 12345)" 中提取 ID
                start = result.find("ID: ") + 4
                end = result.find(")", start)
                return result[start:end]
            if "失败" in result or "错误" in result:
                raise RuntimeError(f"创建歌单失败: {result}")
            return result
        if isinstance(result, dict):
            if result.get("success"):
                return str(result.get("playlist_id", ""))
            raise RuntimeError(f"创建歌单失败: {result.get('error', '未知错误')}")
        return str(result)

    async def get_song_detail(self, song_id: str) -> dict:
        """获取歌曲详情，返回 {id, name, artist, album}"""
        result = await self._call_tool(
            "cloud_music_get_song_detail",
            {"song_id": str(song_id)},
        )
        if isinstance(result, dict) and "id" in result:
            return result
        if isinstance(result, str) and ("失败" in result or "错误" in result):
            raise RuntimeError(f"获取歌曲详情失败: {result}")
        return {}

    async def get_artist_tracks(self, artist_id: str, limit: int = 30) -> list[Song]:
        """获取艺术家热门歌曲列表"""
        result = await self._call_tool(
            "cloud_music_get_artist_tracks",
            {"artist_id": str(artist_id), "limit": limit},
        )
        if isinstance(result, list):
            return [Song.from_dict(d) for d in result]
        if isinstance(result, str) and ("失败" in result or "错误" in result):
            raise RuntimeError(f"获取艺术家歌曲失败: {result}")
        return []

    async def get_album_songs(self, album_id: str) -> list[Song]:
        """获取专辑歌曲列表"""
        result = await self._call_tool(
            "cloud_music_get_album_songs",
            {"album_id": str(album_id)},
        )
        if isinstance(result, list):
            return [Song.from_dict(d) for d in result]
        if isinstance(result, str) and ("失败" in result or "错误" in result):
            raise RuntimeError(f"获取专辑歌曲失败: {result}")
        return []

    async def get_similar_artists(self, artist_id: str) -> list[dict]:
        """获取相似艺人列表 [{id, name}]"""
        result = await self._call_tool(
            "cloud_music_get_similar_artists",
            {"artist_id": str(artist_id)},
        )
        if isinstance(result, list):
            return result
        if isinstance(result, str) and ("失败" in result or "错误" in result):
            raise RuntimeError(f"获取相似艺人失败: {result}")
        return []

    async def get_daily_recommendations(self) -> list[Song]:
        """获取每日推荐歌曲列表"""
        # 每日推荐目前没有直接 MCP 工具，通过搜索热门歌曲模拟
        result = await self._call_tool("cloud_music_search", {"keyword": "每日推荐"})
        if isinstance(result, list):
            return [Song.from_dict(d) for d in result]
        return []

    async def add_tracks_to_playlist(self, playlist_id: str, track_ids: list[str]):
        """批量添加歌曲到歌单"""
        result = await self._call_tool(
            "cloud_music_add_tracks",
            {"playlist_id": str(playlist_id), "track_ids": track_ids},
        )
        if isinstance(result, str) and ("失败" in result or "错误" in result):
            raise RuntimeError(f"添加歌曲失败: {result}")
        return result

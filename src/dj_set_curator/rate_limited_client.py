"""Rate-limited MCP Client 代理

把风控间隔控制在 dj-curator 内部，不污染 mcp_client.py。

双层锁策略：
- 全局锁 (0.5s)：所有 API 调用之间间隔，避免服务端整体过载
- get_audio_url 专用锁 (2.0s)：该接口配额最低，需额外保护
"""

import asyncio
import inspect
import time
from typing import Any


class RateLimitedMCPClient:
    """MCP Client 代理：双层风控间隔

    用法：
        mcp_rl = RateLimitedMCPClient(real_mcp)
        # 所有调用自动走全局 0.5s 间隔
        # get_audio_url 额外走 2.0s 间隔
    """

    # 不需要间隔的方法白名单
    _SKIP_METHODS = frozenset({
        "__aenter__", "__aexit__", "connect", "cleanup",
        "_parse_result", "_parse_similar_songs_text",
    })

    def __init__(
        self,
        real_client: Any,
        global_interval: float = 0.5,
        audio_interval: float = 2.0,
    ):
        self._client = real_client
        self._global_interval = global_interval
        self._audio_interval = audio_interval
        self._global_lock = asyncio.Lock()
        self._audio_lock = asyncio.Lock()
        self._last_global_time = 0.0
        self._last_audio_time = 0.0

    def __getattr__(self, name: str) -> Any:
        """拦截所有属性访问，只对 async 方法加间隔"""
        if name.startswith("_") or name in self._SKIP_METHODS:
            return getattr(self._client, name)

        real_attr = getattr(self._client, name)
        if not inspect.iscoroutinefunction(real_attr):
            return real_attr

        # 判断是否是 get_audio_url 类的高成本接口
        is_audio_call = name in ("get_audio_url",)

        async def _wrapped(*args, **kwargs):
            # 1. 先走全局锁
            async with self._global_lock:
                elapsed = time.time() - self._last_global_time
                if elapsed < self._global_interval and self._last_global_time > 0:
                    await asyncio.sleep(self._global_interval - elapsed)

                # 2. 如果是 audio 调用，再走 audio 专用锁
                if is_audio_call:
                    async with self._audio_lock:
                        audio_elapsed = time.time() - self._last_audio_time
                        if audio_elapsed < self._audio_interval and self._last_audio_time > 0:
                            await asyncio.sleep(self._audio_interval - audio_elapsed)

                        try:
                            result = await real_attr(*args, **kwargs)
                            self._last_audio_time = time.time()
                            self._last_global_time = time.time()
                            return result
                        except Exception:
                            # 异常时也要更新时间戳，避免失败请求密集冲击
                            self._last_audio_time = time.time()
                            self._last_global_time = time.time()
                            raise
                else:
                    try:
                        result = await real_attr(*args, **kwargs)
                        self._last_global_time = time.time()
                        return result
                    except Exception:
                        self._last_global_time = time.time()
                        raise

        return _wrapped

    async def __aenter__(self):
        await self._client.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        return await self._client.__aexit__(exc_type, exc_val, exc_tb)

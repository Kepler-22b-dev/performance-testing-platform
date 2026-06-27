"""WebSocket 连接管理模块。

提供 WebSocket 连接的生命周期管理和消息推送功能，
将测试进度和结果消息实时推送到所有已连接的前端客户端。
"""

import sys
import os
import json
import asyncio
import redis.asyncio as aioredis
from typing import Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB,
)


class ConnectionManager:
    """WebSocket 连接管理器。

    管理所有活跃的 WebSocket 连接，订阅 Redis 频道并将
    测试进度和结果消息广播给所有客户端。
    """
    def __init__(self):
        self.active_connections: Set = set()
        self._redis: aioredis.Redis = None
        self._listener_task = None

    async def connect(self):
        """初始化 Redis 连接并启动后台监听任务。"""
        self._redis = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._listener_task = asyncio.create_task(self._listen_redis())

    async def disconnect(self):
        """关闭 Redis 连接并取消后台监听任务。"""
        if self._listener_task:
            self._listener_task.cancel()
        if self._redis:
            await self._redis.close()

    def add_connection(self, websocket):
        """将新的 WebSocket 连接添加到活跃连接集合。"""
        self.active_connections.add(websocket)

    def remove_connection(self, websocket):
        """将 WebSocket 连接从活跃连接集合中移除。"""
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        """向所有活跃连接广播消息，自动移除已断开的连接。"""
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        self.active_connections -= disconnected

    async def start(self):
        """启动 WebSocket 管理器。"""

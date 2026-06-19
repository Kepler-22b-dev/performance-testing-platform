import sys
import os
import json
import asyncio
import redis.asyncio as aioredis
from typing import Set

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    REDIS_CHANNEL_PROGRESS, REDIS_CHANNEL_RESULT,
)


class ConnectionManager:
    def __init__(self):
        self.active_connections: Set = set()
        self._redis: aioredis.Redis = None
        self._listener_task = None

    async def connect(self):
        self._redis = aioredis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._listener_task = asyncio.create_task(self._listen_redis())

    async def disconnect(self):
        if self._listener_task:
            self._listener_task.cancel()
        if self._redis:
            await self._redis.close()

    def add_connection(self, websocket):
        self.active_connections.add(websocket)

    def remove_connection(self, websocket):
        self.active_connections.discard(websocket)

    async def broadcast(self, message: dict):
        disconnected = set()
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception:
                disconnected.add(connection)
        self.active_connections -= disconnected

    async def _listen_redis(self):
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(
            REDIS_CHANNEL_PROGRESS,
            REDIS_CHANNEL_RESULT,
        )

        try:
            async for message in pubsub.listen():
                if message["type"] == "message":
                    channel = message["channel"]
                    data = message["data"]

                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError:
                        continue

                    msg = {
                        "channel": channel,
                        "data": payload,
                    }

                    await self.broadcast(msg)
        except asyncio.CancelledError:
            await pubsub.unsubscribe()

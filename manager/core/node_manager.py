"""节点管理器模块。

负责管理 JMeter 压测节点（Agent）的注册、心跳监听和状态维护。
通过 Redis Pub/Sub 接收 Agent 心跳信息，自动清理超时失联的节点。
"""

import sys
import os
import time
import json
import redis
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    REDIS_HOST, REDIS_PORT, REDIS_DB,
    REDIS_CHANNEL_HEARTBEAT, AGENT_HEARTBEAT_INTERVAL,
)
from common.protocol import AgentInfo


class NodeManager:
    """JMeter Agent 节点管理器。

    维护在线 Agent 列表，通过 Redis 心跳机制跟踪节点存活状态，
    自动移除超过三个心跳间隔未响应的失联节点。
    """
    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._agents: dict[str, AgentInfo] = {}

    def get_agents(self) -> list[AgentInfo]:
        """获取所有已注册的 Agent 列表（自动清理过期节点）。"""
        self._cleanup_stale_agents()
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """根据 Agent ID 获取单个节点信息。"""
        self._cleanup_stale_agents()
        return self._agents.get(agent_id)

    def get_online_agents(self) -> list[AgentInfo]:
        """获取所有状态为 online 的 Agent 列表。"""
        return [a for a in self.get_agents() if a.status == "online"]

    def get_available_agents(self) -> list[AgentInfo]:
        """获取所有可用的 Agent 列表。"""
        return [a for a in self.get_agents() if a.status == "online"]

    def update_agent(self, info: AgentInfo):
        """更新或注册一个 Agent 的信息。"""
        self._agents[info.agent_id] = info

    def _cleanup_stale_agents(self):
        """清理超过心跳间隔阈值的失联 Agent。"""
        now = time.time()
        stale_ids = [
            aid for aid, info in self._agents.items()
            if now - info.last_heartbeat > AGENT_HEARTBEAT_INTERVAL * 3
        ]
        for aid in stale_ids:
            del self._agents[aid]
            self.redis.srem("jmeter:agents", aid)
            self.redis.delete(f"jmeter:agent:{aid}")

    def start_heartbeat_listener(self):
        """启动 Redis 心跳监听线程，持续接收 Agent 心跳消息。"""
        pubsub = self.redis.pubsub()
        pubsub.subscribe(**{
            REDIS_CHANNEL_HEARTBEAT: self._on_heartbeat,
        })
        return pubsub.run_in_thread(sleep_time=0.1)

    def _on_heartbeat(self, message):
        """处理收到的心跳消息，解析并更新 Agent 信息。"""
        import json
        try:
            data = json.loads(message["data"])
            info = AgentInfo(**data)
            self.update_agent(info)
        except Exception:
            pass

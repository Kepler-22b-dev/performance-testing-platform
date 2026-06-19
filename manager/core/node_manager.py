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
    def __init__(self):
        self.redis = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
            decode_responses=True,
        )
        self._agents: dict[str, AgentInfo] = {}

    def get_agents(self) -> list[AgentInfo]:
        self._cleanup_stale_agents()
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        self._cleanup_stale_agents()
        return self._agents.get(agent_id)

    def get_online_agents(self) -> list[AgentInfo]:
        return [a for a in self.get_agents() if a.status == "online"]

    def get_available_agents(self) -> list[AgentInfo]:
        return [a for a in self.get_agents() if a.status == "online"]

    def update_agent(self, info: AgentInfo):
        self._agents[info.agent_id] = info

    def _cleanup_stale_agents(self):
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
        pubsub = self.redis.pubsub()
        pubsub.subscribe(**{
            REDIS_CHANNEL_HEARTBEAT: self._on_heartbeat,
        })
        return pubsub.run_in_thread(sleep_time=0.1)

    def _on_heartbeat(self, message):
        import json
        try:
            data = json.loads(message["data"])
            info = AgentInfo(**data)
            self.update_agent(info)
        except Exception:
            pass

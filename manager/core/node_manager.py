"""节点管理器模块。

负责管理 JMeter 压测节点（Agent）的注册、心跳监听和状态维护。
通过 Redis Stream 接收 Agent 心跳信息，自动清理超时失联的节点。
"""

import sys
import os
import time
import json
import threading
import redis
from typing import Optional, List, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.config import (
    REDIS_CHANNEL_HEARTBEAT, AGENT_HEARTBEAT_INTERVAL,
    get_redis_connection_kwargs,
)
from common.protocol import AgentInfo


class NodeManager:
    """JMeter Agent 节点管理器。

    维护在线 Agent 列表，通过 Redis Stream 心跳机制跟踪节点存活状态，
    自动移除超过三个心跳间隔未响应的失联节点。

    心跳机制：
    - Agent 每隔 AGENT_HEARTBEAT_INTERVAL 秒向 jmeter:heartbeat Stream 发送心跳
    - NodeManager 通过消费者组（consumer group）读取心跳消息
    - 超过 3 倍心跳间隔未收到心跳的节点被标记为失联并清理
    """

    def __init__(self) -> None:
        """初始化节点管理器，建立 Redis 连接并创建消费者组。"""
        self.redis = redis.Redis(**get_redis_connection_kwargs())
        # 内存中的 Agent 信息缓存，key 为 agent_id
        self._agents: dict[str, AgentInfo] = {}
        # Redis Stream 消费者组名称，用于心跳消息的可靠消费
        self._consumer_group = "node-manager"
        self._consumer_name = f"nm-{os.getpid()}"
        self._ensure_consumer_group()

    def _ensure_consumer_group(self) -> None:
        """确保 Redis Stream 消费者组存在。"""
        try:
            self.redis.xgroup_create(
                REDIS_CHANNEL_HEARTBEAT, self._consumer_group,
                id="0", mkstream=True,
            )
        except redis.exceptions.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                pass

    def get_agents(self) -> List[AgentInfo]:
        """获取所有已注册的 Agent 列表（自动清理过期节点）。"""
        self._cleanup_stale_agents()
        return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        """根据 Agent ID 获取单个节点信息。"""
        self._cleanup_stale_agents()
        return self._agents.get(agent_id)

    def get_online_agents(self) -> List[AgentInfo]:
        """获取所有状态为 online 的 Agent 列表。"""
        return [a for a in self.get_agents() if a.status == "online"]

    def get_available_agents(self) -> List[AgentInfo]:
        """获取所有可用的 Agent 列表。"""
        return [a for a in self.get_agents() if a.status == "online"]

    def update_agent(self, info: AgentInfo) -> None:
        """更新或注册一个 Agent 的信息。"""
        self._agents[info.agent_id] = info

    def _cleanup_stale_agents(self) -> None:
        """清理超过心跳间隔阈值的失联 Agent。

        判断标准：当前时间 - 最后心跳时间 > AGENT_HEARTBEAT_INTERVAL * 3
        清理操作：从内存缓存删除、从 Redis Set 删除、删除 Redis Hash 信息
        """
        now = time.time()
        stale_ids = [
            aid for aid, info in self._agents.items()
            if now - info.last_heartbeat > AGENT_HEARTBEAT_INTERVAL * 3
        ]
        for aid in stale_ids:
            del self._agents[aid]
            self.redis.srem("jmeter:agents", aid)
            self.redis.delete(f"jmeter:agent:{aid}")

    def start_heartbeat_listener(self) -> "_ThreadStopper":
        """启动 Redis Stream 心跳监听线程，持续接收 Agent 心跳消息。

        返回 _ThreadStopper 实例，兼容原有 heartbeat_thread.stop() 调用方式。
        实际上心跳线程是守护线程，随主进程退出自动终止。
        """
        thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        thread.start()
        return _ThreadStopper(thread)

    def _heartbeat_loop(self) -> None:
        """从 Redis Stream 读取心跳消息的核心循环。

        使用 XREADGROUP 消费者组模式：
        - 每次读取最多 10 条消息
        - 阻塞等待 1 秒（无消息时）
        - 处理完成后 XACK 确认，防止消息重复消费
        - 连接断开时自动重试
        """
        while True:
            try:
                results: Any = self.redis.xreadgroup(
                    self._consumer_group, self._consumer_name,
                    {REDIS_CHANNEL_HEARTBEAT: ">"},
                    count=10,
                    block=1000,
                )
                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        data: str = fields.get("data", "")
                        self._on_heartbeat_data(data)
                        self.redis.xack(
                            REDIS_CHANNEL_HEARTBEAT,
                            self._consumer_group, msg_id,
                        )
            except redis.exceptions.ConnectionError:
                time.sleep(3)
            except Exception:
                time.sleep(0.5)

    def _on_heartbeat_data(self, data: str) -> None:
        """处理心跳数据。"""
        try:
            parsed = json.loads(data)
            info = AgentInfo(**parsed)
            self.update_agent(info)
        except Exception:
            pass


class _ThreadStopper:
    """简单包装，提供 stop() 接口以兼容原有调用方式。"""

    def __init__(self, thread: threading.Thread) -> None:
        self._thread = thread

    def stop(self) -> None:
        pass

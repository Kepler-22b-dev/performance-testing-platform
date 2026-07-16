"""
异步工作器 - 在后台线程运行事件循环，处理所有数据库操作
避免 event loop 冲突问题
"""
import asyncio
import threading
from typing import Any, Coroutine, Optional


class AsyncWorker:
    """在后台线程运行一个事件循环，提供 run_sync 方法。

    用于在同步代码路径（如 FastAPI 同步端点、调度器）中调用异步操作，
    避免 "This event loop is already running" 错误。
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._started: threading.Event = threading.Event()

    def start(self) -> None:
        """启动后台事件循环线程。如果已启动则跳过。"""
        if self._thread and self._thread.is_alive():
            return

        def _run_loop() -> None:
            self._loop = asyncio.new_event_loop()
            asyncio.set_event_loop(self._loop)
            self._started.set()
            self._loop.run_forever()

        self._thread = threading.Thread(target=_run_loop, daemon=True)
        self._thread.start()
        self._started.wait(timeout=5)

    def run_sync(self, coro: Coroutine) -> Any:
        """在后台事件循环中运行协程并阻塞等待结果。

        Args:
            coro: 异步协程对象

        Returns:
            协程的返回值

        Raises:
            RuntimeError: 如果后台事件循环未运行
            TimeoutError: 如果协程执行超过 30 秒
        """
        if not self._loop or not self._loop.is_running():
            self.start()

        assert self._loop is not None
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=30)

    def stop(self) -> None:
        """停止后台事件循环线程。"""
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if self._thread:
            self._thread.join(timeout=5)


# 全局异步工作器实例
async_worker = AsyncWorker()


def run_async(coro: Coroutine) -> Any:
    """便捷函数：在后台线程运行异步协程"""
    return async_worker.run_sync(coro)

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from common.protocol import TaskResult, ProgressUpdate
from common.config import REDIS_HOST, REDIS_PORT, REDIS_DB, REDIS_CHANNEL_RESULT, REDIS_CHANNEL_PROGRESS

from manager.core.node_manager import NodeManager
from manager.core.scheduler import TaskScheduler
from manager.core.ws import ConnectionManager

from manager.api.nodes import router as nodes_router, set_node_manager
from manager.api.scripts import router as scripts_router
from manager.api.tasks import router as tasks_router, set_scheduler
from manager.api.results import router as results_router
from manager.api.slave import router as slave_router
from manager.api.registry import router as registry_router
from manager.api.monitor import router as monitor_router
from manager.api.data import router as data_router
from manager.api.templates import router as templates_router
from manager.api.notifications import router as notifications_router
from manager.api.scheduler_api import router as scheduler_router

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


node_manager = NodeManager()
scheduler = TaskScheduler(node_manager=node_manager)
ws_manager = ConnectionManager()


async def redis_listener():
    import redis.asyncio as aioredis
    r = aioredis.Redis(
        host=REDIS_HOST, port=REDIS_PORT, db=REDIS_DB,
        decode_responses=True,
    )
    pubsub = r.pubsub()
    await pubsub.subscribe(REDIS_CHANNEL_RESULT, REDIS_CHANNEL_PROGRESS)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                channel = message["channel"]
                data = message["data"]

                try:
                    payload = json.loads(data)
                except Exception:
                    continue

                if channel == REDIS_CHANNEL_RESULT:
                    result = TaskResult.from_json(data)
                    scheduler.handle_result(result)
                    await ws_manager.broadcast({"channel": "result", "data": payload})
                elif channel == REDIS_CHANNEL_PROGRESS:
                    update = ProgressUpdate.from_json(data)
                    scheduler.handle_progress(update)
                    await ws_manager.broadcast({"channel": "progress", "data": payload})
    except asyncio.CancelledError:
        await pubsub.unsubscribe()
        await r.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    heartbeat_thread = node_manager.start_heartbeat_listener()
    listener_task = asyncio.create_task(redis_listener())
    init_scheduler_loop()

    yield

    listener_task.cancel()
    heartbeat_thread.stop()


app = FastAPI(
    title="性能测试平台 API",
    description="""
## 性能测试平台

基于 JMeter 的分布式性能测试平台，提供以下功能：

### 核心功能
- **任务管理** - 创建、启动、停止、删除测试任务
- **脚本管理** - 上传、编辑、新建 JMeter 脚本
- **结果分析** - 查看测试结果、图表、导出报告
- **节点管理** - 管理施压节点和 JMeter Slave
- **数据管理** - 全局变量和 CSV 数据文件

### 扩展功能
- **任务对比** - 对比两次压测结果
- **测试模板** - 预置常见压测场景模板
- **定时调度** - 定时/周期性执行压测任务
- **通知系统** - 任务完成后通过 Webhook 通知
- **实时监控** - CPU/内存/网络实时监控
- **API 文档** - 本 Swagger UI 文档
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

set_node_manager(node_manager)
set_scheduler(scheduler)

from manager.api.scheduler_api import set_scheduler as set_sched_api, init_scheduler_loop
set_sched_api(scheduler)

app.include_router(nodes_router)
app.include_router(scripts_router)
app.include_router(tasks_router)
app.include_router(results_router)
app.include_router(slave_router)
app.include_router(registry_router)
app.include_router(monitor_router)
app.include_router(data_router)
app.include_router(templates_router)
app.include_router(notifications_router)
app.include_router(scheduler_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    ws_manager.add_connection(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.remove_connection(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

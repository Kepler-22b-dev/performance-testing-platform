"""性能测试平台 Manager 主程序。

本模块是性能测试平台的管理端入口，基于 FastAPI 构建，提供以下核心能力：

- **任务调度** - 分发压测任务至 Agent 节点并跟踪执行状态
- **节点管理** - 监控 Agent 节点心跳、上下线状态
- **结果收集** - 通过 Redis 订阅测试结果和进度更新
- **WebSocket 推送** - 向前端实时推送测试进度和结果
- **REST API** - 提供脚本管理、任务管理、结果查询等接口
- **日志系统** - 记录 API 请求、任务事件、错误信息
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from urllib.parse import urlsplit
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from common.protocol import TaskResult, ProgressUpdate
from common.config import (
    REDIS_HOST, REDIS_PORT,
    REDIS_CHANNEL_RESULT, REDIS_CHANNEL_PROGRESS, REDIS_STREAM_READ_BLOCK_MS,
    COMMAND_CLAIM_IDLE_MS, CORS_ALLOWED_ORIGINS, get_redis_connection_kwargs,
)
from common.logger import get_logger, get_api_logger, get_task_logger, log_error, log_task_event
from common.database import init_db
from common.async_worker import async_worker

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
from manager.api.alerts import router as alerts_router
from manager.api.environments import router as environments_router
from manager.api.jtl_compare import router as jtl_router
from manager.api.tool_logs import (
    LOG_CLEANUP_INTERVAL_SECONDS,
    cleanup_expired_logs,
    router as tool_logs_router,
)
from manager.api.mobile import router as mobile_router

from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse


# 初始化日志
logger = get_logger("manager", level="INFO", log_file="manager.log")
api_logger = get_api_logger()
task_logger = get_task_logger()

logger.info("Manager 服务启动中...")


node_manager = NodeManager()
scheduler = TaskScheduler(node_manager=node_manager)
ws_manager = ConnectionManager()

logger.info(f"Redis 连接: {REDIS_HOST}:{REDIS_PORT}")


def _log_task_exception(task):
    """记录后台任务异常，防止静默失败。

    作为 asyncio.Task.add_done_callback 的回调函数使用。
    如果后台任务（如 Redis Stream 监听器）抛出异常但未被处理，
    异常会被静默吞掉，导致功能失效但无任何日志。
    通过此回调确保异常被记录到日志中。
    """
    if task.cancelled():
        return
    exc = task.exception()
    if exc:
        logger.error("后台任务异常: %s: %s", type(exc).__name__, exc, exc_info=exc)


async def redis_listener():
    """Redis Stream 监听器 - 从 Stream 读取任务结果和进度更新。

    使用消费者组（Consumer Group）模式消费消息，确保：
    1. 消息持久化：Manager 重启后未消费的消息不会丢失
    2. 可靠消费：处理完成后 XACK 确认，避免重复消费
    3. 负载均衡：多实例部署时消息自动分配到不同消费者

    消费的 Stream 频道：
    - jmeter:result: Agent 上报的任务执行结果
    - jmeter:progress: Agent 上报的实时进度数据
    """
    import redis.asyncio as aioredis
    r = aioredis.Redis(**get_redis_connection_kwargs())
    consumer_group = "manager"
    consumer_name = f"manager-{os.getpid()}"

    # 为每个 Stream 创建消费者组（如果不存在）
    # mkstream=True 表示 Stream 不存在时自动创建
    # id="0" 表示从 Stream 起始位置开始消费
    # BUSYGROUP 错误表示消费者组已存在，属于正常情况
    for stream_key in [REDIS_CHANNEL_RESULT, REDIS_CHANNEL_PROGRESS]:
        try:
            await r.xgroup_create(stream_key, consumer_group, id="0", mkstream=True)
        except Exception:
            pass

    logger.info("Redis Stream 监听器已启动，订阅结果和进度频道")

    async def process_message(stream_name, msg_id, fields):
        data = fields.get("data", "")
        try:
            payload = json.loads(data)
        except Exception:
            await r.xack(stream_name, consumer_group, msg_id)
            return

        if stream_name == REDIS_CHANNEL_RESULT:
            result = TaskResult.from_json(data)
            log_task_event(task_logger, result.task_id, "结果收到",
                           {"agent": result.agent_id, "status": result.status})
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, scheduler.handle_result, result)
            await ws_manager.broadcast({"channel": "result", "data": payload})
        elif stream_name == REDIS_CHANNEL_PROGRESS:
            update = ProgressUpdate.from_json(data)
            scheduler.handle_progress(update)
            await ws_manager.broadcast({"channel": "progress", "data": payload})

        await r.xack(stream_name, consumer_group, msg_id)

    try:
        last_claim_at = 0.0
        while True:
            try:
                now = asyncio.get_running_loop().time()
                if now - last_claim_at >= max(1, COMMAND_CLAIM_IDLE_MS / 1000):
                    for stream_key in [REDIS_CHANNEL_RESULT, REDIS_CHANNEL_PROGRESS]:
                        claimed = await r.xautoclaim(
                            stream_key,
                            consumer_group,
                            consumer_name,
                            min_idle_time=COMMAND_CLAIM_IDLE_MS,
                            start_id="0-0",
                            count=10,
                        )
                        claimed_messages = claimed[1] if claimed and len(claimed) > 1 else []
                        for msg_id, fields in claimed_messages:
                            await process_message(stream_key, msg_id, fields)
                    last_claim_at = now

                # 使用 ">" 表示只读取未被消费的新消息
                # count=10 表示每次最多读取 10 条
                # block=1000 表示无消息时阻塞等待 1 秒
                streams = {REDIS_CHANNEL_RESULT: ">", REDIS_CHANNEL_PROGRESS: ">"}
                results = await r.xreadgroup(
                    consumer_group, consumer_name,
                    streams,
                    count=10,
                    block=REDIS_STREAM_READ_BLOCK_MS,
                )
                for stream_name, messages in results:
                    for msg_id, fields in messages:
                        await process_message(stream_name, msg_id, fields)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("Redis Stream 读取异常: %s", e)
                await asyncio.sleep(1)
    finally:
        logger.info("Redis Stream 监听器已停止")
        await r.close()


async def log_cleanup_loop():
    """定期清理过期工具日志，不影响压测报告结果。"""
    while True:
        try:
            result = await asyncio.to_thread(cleanup_expired_logs)
            if result["deleted_files"]:
                logger.info(
                    "工具日志清理完成: 删除 %s 个文件，释放 %.2f MB",
                    result["deleted_files"],
                    result["deleted_bytes"] / 1024 / 1024,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_error(logger, exc, "工具日志清理")
        await asyncio.sleep(LOG_CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    async_worker.start()
    logger.info("异步工作器已启动")
    await init_db()
    logger.info("数据库初始化完成")
    logger.info("Manager 服务启动完成")
    heartbeat_thread = node_manager.start_heartbeat_listener()
    logger.info("心跳监听器已启动")
    listener_task = asyncio.create_task(redis_listener())
    listener_task.add_done_callback(_log_task_exception)
    cleanup_task = asyncio.create_task(log_cleanup_loop())
    cleanup_task.add_done_callback(_log_task_exception)
    init_scheduler_loop()
    logger.info("定时调度器已启动")

    yield

    logger.info("Manager 服务关闭中...")
    listener_task.cancel()
    cleanup_task.cancel()
    heartbeat_thread.stop()
    logger.info("Manager 服务已关闭")


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
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """全局异常处理器，捕获所有未处理的异常。

    作用：
    1. 防止未处理异常导致 500 错误时无日志
    2. 返回结构化 JSON 错误响应，便于前端处理
    3. 记录请求路径和异常类型，便于问题定位
    """
    api_logger.error(f"Unhandled exception: {type(exc).__name__}: {exc} | {request.method} {request.url.path}")
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误", "error": type(exc).__name__},
    )


# 添加 API 请求日志中间件
@app.middleware("http")
async def log_requests(request, call_next):
    import time as _time
    start = _time.time()
    response = await call_next(request)
    duration = (_time.time() - start) * 1000
    api_logger.info(f"{request.method} {request.url.path} -> {response.status_code} ({duration:.1f}ms)")
    return response

logger.info(f"API 路由已注册: nodes, scripts, tasks, results, slave, registry, monitor, data, templates, notifications, scheduler, alerts, environments, tool_logs, mobile")

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
app.include_router(alerts_router)
app.include_router(environments_router)
app.include_router(jtl_router)
app.include_router(tool_logs_router)
app.include_router(mobile_router)

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def index():
    """返回前端首页。"""
    from fastapi.responses import HTMLResponse
    with open(os.path.join(STATIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        content = f.read()
    return HTMLResponse(content=content, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


@app.get("/api/health")
def health():
    """健康检查接口。用于监控服务是否正常运行。"""
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket 端点 - 实时推送测试进度和结果。

    前端通过此连接接收：
    - 实时进度数据（TPS、响应时间、错误率）
    - 任务完成通知
    - Agent 状态变更

    心跳机制：前端定期发送 "ping"，服务端回复 "pong"
    用于检测连接是否存活，防止代理服务器/防火墙断开空闲连接
    """
    origin = websocket.headers.get("origin")
    request_host = websocket.headers.get("host")
    origin_host = urlsplit(origin).netloc if origin else ""
    if origin and origin not in CORS_ALLOWED_ORIGINS and origin_host != request_host:
        await websocket.close(code=1008, reason="WebSocket Origin 不受信任")
        return

    await websocket.accept()
    ws_manager.add_connection(websocket)
    try:
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.remove_connection(websocket)
    except Exception:
        ws_manager.remove_connection(websocket)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# 性能测试平台 - 日志查看指南

> 版本：1.0 | 更新时间：2026-06-20

---

## 一、日志文件说明

平台日志存储在 `logs/` 目录下，按模块自动分类：

| 日志文件 | 用途 | 记录内容 |
|----------|------|----------|
| `manager.log` | Manager 服务日志 | 服务启动/关闭、Redis连接、调度器状态 |
| `api.log` | API 请求日志 | 所有 HTTP 请求的方法、路径、状态码、耗时 |
| `agent.log` | Agent 运行日志 | Agent 启动/停止、心跳状态、Redis注册 |
| `task.log` | 任务事件日志 | 任务创建/执行/完成/失败等生命周期事件 |
| `error.log` | 错误日志 | 异常堆栈、错误详情 |

## 二、日志格式

```
2026-06-20 10:24:40 [INFO] manager: Manager 服务启动中...
```

| 字段 | 说明 |
|------|------|
| `2026-06-20 10:24:40` | 时间戳 |
| `[INFO]` | 日志级别 (DEBUG/INFO/WARNING/ERROR) |
| `manager` | 模块名称 |
| `Manager 服务启动中...` | 日志消息 |

## 三、查看日志方法

### 方法 1：实时跟踪日志

```bash
# 实时查看 Manager 日志
tail -f logs/manager.log

# 实时查看 API 请求
tail -f logs/api.log

# 实时查看 Agent 日志
tail -f logs/agent.log

# 实时查看任务事件
tail -f logs/task.log

# 同时查看所有日志
tail -f logs/*.log
```

### 方法 2：查看最近 N 行

```bash
# 查看最近 50 行
tail -50 logs/manager.log

# 查看最近 100 行 API 请求
tail -100 logs/api.log

# 查看最近 20 行错误日志
tail -20 logs/error.log
```

### 方法 3：搜索日志内容

```bash
# 搜索某个任务的日志
grep "task-abc123" logs/task.log

# 搜索错误日志
grep "ERROR" logs/error.log

# 搜索特定 API 请求
grep "/api/tasks" logs/api.log

# 搜索特定 Agent
grep "agent-xxxx" logs/agent.log

# 搜索时间范围内的日志
grep "2026-06-20 10:" logs/api.log
```

### 方法 4：统计分析

```bash
# 统计 API 请求数
wc -l logs/api.log

# 统计错误数量
grep -c "ERROR" logs/error.log

# 统计特定接口请求数
grep -c "/api/tasks" logs/api.log

# 统计响应时间分布
awk '{print $NF}' logs/api.log | grep -oP '\(\K[0-9]+' | sort -n | tail -20
```

### 方法 5：Web UI 查看

1. 打开 http://localhost:8000
2. 进入「任务管理」
3. 点击任务的「查看图表」
4. 页面底部有「执行日志」区域
5. 点击节点标签查看 JMeter 执行日志

## 四、常见日志场景

### 场景 1：排查服务启动问题

```bash
# 检查 Manager 启动日志
tail -20 logs/manager.log

# 检查 Agent 启动日志
tail -20 logs/agent.log
```

**正常启动日志：**
```
2026-06-20 10:24:40 [INFO] manager: Manager 服务启动中...
2026-06-20 10:24:40 [INFO] manager: Redis 连接: 127.0.0.1:6379
2026-06-20 10:24:40 [INFO] manager: Manager 服务启动完成
```

### 场景 2：排查 API 请求问题

```bash
# 查看 API 请求日志
tail -50 logs/api.log

# 查看失败的请求
grep "500\|404\|400" logs/api.log
```

**正常请求日志：**
```
2026-06-20 10:24:43 [INFO] api: GET /api/health -> 200 (1.7ms)
2026-06-20 10:24:45 [INFO] api: POST /api/tasks/quick-run -> 200 (45.2ms)
```

### 场景 3：排查任务执行问题

```bash
# 查看任务事件
grep "task-abc123" logs/task.log

# 查看任务错误
grep "task-abc123.*失败\|task-abc123.*failed" logs/task.log
```

**任务事件日志：**
```
2026-06-20 10:30:00 [INFO] task: [Task:task-abc123] 开始执行 | {"agent": "agent-xxxx", "script": "..."}
2026-06-20 10:35:00 [INFO] task: [Task:task-abc123] 执行完成 | {"agent": "agent-xxxx", "status": "completed", "samples": 1000}
```

### 场景 4：排查 Agent 连接问题

```bash
# 查看 Agent 日志
tail -50 logs/agent.log

# 查看心跳状态
grep "心跳" logs/agent.log
```

### 场景 5：排查性能问题

```bash
# 查看慢请求（耗时 > 1000ms）
grep -E "\([0-9]{4,}ms\)" logs/api.log

# 查看 API 请求耗时统计
awk -F'\\(' '{print $2}' logs/api.log | awk -F'ms' '{print $1}' | sort -n | tail -10
```

## 五、日志配置

### 修改日志级别

在 `common/logger.py` 中修改默认级别：

```python
# 修改为 DEBUG 级别（输出更详细）
logger = get_logger("manager", level="DEBUG", log_file="manager.log")

# 修改为 WARNING 级别（只输出警告和错误）
logger = get_logger("manager", level="WARNING", log_file="manager.log")
```

### 修改日志文件大小

```python
# 单个文件最大 20MB，保留 10 个历史文件
logger = get_logger("manager", max_bytes=20*1024*1024, backup_count=10)
```

### 禁用文件输出

```python
# 只输出到控制台
logger = get_logger("manager", file=False)
```

## 六、日志清理

### 手动清理

```bash
# 删除 7 天前的日志
find logs/ -name "*.log.*" -mtime +7 -delete

# 删除所有历史日志
find logs/ -name "*.log.*" -delete

# 清空当前日志
> logs/manager.log
> logs/api.log
> logs/agent.log
> logs/task.log
```

### 自动清理（crontab）

```bash
# 每天凌晨 3 点清理 7 天前的日志
0 3 * * * find /path/to/logs/ -name "*.log.*" -mtime +7 -delete
```

## 七、日志格式化输出

### 使用 awk 格式化

```bash
# 格式化 API 日志（只显示时间和路径）
awk '{print $1, $2, $5, $6}' logs/api.log | head -20
```

### 使用 jq 解析 JSON 日志

```bash
# 如果日志包含 JSON 数据
grep "task" logs/task.log | jq .
```

## 八、远程查看日志

### SSH 查看

```bash
# 查看远程服务器日志
ssh user@server "tail -f /path/to/logs/manager.log"

# 下载日志文件
scp user@server:/path/to/logs/*.log ./local-logs/
```

### 日志聚合（规划中）

未来可集成 ELK Stack 或 Grafana Loki 进行日志聚合和可视化。

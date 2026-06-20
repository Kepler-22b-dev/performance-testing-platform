# 性能测试平台

基于 Apache JMeter 的分布式性能测试平台，提供 Web 界面管理压测任务、分析结果、监控资源。

## 项目架构

```
Performance Testing Platform/
├── common/                     # 公共模块
│   ├── config.py              # 全局配置（Redis、JMeter路径、频道等）
│   └── protocol.py            # 通信协议（任务状态、命令、结果等数据结构）
│
├── manager/                    # Manager 服务（FastAPI）
│   ├── main.py                # FastAPI 应用入口
│   ├── api/                   # API 路由层
│   │   ├── tasks.py           # 任务管理（创建/启动/停止/删除/重跑）
│   │   ├── scripts.py         # 脚本管理（上传/编辑/搜索/拖拽排序）
│   │   ├── results.py         # 结果分析（报告/对比/趋势/PDF导出）
│   │   ├── nodes.py           # 节点管理（Agent 状态查询）
│   │   ├── data.py            # 数据管理（全局变量/CSV文件）
│   │   ├── monitor.py         # 系统监控（CPU/内存/网络）
│   │   ├── slave.py           # JMeter Slave 控制
│   │   ├── registry.py        # 远程节点注册与验证
│   │   ├── templates.py       # 测试计划模板
│   │   ├── notifications.py   # Webhook 通知设置
│   │   ├── scheduler_api.py   # 定时调度管理
│   │   ├── alerts.py          # 告警规则配置
│   │   └── environments.py    # 多环境管理
│   ├── core/                  # 核心业务逻辑
│   │   ├── scheduler.py       # 任务调度器（任务生命周期管理）
│   │   ├── node_manager.py    # Agent 节点管理（心跳/状态）
│   │   ├── node_registry.py   # 远程节点注册表
│   │   ├── monitor.py         # 系统资源监控
│   │   ├── slave_manager.py   # JMeter Slave 进程管理
│   │   ├── variables.py       # 变量和 CSV 文件管理
│   │   ├── sample_cache.py    # 采样数据缓存
│   │   └── ws.py              # WebSocket 连接管理
│   ├── models/                # 数据模型（预留）
│   └── static/
│       └── index.html         # 前端单页应用（HTML/CSS/JS）
│
├── agent/                      # Agent 服务（执行节点）
│   ├── main.py                # Agent 主程序（接收命令/上报进度）
│   ├── jmeter_runner.py       # JMeter 执行器（运行测试/解析结果）
│   └── requirements.txt       # Agent 依赖
│
├── scripts/                    # JMeter 脚本目录
├── reports/                    # 压测报告输出目录
├── config/                     # 配置文件
│   ├── nodes.json             # 已注册节点
│   ├── variables.json         # 全局变量
│   ├── script_counter.json    # 脚本 ID 计数器
│   └── csv/                   # CSV 数据文件
│
├── cli.py                      # CLI 命令行工具（CI/CD 集成）
├── deploy.sh                   # 一键部署脚本
├── start-slave.sh              # Slave 启动脚本
├── .github/workflows/          # GitHub Actions 配置
│   └── perf-test.yml           # 性能测试流水线模板
│
└── apache-jmeter-5.6.3/        # JMeter 安装目录
```

## 核心功能

### 压测管理
- 任务创建、启动、停止、删除、重跑
- 支持单机和分布式压测模式
- CSV 参数化数据支持（10万+行）
- 实时进度监控（TPS/RT/错误率）

### 脚本管理
- JMX 脚本上传、在线编辑
- 树形视图可视化展示 JMeter 组件
- 拖拽排序调整组件顺序
- 模糊搜索（文件名和接口关键词）

### 结果分析
- TPS/响应时间/错误率实时曲线
- 响应时间分布和百分位统计
- 接口级性能分析（含 TPS）
- HTML/PDF 报告导出
- 两次压测结果对比
- 历史趋势分析

### 高级功能
- 测试计划模板（8个内置场景）
- 定时调度（间隔/单次执行）
- 告警规则（阈值检测 + Webhook 通知）
- 多环境管理（dev/staging/prod）
- CLI 命令行工具（CI/CD 集成）

## 技术栈

| 组件 | 技术 |
|------|------|
| 后端 | Python 3.10+ / FastAPI |
| 前端 | 原生 HTML/CSS/JS + ECharts |
| 压测引擎 | Apache JMeter 5.6.3 |
| 消息队列 | Redis PubSub |
| 数据存储 | Redis（持久化） |
| 图表可视化 | ECharts 5.4.3 |

## 快速开始

### 环境要求
- Python 3.10+
- Java 8+（JMeter 需要）
- Redis

### 一键部署
```bash
bash deploy.sh
```

### 手动部署

#### 1. 安装依赖
```bash
# Manager 依赖
pip install -r manager/requirements.txt

# Agent 依赖
pip install -r agent/requirements.txt
```

#### 2. 启动 Redis
```bash
# macOS
brew services start redis

# Linux
sudo systemctl start redis
```

#### 3. 启动 Manager
```bash
python3 -m manager.main
# 访问 http://localhost:8000
```

#### 4. 启动 Agent
```bash
python3 -m agent.main
```

#### 5. 启动 Slave（分布式模式）
```bash
bash start-slave.sh 1100
```

## 部署方案

### 方案一：单机部署（开发/测试）

适用场景：个人开发、功能测试

```
┌─────────────────────────────┐
│         单机部署              │
│                              │
│  ┌──────────┐ ┌──────────┐  │
│  │ Manager  │ │  Agent   │  │
│  │ :8000    │ │  :9999   │  │
│  └────┬─────┘ └────┬─────┘  │
│       │            │        │
│  ┌────▼────────────▼────┐   │
│  │      Redis :6379     │   │
│  └──────────────────────┘   │
│                              │
│  ┌──────────────────────┐   │
│  │  JMeter Slave :1100  │   │
│  └──────────────────────┘   │
└─────────────────────────────┘
```

```bash
# 一键启动
bash deploy.sh start

# 查看状态
bash deploy.sh status

# 停止所有
bash deploy.sh stop
```

### 方案二：分布式部署（生产环境）

适用场景：大规模压测、多机协作

```
┌─────────────────────────────────────────────────────────┐
│                    管理节点                                │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Manager  │  │  Redis   │  │  Web UI  │              │
│  │  :8000   │  │  :6379   │  │  :8000   │              │
│  └────┬─────┘  └──────────┘  └──────────┘              │
└───────┼─────────────────────────────────────────────────┘
        │
┌───────▼─────────────────────────────────────────────────┐
│                    施压节点组                              │
│                                                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ Agent 1  │  │ Agent 2  │  │ Agent N  │              │
│  │  :9999   │  │  :9999   │  │  :9999   │              │
│  └────┬─────┘  └────┬─────┘  └────┬─────┘              │
│       │              │              │                    │
│  ┌────▼─────┐  ┌────▼─────┐  ┌────▼─────┐              │
│  │  Slave   │  │  Slave   │  │  Slave   │              │
│  │  :1100   │  │  :1200   │  │  :1300   │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└─────────────────────────────────────────────────────────┘
```

#### 部署步骤

**管理节点：**
```bash
# 1. 安装依赖
pip install -r manager/requirements.txt

# 2. 启动 Redis
sudo systemctl start redis

# 3. 启动 Manager
nohup python3 -m manager.main > manager.log 2>&1 &
```

**施压节点（每台执行）：**
```bash
# 1. 复制项目到施压节点
scp -r Performance Testing Platform/ user@agent-host:~/

# 2. 安装依赖
pip install -r agent/requirements.txt

# 3. 启动 Agent
nohup python3 -m agent.main > agent.log 2>&1 &

# 4. 启动 Slave
bash start-slave.sh 1100
```

**配置远程节点：**
1. 访问 Web UI → 施压节点 → 添加远程节点
2. 输入 IP 和端口
3. 点击「验证」确认连通性

### 方案三：Docker 部署（规划中）

```yaml
# docker-compose.yml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  manager:
    build: .
    command: python3 -m manager.main
    ports:
      - "8000:8000"
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis

  agent:
    build: .
    command: python3 -m agent.main
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
```

### 方案四：Kubernetes 部署（规划中）

适用于大规模企业级部署，支持自动扩缩容。

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| REDIS_HOST | 127.0.0.1 | Redis 地址 |
| REDIS_PORT | 6379 | Redis 端口 |
| JMETER_HOME | /tmp/jmeter | JMeter 安装路径 |
| SCRIPTS_DIR | ./scripts | 脚本目录 |
| REPORTS_DIR | ./reports | 报告目录 |
| AGENT_HEARTBEAT_INTERVAL | 5 | 心跳间隔(秒) |
| TASK_TIMEOUT | 3600 | 任务超时(秒) |

### Redis 频道

| 频道 | 用途 |
|------|------|
| jmeter:command | Manager → Agent 命令下发 |
| jmeter:result | Agent → Manager 结果上报 |
| jmeter:progress | Agent → Manager 进度上报 |
| jmeter:heartbeat | Agent → Manager 心跳 |

## API 文档

启动服务后访问：
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

### 核心 API

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/tasks/ | GET | 获取任务列表 |
| /api/tasks/quick-run | POST | 快速执行压测 |
| /api/scripts/ | GET | 获取脚本列表 |
| /api/scripts/upload | POST | 上传脚本 |
| /api/scripts/search?q= | GET | 搜索脚本 |
| /api/results/tasks | GET | 获取结果列表 |
| /api/results/trend | GET | 性能趋势数据 |
| /api/results/compare | GET | 任务对比 |
| /api/templates/ | GET | 获取模板列表 |
| /api/scheduler/ | GET | 获取调度列表 |
| /api/alerts/ | GET | 获取告警规则 |
| /api/environments/ | GET | 获取环境列表 |
| /api/monitor/overview | GET | 系统监控概览 |

## CLI 工具

```bash
# 安装后直接使用
python cli.py --help

# 启动压测
python cli.py run --script 8 --threads 10 --duration 60

# 等待完成
python cli.py wait --task <task_id> --timeout 600

# 性能阈值检查
python cli.py check --task <task_id> --max-avg-rt 500 --max-error-rate 1

# 导出报告
python cli.py report --task <task_id> --format pdf

# 搜索脚本
python cli.py list-scripts

# CI/CD 集成示例
python cli.py run --script 8 --threads 10 --duration 60 && \
python cli.py wait --task <task_id> && \
python cli.py check --task <task_id> --max-avg-rt 1000
```

## 常见问题

### Q: Agent 连接不上 Manager？
A: 检查 Redis 是否运行，确认 Agent 和 Manager 使用相同的 Redis 地址。

### Q: JMeter 脚本执行失败？
A: 检查 Java 版本（需要 8+），确认 JMeter 安装路径正确。

### Q: 如何查看实时压测数据？
A: 在「任务管理」页面点击运行中的任务，可查看实时 TPS/RT/错误率曲线。

### Q: CSV 参数化如何使用？
A: 1. 在「数据管理」上传 CSV 文件  2. 创建任务时勾选「使用 CSV 参数化数据」  3. 在 JMX 脚本中用 `${变量名}` 引用

## 开发指南

### 项目结构
- `common/` - 公共模块，Manager 和 Agent 共用
- `manager/` - Manager 服务，提供 Web API 和前端
- `agent/` - Agent 服务，执行压测任务

### 添加新功能
1. 在 `manager/api/` 创建新的路由文件
2. 在 `manager/main.py` 注册路由
3. 在 `manager/static/index.html` 添加前端页面
4. 更新 `common/protocol.py` 添加新的数据结构（如需要）

### 运行测试
```bash
# 启动服务
python3 -m manager.main &
python3 -m agent.main &

# 测试 API
curl http://localhost:8000/api/health
curl http://localhost:8000/api/scripts/
```

## 版本历史

### v1.0.0 (2026-06-20)
- 完整的分布式压测功能
- Web 界面管理
- 树形视图脚本编辑
- 拖拽排序组件
- 模糊搜索
- 测试模板
- 定时调度
- 告警规则
- 多环境管理
- CLI 工具
- PDF 报告导出

## 许可证

MIT License

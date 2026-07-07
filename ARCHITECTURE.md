# 性能测试平台 - 系统架构手册

> 版本：3.0 | 更新时间：2026-07-07

---

## 一、平台概述

性能测试平台是一个基于 Apache JMeter 的分布式性能测试平台，提供 Web 界面管理压测任务、分析结果、监控资源，并新增移动端性能监控能力。

### 核心能力

| 能力 | 说明 |
|------|------|
| 🎯 压测执行 | 分布式 JMeter 压测，支持多 Slave 节点 |
| 📊 结果分析 | TPS/RT/错误率曲线、分布图、百分位分析 |
| 📱 移动端监控 | iOS (pymobiledevice3) + Android (SOLOX) 性能采集 |
| ⏱️ 定时调度 | 间隔执行、单次定时、cron 表达式 |
| 🔔 告警通知 | 阈值检测 + Webhook 通知 |
| 📈 趋势分析 | 历史性能趋势、任务对比 |

---

## 二、系统架构

### 2.1 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────┐
│                              用户浏览器                                  │
│                        http://管理节点IP:8000                            │
└───────────────────────────────────┬─────────────────────────────────────┘
                                    │ HTTP / WebSocket
┌───────────────────────────────────▼─────────────────────────────────────┐
│                           Manager 服务                                   │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐   │
│  │   任务管理    │ │   脚本编辑   │ │   结果分析   │ │   节点管理   │   │
│  │   模板引擎    │ │   定时调度   │ │   通知系统   │ │   资源监控   │   │
│  │   告警规则    │ │   环境管理   │ │   JTL对比    │ │   日志系统   │   │
│  └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘   │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                    移动端性能监控模块 (新增)                       │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐             │   │
│  │  │ iOS Monitor │  │Android Mon. │  │  设备检测    │             │   │
│  │  │(pymobiledev)│  │   (SOLOX)   │  │  应用列表    │             │   │
│  │  └─────────────┘  └─────────────┘  └─────────────┘             │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└───────┬─────────────────────────┬─────────────────────┬─────────────────┘
        │ Redis                   │ Redis               │ Redis PubSub
┌───────▼───────────┐  ┌─────────▼─────────┐  ┌────────▼────────────────┐
│       Redis       │  │     Agent 1       │  │     Agent 2 / N         │
│       :6379       │  │     (施压机1)      │  │     (施压机2/N)          │
│  消息队列+持久化   │  │      :9999        │  │      :9999              │
└───────────────────┘  └────────┬──────────┘  └────────┬────────────────┘
                                │                       │
                         ┌──────▼──────┐       ┌───────▼──────┐
                         │ JMeter Slave│       │ JMeter Slave │
                         │    :1100    │       │    :1200     │
                         └─────────────┘       └──────────────┘

┌─────────────────────────────────────────────────────────────────────────┐
│                          移动设备连接                                     │
│  ┌─────────────────┐                    ┌─────────────────┐            │
│  │   iPhone/iPad   │                    │ Android 手机     │            │
│  │   (pymobiledev) │                    │   (SOLOX)        │            │
│  └────────┬────────┘                    └────────┬────────┘            │
│           │ USB                                    │ USB                │
│           ▼                                        ▼                    │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │              Manager: /api/mobile/* 接口                         │   │
│  └─────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| **前端** | HTML/CSS/JS | - | 原生单页应用，无框架依赖 |
| **图表** | ECharts | 5.4.3 | 实时曲线、对比图表、趋势分析 |
| **后端** | Python + FastAPI | 3.10+ | 高性能异步 API 框架 |
| **消息队列** | Redis | 6.0+ | PubSub 实时通信 + 数据持久化 |
| **压测引擎** | Apache JMeter | 5.6.3 | 行业标准压测工具 |
| **通信协议** | Redis PubSub | - | Manager ↔ Agent 实时通信 |
| **iOS监控** | pymobiledevice3 | 9.x | iOS 设备性能数据采集 |
| **Android监控** | SOLOX | 2.9.x | Android/iOS 性能数据采集 |

### 2.3 数据流

```
用户操作 → Web UI → FastAPI API → Redis PubSub → Agent → JMeter
                                                          ↓
用户看到 ← WebSocket ← FastAPI ← Redis PubSub ← Agent ← 执行结果

移动端监控:
iPhone/Android → USB → pymobiledevice3/SOLOX → FastAPI API → Web UI (ECharts)
```

---

## 三、模块详解

### 3.1 Manager 服务

Manager 是平台的核心服务，负责任务调度、结果收集、API 提供。

```
manager/
├── main.py                 # FastAPI 应用入口
├── api/                    # API 路由层（14个模块）
│   ├── tasks.py           # 任务管理
│   ├── scripts.py         # 脚本管理
│   ├── results.py         # 结果分析
│   ├── nodes.py           # 节点管理
│   ├── data.py            # 数据管理（变量/CSV）
│   ├── monitor.py         # 系统监控
│   ├── slave.py           # JMeter Slave 控制
│   ├── registry.py        # 远程节点注册
│   ├── templates.py       # 测试计划模板
│   ├── notifications.py   # Webhook 通知
│   ├── scheduler_api.py   # 定时调度
│   ├── alerts.py          # 告警规则
│   ├── environments.py    # 多环境管理
│   ├── jtl_compare.py     # JTL 文件对比
│   ├── tool_logs.py       # 工具日志
│   └── mobile.py          # 移动端监控 (新增)
├── core/                  # 核心业务逻辑
│   ├── scheduler.py       # 任务调度器
│   ├── node_manager.py    # Agent 节点管理
│   ├── node_registry.py   # 远程节点注册表
│   ├── monitor.py         # 系统资源监控
│   ├── slave_manager.py   # JMeter Slave 管理
│   ├── variables.py       # 变量和 CSV 管理
│   ├── sample_cache.py    # 采样数据缓存
│   ├── ws.py              # WebSocket 管理
│   └── mobile_monitor.py  # 移动端监控核心 (新增)
├── static/
│   └── index.html         # 前端单页应用
└── models/
    └── db_models.py       # 数据库模型
```

### 3.2 Agent 服务

Agent 运行在施压节点上，接收 Manager 的任务指令并执行 JMeter。

```
agent/
├── main.py                # Agent 主程序
├── jmeter_runner.py       # JMeter 执行器
└── requirements.txt       # Agent 依赖
```

### 3.3 移动端监控模块 (新增)

支持 iOS 和 Android 双引擎的移动端性能监控。

```
┌─────────────────────────────────────────────────────────────────┐
│                    移动端监控架构                                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │                  前端 (index.html)                       │   │
│   │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐     │   │
│   │  │  设备检测    │  │  应用选择    │  │  实时图表   │     │   │
│   │  │  自动识别    │  │  74个应用    │  │  CPU/内存   │     │   │
│   │  └─────────────┘  └─────────────┘  └─────────────┘     │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │               API 层 (/api/mobile/*)                     │   │
│   │  • GET  /detect        自动检测设备平台                    │   │
│   │  • GET  /status        服务状态检查                       │   │
│   │  • GET  /apps/{platform}  获取已安装应用列表              │   │
│   │  • POST /collect/all   采集所有性能指标                   │   │
│   │  • GET  /config        获取配置信息                       │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│   ┌─────────────────────────────────────────────────────────┐   │
│   │              核心层 (mobile_monitor.py)                   │   │
│   │  ┌─────────────┐              ┌─────────────┐           │   │
│   │  │ AndroidMonitor│             │  IOSMonitor  │           │   │
│   │  │  (SOLOX API) │             │(pymobiledev3)│           │   │
│   │  └─────────────┘              └─────────────┘           │   │
│   └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│            ┌─────────────────┴─────────────────┐                │
│            ▼                                   ▼                │
│   ┌─────────────────┐               ┌─────────────────┐        │
│   │   SOLOX 服务    │               │ pymobiledevice3  │        │
│   │  (Android/iOS)  │               │    (iOS CLI)     │        │
│   │  :50001         │               │                  │        │
│   └─────────────────┘               └─────────────────┘        │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

#### iOS 监控原理

```
iPhone (USB) → pymobiledevice3 → sysmon process single --userspace
                                       ↓
                                  JSON 数据
                                       ↓
                              CPU: cpuUsage (%)
                              Memory: physFootprint (bytes → MB)
                              PID: pid
```

#### Android 监控原理

```
Android (USB) → SOLOX 服务 (:50001) → HTTP API
                                       ↓
                                  JSON 数据
                                       ↓
                              CPU: cpuUsage (%)
                              Memory: total (MB)
                              FPS: fps (Hz)
                              Network: upload/download (KB)
                              Battery: level (%)
```

---

## 四、API 接口

### 4.1 核心 API

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/health | GET | 健康检查 |
| /api/tasks/ | GET | 任务列表 |
| /api/tasks/quick-run | POST | 快速执行压测 |
| /api/scripts/ | GET | 脚本列表 |
| /api/results/tasks | GET | 结果列表 |
| /api/results/trend | GET | 性能趋势 |
| /api/results/compare | GET | 任务对比 |

### 4.2 移动端监控 API (新增)

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/mobile/status | GET | 检查 SOLOX/pymobiledevice3 状态 |
| /api/mobile/detect | GET | 自动检测设备平台和信息 |
| /api/mobile/devices | POST | 获取已连接设备列表 |
| /api/mobile/apps/{platform} | GET | 获取已安装应用列表 |
| /api/mobile/collect | POST | 采集单个性能指标 |
| /api/mobile/collect/all | POST | 采集所有性能指标 |
| /api/mobile/config | GET | 获取配置信息 |

### 4.3 移动端监控数据格式

```json
{
  "cpu": {"cpu": 16.14},
  "memory": {"total": 607.05},
  "pid": "6139",
  "name": "WeChat"
}
```

---

## 五、目录结构

```
PerformanceTestingPlatform/
├── common/                     # 公共模块
│   ├── config.py              # 全局配置（Redis、JMeter、SOLOX等）
│   ├── protocol.py            # 通信协议
│   └── logger.py              # 统一日志模块
│
├── manager/                    # Manager 服务
│   ├── main.py                # FastAPI 应用入口
│   ├── api/                   # API 路由层（14个模块）
│   ├── core/                  # 核心业务逻辑
│   │   ├── mobile_monitor.py  # 移动端监控核心 (新增)
│   │   └── ...
│   ├── static/
│   │   └── index.html         # 前端单页应用
│   ├── models/
│   │   └── db_models.py       # 数据库模型
│   └── requirements.txt       # Manager 依赖
│
├── agent/                      # Agent 服务（执行节点）
│   ├── main.py                # Agent 主程序
│   ├── jmeter_runner.py       # JMeter 执行器
│   └── requirements.txt       # Agent 依赖
│
├── scripts/                    # JMeter 脚本目录
├── reports/                    # 压测报告目录
├── config/                     # 配置文件
│   ├── nodes.json             # 已注册节点
│   ├── variables.json         # 全局变量
│   ├── templates.json         # 自定义模板
│   └── csv/                   # CSV 数据文件
│
├── logs/                       # 日志目录
├── cli.py                      # CLI 命令行工具
├── deploy.sh                   # 一键部署脚本
├── start-slave.sh              # Slave 启动脚本
│
├── DEPLOY.md                   # 部署手册
├── DEPLOY-GUIDE.md             # 详细部署指南
├── ARCHITECTURE.md             # 本文档（系统架构手册）
│
└── apache-jmeter-5.6.3/        # JMeter（已内置）
```

---

## 六、配置说明

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| REDIS_HOST | 127.0.0.1 | Redis 主机 |
| REDIS_PORT | 6379 | Redis 端口 |
| REDIS_DB | 0 | Redis 数据库 |
| JMETER_HOME | ./apache-jmeter-5.6.3 | JMeter 安装路径 |
| SOLOX_HOST | 127.0.0.1 | SOLOX 服务主机 |
| SOLOX_PORT | 50001 | SOLOX 服务端口 |
| MAX_CONCURRENT_TASKS | 3 | 最大并发任务数 |
| TASK_TIMEOUT | 3600 | 任务超时时间（秒） |

### 6.2 Redis 频道

| 频道 | 说明 |
|------|------|
| jmeter:result | 压测结果频道 |
| jmeter:heartbeat | Agent 心跳频道 |
| jmeter:command | 命令下发频道 |
| jmeter:progress | 进度更新频道 |

---

## 七、部署架构

### 7.1 单机部署

```
┌─────────────────────────────────┐
│           单机模式               │
│  ┌──────────┐  ┌──────────┐    │
│  │ Manager  │  │  Agent   │    │
│  │  :8000   │  │  :9999   │    │
│  └──────────┘  └──────────┘    │
│  ┌──────────┐  ┌──────────┐    │
│  │  Redis   │  │  JMeter  │    │
│  │  :6379   │  │  Slave   │    │
│  └──────────┘  └──────────┘    │
└─────────────────────────────────┘
```

### 7.2 分布式部署

```
┌─────────────────────────────────────────────────────────┐
│                    管理节点                               │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ FastAPI  │  │  Redis   │  │  Web UI  │              │
│  │  :8000   │  │  :6379   │  │  :8000   │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└───────────────────────┬─────────────────────────────────┘
                        │
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   Agent 1     │ │   Agent 2     │ │   Agent N     │
│   :9999       │ │   :9999       │ │   :9999       │
├───────────────┤ ├───────────────┤ ├───────────────┤
│ Slave :1100   │ │ Slave :1200   │ │ Slave :1300   │
│ Slave :1101   │ │ Slave :1201   │ │ Slave :1301   │
└───────────────┘ └───────────────┘ └───────────────┘
```

---

## 八、移动端监控详细设计

### 8.1 支持平台

| 平台 | 引擎 | 采集指标 | 依赖 |
|------|------|----------|------|
| iOS | pymobiledevice3 | CPU、内存、PID | USB连接、pymobiledevice3 |
| Android | SOLOX | CPU、内存、FPS、网络、电池、GPU | USB连接、SOLOX服务 |

### 8.2 iOS 监控限制

- 需要 iOS 17+ 支持 `--userspace` 模式（无需 sudo）
- `sysmon process single` 命令首次调用约需 20 秒
- 不支持 FPS、网络、电池等指标
- 进程名匹配需要通过 `CFBundleExecutable` 字段

### 8.3 异步架构

为避免阻塞 FastAPI 事件循环，所有 pymobiledevice3 调用使用 `run_in_executor` 异步执行：

```python
@staticmethod
async def _run_cmd_async(cmd: List[str], timeout: int = 15) -> Optional[str]:
    import asyncio
    return await asyncio.get_event_loop().run_in_executor(
        None, IOSMonitor._run_cmd, cmd, timeout
    )
```

### 8.4 进程查找策略

```
1. 尝试 pgrep <app_name> --userspace
   ↓ 失败
2. 获取所有进程列表 (ps --userspace)
   ↓ 遍历匹配
3. 匹配规则:
   - execName 包含 app_name
   - comm 包含 app_name
   - name 包含 app_name
   - bundle_id 以 .<comm> 结尾
```

---

## 九、关键流程

### 9.1 任务执行流程

```
1. 用户创建任务 → Web UI
2. 提交到 API → /api/tasks/quick-run
3. 任务调度器分配节点 → scheduler.py
4. 通过 Redis 下发命令 → jmeter:command 频道
5. Agent 接收命令 → agent/main.py
6. 启动 JMeter 执行 → jmeter_runner.py
7. 实时推送进度 → jmeter:progress 频道
8. 执行完成，收集结果 → jmeter:result 频道
9. 生成报告 → reports/
10. 通知用户 → WebSocket + Webhook
```

### 9.2 移动端监控流程

```
1. 页面加载 → autoDetectDevice()
2. 自动检测设备平台 → /api/mobile/detect
3. 加载设备列表和应用列表 → /api/mobile/apps/{platform}
4. 用户选择应用 → onAppSelect()
5. 点击开始监控 → startMobileMonitor()
6. 定时采集数据 → /api/mobile/collect/all (每5秒)
7. 后端调用 pymobiledevice3/SOLOX
8. 返回 CPU/内存数据
9. 更新 ECharts 图表
```

---

## 十、性能指标

| 指标 | 目标值 | 说明 |
|------|--------|------|
| API 响应时间 | < 100ms | 不含移动端采集 |
| 移动端采集延迟 | < 30s | iOS sysmon 首次调用 |
| WebSocket 推送延迟 | < 1s | 实时进度更新 |
| 最大并发任务数 | 3-10 | 可配置 |
| 历史数据查询 | < 500ms | PostgreSQL |
| 图表渲染 | < 100ms | ECharts |

---

## 十一、安全考虑

1. **Redis 安全**：生产环境配置密码、限制访问 IP
2. **网络安全**：使用防火墙限制端口访问
3. **数据安全**：定期备份脚本和报告
4. **访问控制**：生产环境建议添加认证机制
5. **日志管理**：定期清理过期日志和报告

---

## 十二、演进方向

| 阶段 | 内容 | 优先级 |
|------|------|--------|
| 近期 | 性能基线与回归检测 | 高 |
| 近期 | CI/CD 门禁集成 | 高 |
| 中期 | 压力机资源池 | 中 |
| 中期 | 多引擎支持（k6/Locust） | 中 |
| 远期 | AI 辅助诊断 | 低 |
| 远期 | 全链路压测 | 低 |

---

## 十三、维护记录

- 2026-07-04：初始架构设计，搭建基础框架
- 2026-07-05：完善任务管理、脚本管理、结果分析
- 2026-07-06：新增模板管理、定时调度、告警规则
- 2026-07-07：新增移动端性能监控（iOS + Android 双引擎）

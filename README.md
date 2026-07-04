# 性能测试平台

基于 Apache JMeter 的分布式性能测试平台，提供 Web 界面管理压测任务、分析结果、监控资源。

---

## 技术架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户浏览器                                 │
│                    http://管理节点IP:8000                         │
└───────────────────────────┬─────────────────────────────────────┘
                            │ HTTP / WebSocket
┌───────────────────────────▼─────────────────────────────────────┐
│                      Manager 服务                                 │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │
│  │ 任务管理  │ │ 脚本编辑  │ │ 结果分析  │ │ 节点管理  │           │
│  │ 模板引擎  │ │ 定时调度  │ │ 通知系统  │ │ 资源监控  │           │
│  │ 告警规则  │ │ 环境管理  │ │ JTL对比  │ │ 日志系统  │           │
│  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │
└───────┬───────────────────┬───────────────────┬─────────────────┘
        │ Redis             │ Redis             │ Redis PubSub
┌───────▼───────┐  ┌───────▼───────┐  ┌───────▼─────────────────┐
│     Redis     │  │   Agent 1     │  │   Agent 2 / N           │
│   :6379       │  │   (施压机1)    │  │   (施压机2/N)            │
│ 消息队列+持久化 │  │   :9999       │  │   :9999                 │
└───────────────┘  └───────┬───────┘  └───────┬─────────────────┘
                           │                   │
                    ┌──────▼──────┐     ┌──────▼──────┐
                    │ JMeter Slave│     │ JMeter Slave│
                    │   :1100     │     │   :1200     │
                    └─────────────┘     └─────────────┘
```

### 技术栈

| 层级 | 技术 | 版本 | 说明 |
|------|------|------|------|
| **前端** | HTML/CSS/JS | - | 原生单页应用，无框架依赖 |
| **图表** | ECharts | 5.4.3 | 实时曲线、对比图表、趋势分析 |
| **后端** | Python + FastAPI | 3.10+ | 高性能异步 API 框架 |
| **消息队列** | Redis | 6.0+ | PubSub 实时通信 + 数据持久化 |
| **压测引擎** | Apache JMeter | 5.6.3 | 行业标准压测工具 |
| **通信协议** | Redis PubSub | - | Manager ↔ Agent 实时通信 |

### 数据流

```
用户操作 → Web UI → FastAPI API → Redis PubSub → Agent → JMeter
                                                         ↓
用户看到 ← WebSocket ← FastAPI ← Redis PubSub ← Agent ← 执行结果
```

---

## 功能清单

### 核心功能

| 功能 | 说明 | 状态 |
|------|------|------|
| 任务管理 | 创建/启动/停止/删除/重跑/批量创建 | ✅ |
| 脚本管理 | 上传/编辑/搜索/树形视图/拖拽排序 | ✅ |
| 结果分析 | TPS/RT/错误率曲线、分布图、百分位 | ✅ |
| 节点管理 | Agent 状态监控、心跳检测 | ✅ |
| 数据管理 | 全局变量、CSV 文件管理（10万+行） | ✅ |
| 实时监控 | CPU/内存/网络/进程监控 | ✅ |
| 报告导出 | HTML 报告 / PDF 报告 | ✅ |

### 高级功能

| 功能 | 说明 | 状态 |
|------|------|------|
| 并发场景配置 | 11种预设场景 + 自定义构建器 + 实时曲线预览 | ✅ |
| CSV 参数化 | 选择CSV文件自动注入JMeter，数据预览 | ✅ |
| JTL 对比分析 | 上传JTL文件，多接口性能曲线对比 | ✅ |
| 测试计划模板 | 8个内置场景 + 自定义模板 | ✅ |
| 定时调度 | 间隔执行/单次定时 | ✅ |
| 告警规则 | 阈值检测 + Webhook 通知 | ✅ |
| 多环境管理 | dev/staging/prod 配置管理 | ✅ |
| 任务对比 | 两次压测结果性能对比 | ✅ |
| 历史趋势 | 同一接口多次压测趋势分析 | ✅ |
| 脚本历史 | 选择脚本时显示历史压测配置 | ✅ |
| 日志系统 | Manager/Agent/API/任务分级日志 | ✅ |

### 脚本编辑功能

| 功能 | 说明 |
|------|------|
| 树形视图 | 可视化展示 JMeter 组件层级结构 |
| 展开/收起 | 支持全部展开、全部收起、单个节点展开 |
| 拖拽排序 | 同层级组件可拖拽调整顺序 |
| 详情面板 | 点击节点显示 HTTP方法/URL/请求体/请求头/参数 |
| 代码视图 | 切换到原始 XML 代码编辑 |
| 模糊搜索 | 按文件名和接口关键词搜索脚本 |

### 并发场景

| 场景 | 并发数 | 时长 | 适用场景 |
|------|--------|------|----------|
| 冒烟测试 | 5 | 1分钟 | 上线前快速验证 |
| 负载测试 | 100 | 10分钟 | 评估系统容量 |
| 压力测试 | 500 | 15分钟 | 找性能瓶颈 |
| 尖刺测试 | 1000 | 2分钟 | 模拟秒杀 |
| 阶梯加压 | 200 | 10分钟 | 观察性能拐点 |
| 峰值测试 | 300 | 30分钟 | 高并发持续 |
| 稳定性测试 | 80 | 2小时 | 检测内存泄漏 |
| 尖刺恢复 | 800 | 5分钟 | 测试恢复能力 |
| 加压-保持-冷却 | 200 | 10分钟 | 模拟业务波峰 |
| 双峰测试 | 300 | 10分钟 | 两个连续高峰 |
| 自定义 | 自定义 | 自定义 | 完全自定义 |

---

## 项目结构

```
Performance Testing Platform/
├── common/                     # 公共模块
│   ├── config.py              # 全局配置（Redis、JMeter路径、频道等）
│   ├── protocol.py            # 通信协议（任务状态、命令、结果等数据结构）
│   └── logger.py              # 统一日志模块
│
├── manager/                    # Manager 服务（FastAPI）
│   ├── main.py                # FastAPI 应用入口
│   ├── api/                   # API 路由层（13个模块）
│   │   ├── tasks.py           # 任务管理
│   │   ├── scripts.py         # 脚本管理（含搜索/排序）
│   │   ├── results.py         # 结果分析（含JTL对比/PDF导出）
│   │   ├── nodes.py           # 节点管理
│   │   ├── data.py            # 数据管理（变量/CSV）
│   │   ├── monitor.py         # 系统监控
│   │   ├── slave.py           # JMeter Slave 控制
│   │   ├── registry.py        # 远程节点注册
│   │   ├── templates.py       # 测试计划模板
│   │   ├── notifications.py   # Webhook 通知
│   │   ├── scheduler_api.py   # 定时调度
│   │   ├── alerts.py          # 告警规则
│   │   ├── environments.py    # 多环境管理
│   │   └── jtl_compare.py     # JTL 文件对比
│   ├── core/                  # 核心业务逻辑
│   │   ├── scheduler.py       # 任务调度器
│   │   ├── node_manager.py    # Agent 节点管理
│   │   ├── node_registry.py   # 远程节点注册表
│   │   ├── monitor.py         # 系统资源监控
│   │   ├── slave_manager.py   # JMeter Slave 管理
│   │   ├── variables.py       # 变量和 CSV 管理
│   │   ├── sample_cache.py    # 采样数据缓存
│   │   └── ws.py              # WebSocket 管理
│   └── static/
│       └── index.html         # 前端单页应用
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
│   ├── script_counter.json    # 脚本 ID 计数器
│   ├── templates.json         # 自定义模板
│   └── csv/                   # CSV 数据文件
│
├── logs/                       # 日志目录
├── cli.py                      # CLI 命令行工具
├── deploy.sh                   # 一键部署脚本
├── start-slave.sh              # Slave 启动脚本
├── .github/workflows/          # GitHub Actions
│
├── README.md                   # 项目说明
├── DEPLOY.md                   # 部署手册
├── DEPLOY-GUIDE.md             # 详细部署指南
├── HARDWARE-GUIDE.md           # 硬件配置指南
├── LOG-GUIDE.md                # 日志查看指南
│
└── apache-jmeter-5.6.3/        # JMeter（需单独下载）
```

---

## 快速开始

### 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | 3.10+ | Manager 和 Agent |
| Java | 8+ | JMeter 需要 |
| Redis | 6.0+ | 消息队列 |
| JMeter | 5.6.3 | 需要单独下载 |

### 一键部署

```bash
# 1. 克隆项目
git clone https://github.com/your-org/performance-testing-platform.git
cd performance-testing-platform

# 2. 下载 JMeter
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
tar -xzf apache-jmeter-5.6.3.tgz && rm apache-jmeter-5.6.3.tgz

# 3. 一键启动
bash deploy.sh
```

### 手动部署

```bash
# 安装依赖
pip install -r manager/requirements.txt
pip install -r agent/requirements.txt

# 启动 Redis
redis-server &

# 启动 Manager
python3 -m manager.main &

# 启动 Agent
python3 -m agent.main &

# 启动 Slave（可选）
bash start-slave.sh 3  # 启动3个Slave
```

---

## API 接口

| 接口 | 方法 | 说明 |
|------|------|------|
| /api/health | GET | 健康检查 |
| /api/tasks/ | GET | 任务列表 |
| /api/tasks/quick-run | POST | 快速执行压测 |
| /api/tasks/{id}/rerun | POST | 重新执行任务 |
| /api/scripts/ | GET | 脚本列表 |
| /api/scripts/upload | POST | 上传脚本 |
| /api/scripts/search?q= | GET | 搜索脚本 |
| /api/scripts/{id}/reorder | POST | 拖拽排序 |
| /api/results/tasks | GET | 结果列表 |
| /api/results/trend | GET | 性能趋势 |
| /api/results/compare | GET | 任务对比 |
| /api/results/jtl/upload | POST | JTL 文件上传 |
| /api/templates/ | GET | 模板列表 |
| /api/scheduler/ | GET | 调度列表 |
| /api/alerts/ | GET | 告警规则 |
| /api/environments/ | GET | 环境列表 |
| /api/data/csv | GET | CSV 文件列表 |
| /api/data/vars | GET | 变量列表 |
| /api/monitor/overview | GET | 系统监控 |
| /api/nodes/ | GET | Agent 列表 |
| /api/registry/ | GET | 注册节点 |

---

## CLI 工具

```bash
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
```

---

## 相关文档

| 文档 | 说明 |
|------|------|
| [DEPLOY.md](DEPLOY.md) | 部署手册 |
| [DEPLOY-GUIDE.md](DEPLOY-GUIDE.md) | 详细部署指南（含JMeter安装） |
| [HARDWARE-GUIDE.md](HARDWARE-GUIDE.md) | 硬件配置指南 |
| [LOG-GUIDE.md](LOG-GUIDE.md) | 日志查看指南 |

---

## 许可证

MIT License

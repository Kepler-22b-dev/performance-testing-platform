# 性能测试平台 - 部署手册

> 版本：1.1 | 更新时间：2026-06-18

## 一、环境要求

| 组件 | 要求 |
|------|------|
| Python | 3.10+ |
| Redis | 6.0+ |
| JMeter | 5.6.3（已内置） |
| 操作系统 | Linux / macOS |
| 最低内存 | 4GB（建议 8GB+） |

## 二、目录结构

```
Performance Testing Platform/
├── common/                    # 公共模块
│   ├── config.py             # 全局配置
│   └── protocol.py           # 通信协议
├── manager/                   # 管理服务
│   ├── main.py               # FastAPI 入口
│   ├── api/                  # API 路由
│   │   ├── data.py           # 数据管理（变量/CSV）
│   │   ├── monitor.py        # 资源监控
│   │   ├── nodes.py          # Agent 节点
│   │   ├── registry.py       # 节点注册
│   │   ├── results.py        # 结果分析
│   │   ├── scripts.py        # 脚本管理
│   │   ├── slave.py          # Slave 控制
│   │   └── tasks.py          # 任务管理
│   ├── core/                 # 核心逻辑
│   │   ├── monitor.py        # 系统指标采集
│   │   ├── node_manager.py   # 节点状态管理
│   │   ├── node_registry.py  # 节点注册中心
│   │   ├── scheduler.py      # 任务调度
│   │   ├── slave_manager.py  # Slave 进程管理
│   │   ├── variables.py      # 变量/CSV 管理
│   │   └── ws.py             # WebSocket
│   ├── static/
│   │   └── index.html        # 前端页面
│   └── requirements.txt      # Python 依赖
├── agent/                     # 施压节点
│   ├── main.py               # Agent 入口
│   ├── jmeter_runner.py      # JMeter 执行器
│   └── requirements.txt
├── config/                    # 配置数据（自动生成）
│   ├── nodes.json            # 已注册节点
│   ├── variables.json        # 全局变量
│   └── csv/                  # CSV 数据文件
├── scripts/                   # JMeter 脚本
├── reports/                   # 测试报告
├── apache-jmeter-5.6.3/      # Master JMeter
├── apache-jmeter-5.6.3-slave/ # Slave JMeter (端口 1100)
├── apache-jmeter-5.6.3-slave2/ # Slave2 JMeter (端口 1200)
├── apache-jmeter-5.6.3-slave3/ # Slave3 JMeter (端口 1300)
└── start-slave.sh            # Slave 启动脚本
```

## 三、快速部署

### 3.1 安装依赖

```bash
cd "Performance Testing Platform"

# 安装 Redis（macOS）
brew install redis
brew services start redis

# 或 Ubuntu
sudo apt install redis-server
sudo systemctl start redis

# 安装 Python 依赖
pip install -r manager/requirements.txt
pip install -r agent/requirements.txt
```

### 3.2 配置 JMeter

```bash
# 创建无空格路径的符号链接（JMeter 不支持路径含空格）
ln -sf "$(pwd)/apache-jmeter-5.6.3" /tmp/jmeter
ln -sf "$(pwd)/apache-jmeter-5.6.3-slave" /tmp/jmeter-slave
ln -sf "$(pwd)/apache-jmeter-5.6.3-slave2" /tmp/jmeter-slave2
ln -sf "$(pwd)/apache-jmeter-5.6.3-slave3" /tmp/jmeter-slave3
```

### 3.3 启动服务

```bash
# 终端 1：启动管理服务
python3 -m manager.main
# 访问 http://localhost:8000

# 终端 2：启动 Agent（本机施压节点）
python3 -m agent.main

# 终端 3：启动 Slave（分布式模式需要）
./start-slave.sh        # 端口 1100
./start-slave.sh 1200   # 端口 1200
./start-slave.sh 1300   # 端口 1300
```

### 3.4 验证部署

```bash
# 检查服务状态
curl http://localhost:8000/api/health

# 检查节点状态
curl http://localhost:8000/api/nodes/

# 检查 Slave 状态
curl http://localhost:8000/api/slave/status
```

## 四、端口说明

| 端口 | 用途 | 进程 |
|------|------|------|
| 8000 | Web 管理界面 + API | manager.main |
| 6379 | Redis | redis-server |
| 1100 | JMeter Slave1 | jmeter-server |
| 1200 | JMeter Slave2 | jmeter-server |
| 1300 | JMeter Slave3 | jmeter-server |
| 9999 | Agent 心跳 | agent.main |

## 五、分布式部署

### 5.1 单机多 Slave

在同一台机器上运行多个 Slave，使用不同端口：

```bash
./start-slave.sh 1100 &
./start-slave.sh 1200 &
./start-slave.sh 1300 &
```

### 5.2 跨机器部署

在远程机器上：

```bash
# 1. 复制 JMeter 到远程机器
scp -r apache-jmeter-5.6.3-slave user@remote:/opt/jmeter-slave

# 2. 配置 Slave
cd /opt/jmeter-slave
sed -i 's/^server_port=1100/server_port=1100/' bin/jmeter.properties
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' bin/jmeter.properties

# 3. 启动 Slave
/opt/jmeter-slave/bin/jmeter-server \
    -Dserver_port=1100 \
    -Dserver.rmi.ssl.disable=true \
    -Djava.rmi.server.hostname=<远程机器IP>
```

在管理平台：

```
1. 进入「施压节点」页面
2. 添加远程节点：输入 IP 和端口
3. 点击「验证」确认连通
4. 创建分布式任务时勾选「分布式模式」
```

## 六、配置说明

### 6.1 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| REDIS_HOST | 127.0.0.1 | Redis 地址 |
| REDIS_PORT | 6379 | Redis 端口 |
| JMETER_HOME | /tmp/jmeter | Master JMeter 路径 |
| JMETER_SLAVE_HOME | /tmp/jmeter-slave | Slave JMeter 路径 |
| SLAVE_PORT | 1100 | 默认 Slave 端口 |
| AGENT_HEARTBEAT_INTERVAL | 5 | 心跳间隔(秒) |
| MAX_CONCURRENT_TASKS | 3 | 最大并行任务数 |

### 6.2 JMeter 配置

修改 `apache-jmeter-5.6.3/bin/jmeter.properties`：

```properties
# Master 连接的 Slave 列表（自动由平台管理）
remote_hosts=192.168.1.100:1100,192.168.1.101:1100

# 禁用 SSL（分布式调试必须）
server.rmi.ssl.disable=true
```

## 七、常见问题

### 7.1 JMeter 启动报 "loopback address" 错误

**原因**：路径含空格或绑定到 127.0.0.1

**解决**：
```bash
# 创建无空格符号链接
ln -sf /path/to/apache-jmeter-5.6.3 /tmp/jmeter

# 启动时指定实际 IP
jmeter-server -Djava.rmi.server.hostname=192.168.1.100
```

### 7.2 分布式连接失败

**检查清单**：
```bash
# 1. Slave 端口是否监听
lsof -i :1100

# 2. RMI 端口是否可达
telnet 192.168.1.100 1100

# 3. SSL 是否禁用
grep "ssl.disable" jmeter.properties

# 4. Master remote_hosts 是否包含 Slave
grep "remote_hosts" jmeter.properties

# 5. 查看 Slave 日志
tail -f /tmp/slave.log
```

### 7.3 Redis 连接失败

```bash
# 检查 Redis 状态
redis-cli ping

# 启动 Redis
redis-server --daemonize yes
```

### 7.4 端口被占用

```bash
# 查找占用端口的进程
lsof -i :8000

# 杀掉进程
kill -9 <PID>
```

## 八、停止服务

```bash
# 停止所有服务
pkill -f "manager.main"
pkill -f "agent.main"
pkill -f "jmeter-server"
pkill -f "ApacheJMeter.jar"

# 清空 Redis
redis-cli FLUSHDB
```

## 九、API 速查

| 模块 | 接口 | 说明 |
|------|------|------|
| 健康检查 | `GET /api/health` | 服务状态 |
| 首页 | `GET /api/tasks/` | 任务列表（首页统计） |
| 任务 | `POST /api/tasks/quick-run` | 一键创建执行 |
| 任务 | `GET /api/tasks/` | 任务列表 |
| 任务 | `POST /api/tasks/{id}/start` | 启动任务 |
| 任务 | `POST /api/tasks/{id}/stop` | 停止任务 |
| 脚本 | `POST /api/scripts/upload` | 上传脚本 |
| 脚本 | `GET /api/scripts/{id}` | 获取脚本内容 |
| 脚本 | `POST /api/scripts/{id}/save` | 保存脚本 |
| 脚本 | `GET /api/scripts/{id}/structure` | 解析脚本结构 |
| 脚本 | `POST /api/scripts/create` | 新建脚本 |
| 节点 | `GET /api/nodes/` | Agent 列表 |
| 注册 | `POST /api/registry/` | 注册节点 |
| 注册 | `GET /api/registry/` | 已注册节点列表 |
| 注册 | `POST /api/registry/{id}/verify` | 验证节点 |
| 注册 | `POST /api/registry/verify-all` | 验证全部节点 |
| Slave | `GET /api/slave/status` | Slave 状态 |
| Slave | `POST /api/slave/start` | 启动 Slave |
| Slave | `POST /api/slave/stop` | 停止 Slave |
| 监控 | `GET /api/monitor/overview` | 资源概览 |
| 监控 | `GET /api/monitor/system` | 系统指标 |
| 监控 | `GET /api/monitor/jmeter` | JMeter 进程 |
| 数据 | `GET /api/data/vars` | 变量列表 |
| 数据 | `POST /api/data/vars` | 添加变量 |
| 数据 | `GET /api/data/csv` | CSV 列表 |
| 数据 | `POST /api/data/csv/upload` | 上传 CSV |
| 数据 | `GET /api/data/csv/{id}/data` | CSV 数据 |
| 结果 | `GET /api/results/tasks/{id}/summary` | 结果分析 |
| 结果 | `GET /api/results/tasks/{id}/logs` | 执行日志 |
| 结果 | `GET /api/results/tasks/{id}/export` | 导出 HTML 报告 |
| WebSocket | `ws://localhost:8000/ws` | 实时推送 |

## 十、功能清单

| 功能 | 说明 | 状态 |
|------|------|------|
| 首页仪表盘 | 系统概览、最近任务、快速开始 | ✓ |
| 任务管理 | 创建/启动/停止/查看任务 | ✓ |
| 实时监控 | 运行中任务实时进度显示 | ✓ |
| 结果分析 | TPS/RT/错误率/百分位/分布图表 | ✓ |
| 脚本管理 | 上传/新建/编辑/保存/删除 | ✓ |
| 脚本编辑器 | 在线编辑 XML、格式化、一键执行 | ✓ |
| 分布式压测 | 多 Slave 并行执行 | ✓ |
| 节点注册 | 添加/验证/删除远程节点 | ✓ |
| 资源监控 | CPU/内存/磁盘/网络/JMeter 进程 | ✓ |
| 变量管理 | 全局变量配置 | ✓ |
| CSV 管理 | 数据文件上传、预览 | ✓ |
| 执行日志 | 各节点 JMeter 日志查看 | ✓ |
| 报告导出 | HTML 格式测试报告 | ✓ |
| 一键部署 | deploy.sh 自动化部署 | ✓ |

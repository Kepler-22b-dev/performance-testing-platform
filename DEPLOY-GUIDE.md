# 性能测试平台 - 分布式部署指南

> 版本：3.0 | 更新时间：2026-06-20

---

## 目录

- [一、架构概览](#一架构概览)
- [二、环境准备](#二环境准备)
- [三、JMeter 安装](#三jmeter-安装)
- [四、单机部署](#四单机部署)
- [五、分布式部署](#五分布式部署)
- [六、Docker 部署](#六docker-部署)
- [七、生产环境配置](#七生产环境配置)
- [八、监控与运维](#八监控与运维)
- [九、故障排查](#九故障排查)
- [十、性能调优](#十性能调优)

---

## 一、架构概览

### 系统架构图

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
                    │   :1100     │     │   :1100     │
                    └─────────────┘     └─────────────┘
```

### 角色说明

| 角色 | 作用 | 部署位置 | 资源需求 |
|------|------|----------|----------|
| **Manager** | Web 界面 + API + 任务调度 | 管理节点 | 2核4G+ |
| **Redis** | 消息队列 + 数据持久化 | 管理节点 | 1核2G+ |
| **Agent** | 接收命令、执行压测、上报结果 | 每台施压机 | 2核4G+ |
| **JMeter Slave** | 实际执行 JMeter 压测 | 每台施压机 | 视并发量 |

### 通信流程

```
1. 用户在 Web UI 创建任务
2. Manager 通过 Redis 发送命令给 Agent
3. Agent 接收命令，启动 JMeter Slave 执行压测
4. Agent 实时上报进度（TPS/RT/错误率）
5. Agent 执行完成后上报最终结果
6. Manager 处理结果，生成报告
7. Web UI 实时展示数据
```

---

## 二、环境准备

### 2.1 软件要求

| 软件 | 版本要求 | 说明 |
|------|----------|------|
| Python | 3.10+ | 运行 Manager 和 Agent |
| Java | 8+ | JMeter 运行需要 |
| Redis | 6.0+ | 消息队列和数据存储 |
| JMeter | 5.6.3 | 需要单独下载（见第三章） |

### 2.2 检查环境

```bash
# 检查 Python
python3 --version
# 需要 3.10+

# 检查 Java
java -version
# 需要 8+

# 检查 Redis
redis-cli ping
# 返回 PONG 表示正常
```

### 2.3 网络要求

| 端口 | 用途 | 方向 |
|------|------|------|
| 8000 | Web UI | 入站 |
| 6379 | Redis | Agent → Manager |
| 9999 | Agent | Manager → Agent |
| 1100+ | JMeter Slave | Agent → Slave |

---

## 三、JMeter 安装

JMeter 不在 Git 仓库中（文件太大），需要单独下载。

### 3.1 下载 JMeter

```bash
# 进入项目目录
cd performance-testing-platform

# 下载 JMeter 5.6.3
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz

# 解压
tar -xzf apache-jmeter-5.6.3.tgz

# 删除压缩包
rm apache-jmeter-5.6.3.tgz
```

### 3.2 配置 JMeter

```bash
# 禁用 SSL（分布式模式需要）
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' apache-jmeter-5.6.3/bin/jmeter.properties
```

### 3.3 验证 JMeter

```bash
# 检查 JMeter 版本
./apache-jmeter-5.6.3/bin/jmeter --version

# 检查 Java 连接
./apache-jmeter-5.6.3/bin/jmeter -n -t /dev/null 2>&1 | head -5
```

### 3.4 Slave 节点（可选）

如果需要分布式模式，为每台施压机准备 Slave：

```bash
# 复制 JMeter 作为 Slave
cp -r apache-jmeter-5.6.3 apache-jmeter-5.6.3-slave

# 配置 Slave 禁用 SSL
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' apache-jmeter-5.6.3-slave/bin/jmeter.properties
```

### 3.5 目录结构（下载后）

```
performance-testing-platform/
├── apache-jmeter-5.6.3/          ← 主 JMeter（必须）
├── apache-jmeter-5.6.3-slave/    ← Slave 节点（可选）
├── common/
├── manager/
├── agent/
├── scripts/
├── reports/
├── config/
├── cli.py
├── deploy.sh
└── ...
```

---

## 四、单机部署

**适用场景：** 个人使用、功能测试、小规模压测

### 步骤 1：获取代码

```bash
git clone https://github.com/Kepler-22b-dev/performance-testing-platform.git
cd performance-testing-platform
```

### 步骤 2：安装 JMeter

```bash
# 下载 JMeter 5.6.3
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz

# 解压
tar -xzf apache-jmeter-5.6.3.tgz

# 删除压缩包
rm apache-jmeter-5.6.3.tgz

# 禁用 SSL（分布式模式需要）
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' apache-jmeter-5.6.3/bin/jmeter.properties

# 验证
./apache-jmeter-5.6.3/bin/jmeter --version
```

### 步骤 3：安装依赖

```bash
pip install -r manager/requirements.txt
pip install -r agent/requirements.txt
```

### 步骤 4：启动服务

```bash
# 一键启动所有服务
bash deploy.sh start

# 或手动启动
# 终端 1: 启动 Redis
redis-server

# 终端 2: 启动 Manager
python3 -m manager.main

# 终端 3: 启动 Agent
python3 -m agent.main
```

### 步骤 4：验证

```bash
# 检查服务状态
bash deploy.sh status

# 访问 Web UI
open http://localhost:8000
```

### 常用命令

```bash
bash deploy.sh start    # 启动
bash deploy.sh stop     # 停止
bash deploy.sh restart  # 重启
bash deploy.sh status   # 状态
bash deploy.sh clean    # 清理数据
```

---

## 四、分布式部署

**适用场景：** 多台机器协作压测、生产环境

### 4.1 部署规划

假设使用 4 台机器：

| 机器 | IP | 角色 | 配置 |
|------|-----|------|------|
| 机器 A | 192.168.1.100 | 管理节点 | 4核8G |
| 机器 B | 192.168.1.101 | 施压节点 1 | 4核8G |
| 机器 C | 192.168.1.102 | 施压节点 2 | 4核8G |
| 机器 D | 192.168.1.103 | 施压节点 3 | 4核8G |

### 4.2 管理节点部署（机器 A）

```bash
# 1. 获取代码
git clone https://github.com/Kepler-22b-dev/performance-testing-platform.git
cd performance-testing-platform

# 2. 安装 JMeter
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
tar -xzf apache-jmeter-5.6.3.tgz
rm apache-jmeter-5.6.3.tgz
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' apache-jmeter-5.6.3/bin/jmeter.properties

# 3. 安装依赖
pip install -r manager/requirements.txt

# 4. 安装并启动 Redis
# Ubuntu/Debian:
sudo apt update && sudo apt install -y redis-server
sudo systemctl start redis
sudo systemctl enable redis

# CentOS/RHEL:
sudo yum install -y redis
sudo systemctl start redis
sudo systemctl enable redis

# macOS:
brew install redis && brew services start redis

# 4. 配置 Redis 允许远程连接
sudo vi /etc/redis/redis.conf
# 修改: bind 0.0.0.0
# 修改: requirepass your_password  (可选，建议设置)

# 5. 重启 Redis
sudo systemctl restart redis

# 6. 启动 Manager
nohup python3 -m manager.main > manager.log 2>&1 &

# 7. 验证
curl http://localhost:8000/api/health
# 返回: {"status":"ok"}
```

### 4.3 施压节点部署（机器 B/C/D）

在**每台施压机**上执行：

```bash
# 1. 获取代码
git clone https://github.com/Kepler-22b-dev/performance-testing-platform.git
cd performance-testing-platform

# 2. 安装 JMeter
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
tar -xzf apache-jmeter-5.6.3.tgz
rm apache-jmeter-5.6.3.tgz
sed -i 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' apache-jmeter-5.6.3/bin/jmeter.properties

# 3. 安装依赖
pip install -r agent/requirements.txt

# 4. 配置 Redis 连接
# 方法 1: 环境变量
export REDIS_HOST=192.168.1.100

# 方法 2: 写入 .bashrc（永久生效）
echo 'export REDIS_HOST=192.168.1.100' >> ~/.bashrc
source ~/.bashrc

# 5. 启动 Agent
nohup python3 -m agent.main > agent.log 2>&1 &

# 6. 启动 JMeter Slave
nohup apache-jmeter-5.6.3/bin/jmeter-server \
    -Dserver_port=1100 \
    -Dserver.rmi.ssl.disable=true \
    -Djava.rmi.server.hostname=当前机器IP \
    > slave.log 2>&1 &

# 7. 验证
cat agent.log

# 8. 可选：启动多个 Slave 增加并发
nohup apache-jmeter-5.6.3/bin/jmeter-server \
    -Dserver_port=1101 \
    -Dserver.rmi.ssl.disable=true \
    -Djava.rmi.server.hostname=当前机器IP \
    > slave2.log 2>&1 &
```

### 4.4 防火墙配置

```bash
# 管理节点（机器 A）
sudo firewall-cmd --add-port=8000/tcp --permanent
sudo firewall-cmd --add-port=6379/tcp --permanent
sudo firewall-cmd --reload

# 施压节点（机器 B/C/D）
sudo firewall-cmd --add-port=9999/tcp --permanent
sudo firewall-cmd --add-port=1100-1101/tcp --permanent
sudo firewall-cmd --reload

# 或者关闭防火墙（测试环境）
sudo systemctl stop firewalld
```

### 4.5 Web UI 注册节点

1. 浏览器打开 `http://192.168.1.100:8000`
2. 点击顶部导航「施压节点」
3. 在「添加远程节点」区域：

| 节点名称 | IP 地址 | 端口 |
|----------|---------|------|
| 施压机1 | 192.168.1.101 | 1100 |
| 施压机2 | 192.168.1.102 | 1100 |
| 施压机3 | 192.168.1.103 | 1100 |

4. 依次点击「添加」
5. 点击「全部验证」
6. 状态显示「已验证」表示连通正常

### 4.6 执行分布式压测

**方式 1：Web UI**
1. 进入「创建任务」页面
2. 选择脚本
3. 设置并发线程数、持续时间
4. **勾选「分布式模式」**
5. 点击「启动压测」

**方式 2：CLI 工具**
```bash
python cli.py run \
    --script 8 \
    --threads 100 \
    --duration 300 \
    --distributed
```

**方式 3：API 调用**
```bash
curl -X POST http://192.168.1.100:8000/api/tasks/quick-run \
  -H "Content-Type: application/json" \
  -d '{
    "script_id": "8",
    "threads": 100,
    "duration": 300,
    "distributed": true
  }'
```

---

## 五、Docker 部署

### 5.1 创建 Dockerfile.manager

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装 Java
RUN apt-get update && \
    apt-get install -y default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

COPY manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ /app/common/
COPY manager/ /app/manager/
COPY scripts/ /app/scripts/
COPY config/ /app/config/
COPY apache-jmeter-5.6.3/ /app/apache-jmeter-5.6.3/

EXPOSE 8000

CMD ["python3", "-m", "manager.main"]
```

### 5.2 创建 Dockerfile.agent

```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装 Java
RUN apt-get update && \
    apt-get install -y default-jre-headless && \
    rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ /app/common/
COPY agent/ /app/agent/
COPY apache-jmeter-5.6.3/ /app/apache-jmeter-5.6.3/

CMD ["python3", "-m", "agent.main"]
```

### 5.3 创建 docker-compose.yml

```yaml
version: '3.8'

services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    volumes:
      - redis_data:/data
    restart: always

  manager:
    build:
      context: .
      dockerfile: Dockerfile.manager
    ports:
      - "8000:8000"
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    volumes:
      - ./scripts:/app/scripts
      - ./reports:/app/reports
      - ./config:/app/config
    restart: always

  agent:
    build:
      context: .
      dockerfile: Dockerfile.agent
    depends_on:
      - redis
    environment:
      - REDIS_HOST=redis
      - REDIS_PORT=6379
    volumes:
      - ./scripts:/app/scripts
      - ./reports:/app/reports
    restart: always
    # 可以启动多个 agent 实例
    # deploy:
    #   replicas: 3

volumes:
  redis_data:
```

### 5.4 启动

```bash
# 构建并启动
docker-compose up -d

# 查看日志
docker-compose logs -f

# 停止
docker-compose down
```

---

## 六、生产环境配置

### 6.1 systemd 服务（推荐）

**Manager 服务：**

```ini
# /etc/systemd/system/perftest-manager.service
[Unit]
Description=Performance Test Platform Manager
After=network.target redis.service

[Service]
Type=simple
User=perftest
Group=perftest
WorkingDirectory=/opt/performance-testing-platform
ExecStart=/usr/bin/python3 -m manager.main
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**Agent 服务：**

```ini
# /etc/systemd/system/perftest-agent.service
[Unit]
Description=Performance Test Platform Agent
After=network.target redis.service

[Service]
Type=simple
User=perftest
Group=perftest
WorkingDirectory=/opt/performance-testing-platform
ExecStart=/usr/bin/python3 -m agent.main
Restart=always
RestartSec=5
Environment=REDIS_HOST=192.168.1.100
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

**启用服务：**

```bash
# 创建用户
sudo useradd -r -s /bin/false perftest
sudo chown -R perftest:perftest /opt/performance-testing-platform

# 重载配置
sudo systemctl daemon-reload

# 启动服务
sudo systemctl start perftest-manager
sudo systemctl start perftest-agent

# 设置开机自启
sudo systemctl enable perftest-manager
sudo systemctl enable perftest-agent

# 查看状态
sudo systemctl status perftest-manager
sudo systemctl status perftest-agent

# 查看日志
sudo journalctl -u perftest-manager -f
sudo journalctl -u perftest-agent -f
```

### 6.2 Nginx 反向代理

```nginx
# /etc/nginx/sites-available/perftest
server {
    listen 80;
    server_name perftest.yourdomain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
    }
}
```

### 6.3 Redis 安全配置

```conf
# /etc/redis/redis.conf
bind 0.0.0.0
requirepass your_strong_password
maxmemory 2gb
maxmemory-policy allkeys-lru
save 60 1000
```

---

## 七、监控与运维

### 7.1 内置监控

Web UI 提供实时监控：
- **压力机资源监控**：CPU、内存、网络、磁盘
- **JMeter 进程监控**：进程状态、资源占用
- **任务实时数据**：TPS、响应时间、错误率

访问路径：施压节点 → 压力机资源监控

### 7.2 健康检查脚本

```bash
#!/bin/bash
# health_check.sh

echo "=== 性能测试平台健康检查 ==="

# 检查 Manager
if curl -s http://localhost:8000/api/health | grep -q "ok"; then
    echo "✓ Manager: 正常"
else
    echo "✗ Manager: 异常"
fi

# 检查 Redis
if redis-cli ping | grep -q "PONG"; then
    echo "✓ Redis: 正常"
else
    echo "✗ Redis: 异常"
fi

# 检查 Agent
if pgrep -f "agent.main" > /dev/null; then
    echo "✓ Agent: 运行中"
else
    echo "✗ Agent: 未运行"
fi

# 检查 JMeter
if pgrep -f "jmeter-server" > /dev/null; then
    echo "✓ JMeter Slave: 运行中"
else
    echo "✗ JMeter Slave: 未运行"
fi
```

### 7.3 定时备份

```bash
#!/bin/bash
# backup.sh

BACKUP_DIR="/backup/perftest/$(date +%Y%m%d)"
mkdir -p "$BACKUP_DIR"

# 备份脚本
cp -r scripts/ "$BACKUP_DIR/scripts/"

# 备份配置
cp -r config/ "$BACKUP_DIR/config/"

# 备份 Redis
redis-cli BGSAVE
cp /var/lib/redis/dump.rdb "$BACKUP_DIR/"

# 保留最近 7 天备份
find /backup/perftest/ -maxdepth 1 -type d -mtime +7 -exec rm -rf {} \;

echo "备份完成: $BACKUP_DIR"
```

### 7.4 日志轮转

```conf
# /etc/logrotate.d/perftest
/opt/performance-testing-platform/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
}
```

---

## 八、故障排查

### 8.1 常见问题

| 问题 | 可能原因 | 排查步骤 | 解决方案 |
|------|----------|----------|----------|
| Web UI 无法访问 | Manager 未启动 | `curl localhost:8000/api/health` | 重启 Manager |
| Agent 显示离线 | Redis 连接失败 | `redis-cli -h 管理节点IP ping` | 检查网络和防火墙 |
| 节点验证失败 | 端口不通 | `telnet IP 1100` | 开放防火墙端口 |
| 压测报错 | Java 版本不对 | `java -version` | 安装 Java 8+ |
| 分布式没生效 | 未勾选分布式模式 | 检查任务配置 | 重新创建任务勾选 |
| TPS 很低 | Slave 数量不够 | 检查 Slave 数量 | 增加 Slave |
| 报告生成失败 | JMeter 路径错误 | 检查 JMETER_HOME | 修正路径配置 |

### 8.2 日志查看

```bash
# Manager 日志
tail -100 manager.log

# Agent 日志
tail -100 agent.log

# JMeter 执行日志
tail -100 reports/<task_id>/<agent_id>/jmeter.log

# 系统日志
sudo journalctl -u perftest-manager --since "1 hour ago"
sudo journalctl -u perftest-agent --since "1 hour ago"
```

### 8.3 服务重启

```bash
# 重启单个服务
sudo systemctl restart perftest-manager
sudo systemctl restart perftest-agent

# 重启 Redis
sudo systemctl restart redis

# 重启所有（非 systemd）
bash deploy.sh restart
```

---

## 九、性能调优

### 9.1 系统参数

```bash
# 增大文件描述符
ulimit -n 65535

# 持久化设置
echo "* soft nofile 65535" >> /etc/security/limits.conf
echo "* hard nofile 65535" >> /etc/security/limits.conf

# 网络优化
echo "net.core.somaxconn = 65535" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65535" >> /etc/sysctl.conf
sysctl -p
```

### 9.2 Redis 优化

```conf
# /etc/redis/redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru
save 60 1000
tcp-backlog 511
```

### 9.3 JMeter JVM 调优

```bash
# 编辑 apache-jmeter-5.6.3/bin/jmeter
# 修改 JVM 内存参数
JAVA_OPTS="-Xms1g -Xmx4g -XX:+UseG1GC"
```

### 9.4 并发能力参考

| 配置 | 建议并发数 | 预估 TPS |
|------|-----------|----------|
| 1台 4核8G | 100-200 | 500-1000 |
| 3台 4核8G | 300-600 | 1500-3000 |
| 5台 8核16G | 500-1000 | 3000-6000 |

> 注：实际性能取决于被测系统响应速度和网络延迟

---

## 快速参考

### 常用命令

```bash
# 部署
bash deploy.sh start          # 启动所有服务
bash deploy.sh stop           # 停止所有服务
bash deploy.sh restart        # 重启所有服务
bash deploy.sh status         # 查看状态

# CLI 工具
python cli.py run --script <id> --threads <n> --duration <s>
python cli.py wait --task <id>
python cli.py check --task <id> --max-avg-rt 1000
python cli.py report --task <id> --format pdf

# 服务管理
sudo systemctl start perftest-manager
sudo systemctl start perftest-agent
sudo systemctl status perftest-manager
```

### 默认端口

| 端口 | 服务 |
|------|------|
| 8000 | Web UI / API |
| 6379 | Redis |
| 9999 | Agent |
| 1100+ | JMeter Slave |

### 重要路径

| 路径 | 说明 |
|------|------|
| scripts/ | JMeter 脚本目录 |
| reports/ | 压测报告输出 |
| config/ | 配置文件 |
| manager.log | Manager 日志 |
| agent.log | Agent 日志 |

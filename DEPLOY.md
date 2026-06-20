# 性能测试平台 - 部署手册

> 版本：2.0 | 更新时间：2026-06-20

## 一、环境要求

| 组件 | 要求 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行 Manager 和 Agent |
| Redis | 6.0+ | 消息队列和数据持久化 |
| Java | 8+ | JMeter 运行需要 |
| JMeter | 5.6.3 | 已内置在项目中 |
| 操作系统 | Linux / macOS / Windows | 推荐 Linux |
| 最低内存 | 4GB | 建议 8GB+ |
| 磁盘空间 | 2GB | JMeter + 脚本 + 报告 |

## 二、快速部署（单机模式）

### 2.1 一键部署
```bash
cd Performance\ Testing\ Platform
bash deploy.sh
```

### 2.2 手动部署

#### 步骤 1：安装 Python 依赖
```bash
pip install -r manager/requirements.txt
pip install -r agent/requirements.txt
```

#### 步骤 2：启动 Redis
```bash
# macOS
brew services start redis

# Ubuntu/Debian
sudo systemctl start redis
sudo systemctl enable redis

# CentOS/RHEL
sudo systemctl start redis
sudo systemctl enable redis
```

#### 步骤 3：启动 Manager 服务
```bash
# 前台运行（调试用）
python3 -m manager.main

# 后台运行（生产用）
nohup python3 -m manager.main > manager.log 2>&1 &
```

访问 http://localhost:8000

#### 步骤 4：启动 Agent 服务
```bash
# 前台运行
python3 -m agent.main

# 后台运行
nohup python3 -m agent.main > agent.log 2>&1 &
```

#### 步骤 5：启动 JMeter Slave（分布式模式）
```bash
bash start-slave.sh 1100
```

### 2.3 服务管理命令
```bash
# 启动所有服务
bash deploy.sh start

# 停止所有服务
bash deploy.sh stop

# 重启所有服务
bash deploy.sh restart

# 查看服务状态
bash deploy.sh status

# 清理所有数据
bash deploy.sh clean
```

## 三、分布式部署（生产环境）

### 3.1 架构图

```
┌─────────────────────────────────────────────────────────┐
│                    管理节点 (Manager)                      │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐              │
│  │ FastAPI  │  │  Redis   │  │  Web UI  │              │
│  │  :8000   │  │  :6379   │  │  :8000   │              │
│  └──────────┘  └──────────┘  └──────────┘              │
└───────────────────────┬─────────────────────────────────┘
                        │ Redis PubSub
        ┌───────────────┼───────────────┐
        ▼               ▼               ▼
┌───────────────┐ ┌───────────────┐ ┌───────────────┐
│   Agent 1     │ │   Agent 2     │ │   Agent N     │
│   (施压机1)    │ │   (施压机2)    │ │   (施压机N)    │
│   :9999       │ │   :9999       │ │   :9999       │
├───────────────┤ ├───────────────┤ ├───────────────┤
│ Slave :1100   │ │ Slave :1200   │ │ Slave :1300   │
│ Slave :1101   │ │ Slave :1201   │ │ Slave :1301   │
└───────────────┘ └───────────────┘ └───────────────┘
```

### 3.2 管理节点部署

```bash
# 1. 安装依赖
pip install -r manager/requirements.txt

# 2. 配置 Redis（编辑 /etc/redis/redis.conf）
# bind 0.0.0.0
# requirepass your_password

# 3. 启动 Redis
sudo systemctl start redis

# 4. 启动 Manager
nohup python3 -m manager.main > manager.log 2>&1 &

# 5. 验证服务
curl http://localhost:8000/api/health
```

### 3.3 施压节点部署

在每台施压机上执行：

```bash
# 1. 复制项目
scp -r Performance\ Testing\ Platform/ user@agent-host:~/

# 2. 安装依赖
ssh user@agent-host
cd ~/Performance\ Testing\ Platform
pip install -r agent/requirements.txt

# 3. 配置 Redis 连接（可选，默认连接本机）
export REDIS_HOST=管理节点IP

# 4. 启动 Agent
nohup python3 -m agent.main > agent.log 2>&1 &

# 5. 启动多个 Slave
bash start-slave.sh 1100 &
bash start-slave.sh 1200 &
bash start-slave.sh 1300 &
```

### 3.4 节点注册

1. 登录 Web UI → 施压节点
2. 在「添加远程节点」中输入 Agent 的 IP 和端口
3. 点击「添加」
4. 点击「验证」确认连通性
5. 状态显示「已验证」后即可使用

### 3.5 防火墙配置

```bash
# Manager 节点
sudo firewall-cmd --add-port=8000/tcp --permanent    # Web UI
sudo firewall-cmd --add-port=6379/tcp --permanent    # Redis
sudo firewall-cmd --reload

# Agent 节点
sudo firewall-cmd --add-port=9999/tcp --permanent    # Agent
sudo firewall-cmd --add-port=1100-1301/tcp --permanent  # JMeter Slave
sudo firewall-cmd --reload
```

## 四、Docker 部署

### 4.1 docker-compose.yml
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

volumes:
  redis_data:
```

### 4.2 Dockerfile.manager
```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装 Java（JMeter 需要）
RUN apt-get update && apt-get install -y default-jre-headless && rm -rf /var/lib/apt/lists/*

COPY manager/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ /app/common/
COPY manager/ /app/manager/
COPY scripts/ /app/scripts/
COPY apache-jmeter-5.6.3/ /app/apache-jmeter-5.6.3/

EXPOSE 8000

CMD ["python3", "-m", "manager.main"]
```

### 4.3 Dockerfile.agent
```dockerfile
FROM python:3.10-slim

WORKDIR /app

# 安装 Java
RUN apt-get update && apt-get install -y default-jre-headless && rm -rf /var/lib/apt/lists/*

COPY agent/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY common/ /app/common/
COPY agent/ /app/agent/
COPY apache-jmeter-5.6.3/ /app/apache-jmeter-5.6.3/

CMD ["python3", "-m", "agent.main"]
```

### 4.4 启动
```bash
docker-compose up -d
```

## 五、systemd 服务配置

### 5.1 Manager 服务
```ini
# /etc/systemd/system/perftest-manager.service
[Unit]
Description=Performance Test Platform Manager
After=network.target redis.service

[Service]
Type=simple
User=perftest
WorkingDirectory=/opt/performance-testing-platform
ExecStart=/usr/bin/python3 -m manager.main
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### 5.2 Agent 服务
```ini
# /etc/systemd/system/perftest-agent.service
[Unit]
Description=Performance Test Platform Agent
After=network.target redis.service

[Service]
Type=simple
User=perftest
WorkingDirectory=/opt/performance-testing-platform
ExecStart=/usr/bin/python3 -m agent.main
Restart=always
RestartSec=5
Environment=REDIS_HOST=管理节点IP

[Install]
WantedBy=multi-user.target
```

### 5.3 启用服务
```bash
sudo systemctl daemon-reload
sudo systemctl enable perftest-manager perftest-agent
sudo systemctl start perftest-manager perftest-agent
```

## 六、性能调优

### 6.1 Redis 优化
```conf
# /etc/redis/redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru
save 60 1000
```

### 6.2 系统参数
```bash
# 增大文件描述符
ulimit -n 65535

# 优化网络参数
echo "net.core.somaxconn = 65535" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65535" >> /etc/sysctl.conf
sysctl -p
```

### 6.3 JMeter 调优
```bash
# 编辑 apache-jmeter-5.6.3/bin/jmeter
# 调整 JVM 内存
JAVA_OPTS="-Xms1g -Xmx4g"
```

## 七、监控与告警

### 7.1 内置监控
- Web UI → 施压节点 → 压力机资源监控
- 实时显示 CPU、内存、网络、JMeter 进程

### 7.2 Webhook 通知
1. Web UI → 通知设置
2. 添加 Webhook URL
3. 配置告警规则

### 7.3 告警规则示例
- 平均响应时间 > 1000ms
- 错误率 > 5%
- P99 > 3000ms

## 八、备份与恢复

### 8.1 备份
```bash
# 备份脚本
cp -r scripts/ backup/scripts_$(date +%Y%m%d)/

# 备份报告
cp -r reports/ backup/reports_$(date +%Y%m%d)/

# 备份配置
cp -r config/ backup/config_$(date +%Y%m%d)/

# 备份 Redis
redis-cli BGSAVE
cp /var/lib/redis/dump.rdb backup/
```

### 8.2 恢复
```bash
# 恢复文件
cp -r backup/scripts_*/ scripts/
cp -r backup/reports_*/ reports/
cp -r backup/config_*/ config/

# 恢复 Redis
cp backup/dump.rdb /var/lib/redis/
sudo systemctl restart redis
```

## 九、故障排查

### 9.1 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| Agent 无法连接 | Redis 未启动或网络不通 | 检查 Redis 状态和防火墙 |
| JMeter 执行失败 | Java 版本不兼容 | 安装 Java 8+ |
| 脚本上传失败 | 文件格式不是 .jmx | 只支持 .jmx 文件 |
| 报告生成失败 | JMeter 路径错误 | 检查 JMETER_HOME 配置 |
| 内存不足 | 并发线程数过高 | 增加内存或降低并发数 |

### 9.2 日志查看
```bash
# Manager 日志
tail -f manager.log

# Agent 日志
tail -f agent.log

# JMeter 日志
tail -f reports/<task_id>/<agent_id>/jmeter.log
```

### 9.3 健康检查
```bash
# API 健康检查
curl http://localhost:8000/api/health

# Redis 连接检查
redis-cli ping

# Agent 状态检查
curl http://localhost:8000/api/nodes/
```

## 十、安全建议

1. **Redis 安全**：配置密码、限制访问IP
2. **网络安全**：使用防火墙限制端口访问
3. **数据安全**：定期备份脚本和报告
4. **访问控制**：生产环境建议添加认证机制
5. **日志管理**：定期清理过期日志和报告

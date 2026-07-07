# 性能测试平台 - 部署手册

> 版本：3.0 | 更新时间：2026-07-07

## 一、环境要求

| 组件 | 要求 | 说明 |
|------|------|------|
| Python | 3.10+ | 运行 Manager 和 Agent |
| Redis | 6.0+ | 消息队列和数据持久化 |
| Java | 8+ | JMeter 运行需要 |
| JMeter | 5.6.3 | 已内置在项目中 |
| 操作系统 | Linux / macOS / Windows | 推荐 Linux/macOS |
| 最低内存 | 4GB | 建议 8GB+ |
| 磁盘空间 | 2GB | JMeter + 脚本 + 报告 |

## 二、一键部署（推荐）

### 2.1 最简部署
```bash
# 克隆项目
git clone https://github.com/Kepler-22b-dev/performance-testing-platform.git
cd performance-testing-platform

# 一键部署（自动检测环境、安装依赖、配置服务）
bash deploy.sh
```

脚本会自动：
1. ✅ 检测 Python、Redis、Java 环境
2. ✅ 自动安装缺失组件
3. ✅ 测试并选择最快的 pip 镜像源（阿里云/清华/腾讯等）
4. ✅ 自动安装所有 Python 依赖
5. ✅ 配置 JMeter
6. ✅ 启动所有服务

### 2.2 智能镜像源

脚本会自动测试以下镜像源并选择最快的：
- 阿里云 (mirrors.aliyun.com)
- 清华大学 (pypi.tuna.tsinghua.edu.cn)
- 腾讯云 (mirrors.cloud.tencent.com)
- 中科大 (pypi.mirrors.ustc.edu.cn)
- 华为云 (mirrors.huaweicloud.com)
- 网易 (mirrors.163.com)

### 2.3 一键修复
```bash
# 自动检测并修复问题
bash deploy.sh fix
```

修复功能包括：
- 🔧 检查并安装缺失的 Python 依赖
- 🔧 自动切换到最快的 pip 镜像源
- 🔧 启动未运行的 Redis
- 🔧 清理僵尸进程
- 🔧 重启所有服务

## 三、服务管理

### 3.1 常用命令
```bash
# 启动所有服务
bash deploy.sh start

# 停止所有服务
bash deploy.sh stop

# 重启所有服务
bash deploy.sh restart

# 查看服务状态
bash deploy.sh status

# 一键修复问题
bash deploy.sh fix

# 清理所有数据
bash deploy.sh clean
```

### 3.2 服务状态说明
```bash
$ bash deploy.sh status

════════════════════════════════════════
          服务状态检查
════════════════════════════════════════

  Manager:  ● 运行中  http://localhost:8000
  Agent:    ● 运行中
  Slave:1100 ● 运行中
  Slave:1200 ● 运行中
  Redis:    ● 运行中
  节点:     ● 2/2 已验证
  移动设备: ● 已连接 (ios)

════════════════════════════════════════
```

## 四、手动部署

### 4.1 安装依赖
```bash
# 使用阿里云镜像（国内推荐）
pip3 install -r manager/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
pip3 install -r agent/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 使用清华镜像（备选）
pip3 install -r manager/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/
pip3 install -r agent/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple/

# 或使用默认源
pip3 install -r manager/requirements.txt
pip3 install -r agent/requirements.txt
```

### 4.2 配置 pip 镜像源
```bash
# 创建 pip 配置
mkdir -p ~/.config/pip
cat > ~/.config/pip/pip.conf << EOF
[global]
index-url = https://mirrors.aliyun.com/pypi/simple/
trusted-host = mirrors.aliyun.com
timeout = 60
EOF
```

### 4.3 启动 Redis
```bash
# macOS
brew services start redis

# Ubuntu/Debian
sudo systemctl start redis
sudo systemctl enable redis

# CentOS/RHEL
sudo systemctl start redis
sudo systemctl enable redis

# 直接启动（无需 sudo）
redis-server --daemonize yes
```

### 4.4 启动服务
```bash
# 启动 Manager
nohup python3 -m manager.main > /tmp/manager.log 2>&1 &

# 启动 Agent
nohup python3 -m agent.main > /tmp/agent.log 2>&1 &

# 启动 JMeter Slave（分布式模式）
bash start-slave.sh 1100
```

### 4.5 验证服务
```bash
# 检查 Manager
curl http://localhost:8000/api/health

# 检查 Redis
redis-cli ping
```

访问 http://localhost:8000

## 五、分布式部署（生产环境）

### 5.1 架构图

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

### 5.2 管理节点部署

```bash
# 1. 安装依赖（使用国内镜像）
pip3 install -r manager/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 2. 配置 Redis
sudo vim /etc/redis/redis.conf
# bind 0.0.0.0
# requirepass your_password

# 3. 启动 Redis
sudo systemctl start redis

# 4. 启动 Manager
nohup python3 -m manager.main > manager.log 2>&1 &

# 5. 验证服务
curl http://localhost:8000/api/health
```

### 5.3 施压节点部署

在每台施压机上执行：

```bash
# 1. 复制项目
scp -r Performance\ Testing\ Platform/ user@agent-host:~/

# 2. 安装依赖
ssh user@agent-host
cd ~/Performance\ Testing\ Platform
pip3 install -r agent/requirements.txt -i https://mirrors.aliyun.com/pypi/simple/

# 3. 配置 Redis 连接（可选，默认连接本机）
export REDIS_HOST=管理节点IP

# 4. 启动 Agent
nohup python3 -m agent.main > agent.log 2>&1 &

# 5. 启动多个 Slave
bash start-slave.sh 1100 &
bash start-slave.sh 1200 &
bash start-slave.sh 1300 &
```

### 5.4 节点注册

1. 登录 Web UI → 施压节点
2. 在「添加远程节点」中输入 Agent 的 IP 和端口
3. 点击「添加」
4. 点击「验证」确认连通性
5. 状态显示「已验证」后即可使用

### 5.5 防火墙配置

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

## 六、移动端性能监控

### 6.1 iOS 设备（需要 pymobiledevice3）

```bash
# 安装 pymobiledevice3
pip3 install pymobiledevice3

# 检查设备连接
pymobiledevice3 usbmux list

# 如果是 iOS 17+ 设备，需要启动 tunnel
sudo pymobiledevice3 remote tunneld
```

### 6.2 Android 设备（需要 SOLOX）

```bash
# 安装 SOLOX
pip3 install solox

# 启动 SOLOX 服务
python3 -m solox --host=127.0.0.1 --port=50001

# 或后台运行
nohup python3 -m solox --host=127.0.0.1 --port=50001 &
```

### 6.3 使用移动端监控

1. 连接设备（USB 连接 iPhone 或 Android）
2. 启动对应服务（pymobiledevice3 或 SOLOX）
3. 访问 Web UI → 移动端监控
4. 系统自动检测设备平台
5. 选择应用 → 开始监控

## 七、Docker 部署

### 7.1 docker-compose.yml
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

### 7.2 启动
```bash
docker-compose up -d
```

## 八、性能调优

### 8.1 Redis 优化
```conf
# /etc/redis/redis.conf
maxmemory 2gb
maxmemory-policy allkeys-lru
save 60 1000
```

### 8.2 系统参数
```bash
# 增大文件描述符
ulimit -n 65535

# 优化网络参数
echo "net.core.somaxconn = 65535" >> /etc/sysctl.conf
echo "net.ipv4.tcp_max_syn_backlog = 65535" >> /etc/sysctl.conf
sudo sysctl -p
```

### 8.3 JMeter 调优
```bash
# 编辑 apache-jmeter-5.6.3/bin/jmeter
# 调整 JVM 内存
JAVA_OPTS="-Xms1g -Xmx4g"
```

## 九、故障排查

### 9.1 一键诊断
```bash
# 运行修复脚本
bash deploy.sh fix

# 查看服务状态
bash deploy.sh status
```

### 9.2 常见问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| pip 安装超时 | 网络问题或源不可用 | 运行 `bash deploy.sh fix` 自动切换镜像 |
| Agent 无法连接 | Redis 未启动或网络不通 | 检查 Redis 状态：`redis-cli ping` |
| JMeter 执行失败 | Java 版本不兼容 | 安装 Java 8+：`java -version` |
| 脚本上传失败 | 文件格式不是 .jmx | 只支持 .jmx 文件 |
| 报告生成失败 | JMeter 路径错误 | 检查 JMETER_HOME 配置 |
| 内存不足 | 并发线程数过高 | 增加内存或降低并发数 |
| iOS 设备无法检测 | pymobiledevice3 未安装 | `pip3 install pymobiledevice3` |
| Android 设备无法检测 | SOLOX 未启动 | `python3 -m solox` |

### 9.3 日志查看
```bash
# Manager 日志
tail -f /tmp/manager.log

# Agent 日志
tail -f /tmp/agent.log

# JMeter 日志
tail -f reports/<task_id>/<agent_id>/jmeter.log
```

### 9.4 健康检查
```bash
# API 健康检查
curl http://localhost:8000/api/health

# Redis 连接检查
redis-cli ping

# Agent 状态检查
curl http://localhost:8000/api/nodes/

# 移动设备检测
curl http://localhost:8000/api/mobile/detect
```

## 十、安全建议

1. **Redis 安全**：配置密码、限制访问IP
2. **网络安全**：使用防火墙限制端口访问
3. **数据安全**：定期备份脚本和报告
4. **访问控制**：生产环境建议添加认证机制
5. **日志管理**：定期清理过期日志和报告

## 十一、相关命令速查

```bash
# 部署相关
bash deploy.sh start      # 一键部署
bash deploy.sh stop       # 停止服务
bash deploy.sh restart    # 重启服务
bash deploy.sh status     # 查看状态
bash deploy.sh fix        # 一键修复
bash deploy.sh clean      # 清理数据

# 服务管理
python3 -m manager.main   # 前台启动 Manager
python3 -m agent.main     # 前台启动 Agent
redis-server --daemonize yes  # 启动 Redis

# 移动端监控
pymobiledevice3 usbmux list   # 检查 iOS 设备
python3 -m solox              # 启动 Android 监控
```

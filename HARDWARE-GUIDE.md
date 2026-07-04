# 性能测试平台 - 硬件配置指南

> 版本：1.0 | 更新时间：2026-06-20

本文档提供部署性能测试平台的硬件配置建议，确保系统运行无性能瓶颈。

---

## 一、架构概览

```
┌─────────────────────────────────────────────────────────────┐
│                    管理节点 (1台)                              │
│  Manager + Redis + Web UI                                    │
│  职责: 任务调度、结果收集、前端服务、JTL解析存储              │
└───────────────────────────┬─────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│  施压节点 1    │   │  施压节点 2    │   │  施压节点 3    │
│  Agent+Slave  │   │  Agent+Slave  │   │  Agent+Slave  │
│  500并发       │   │  500并发       │   │  500并发       │
└───────────────┘   └───────────────┘   └───────────────┘
```

---

## 二、配置方案

### 方案一：开发测试环境

适用场景：个人开发、功能测试、小规模验证

| 组件 | 配置 | 说明 |
|------|------|------|
| 服务器 | 1台 | 管理+施压合一 |
| CPU | 4核 | 够用 |
| 内存 | 8GB | JMeter JVM 给 2GB |
| 磁盘 | 100GB SSD | 存储脚本和报告 |
| 并发能力 | 200-500 | 单机极限 |

### 方案二：小团队生产环境

适用场景：5-20人团队，日常压测

| 组件 | 配置 | 数量 |
|------|------|------|
| 管理节点 | 4核 8GB 100GB SSD | 1台 |
| 施压节点 | 8核 16GB 100GB SSD | 2台 |
| 总并发能力 | 1000-2000 | - |

### 方案三：中大型团队生产环境

适用场景：20-50人团队，高频压测

| 组件 | 配置 | 数量 |
|------|------|------|
| 管理节点 | 8核 16GB 500GB SSD | 1台 |
| 施压节点 | 8核 16GB 100GB SSD | 3-5台 |
| 总并发能力 | 2000-5000 | - |

### 方案四：企业级高可用

适用场景：50+人团队，关键业务压测

| 组件 | 配置 | 数量 |
|------|------|------|
| 管理节点 | 16核 32GB 1TB SSD | 2台(主备) |
| Redis | 8核 16GB | 3台(集群) |
| 施压节点 | 16核 32GB 200GB SSD | 5-10台 |
| 总并发能力 | 5000-10000 | - |

---

## 三、各节点详细配置

### 3.1 管理节点

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| **CPU** | 8核 | JTL 解析、报告生成需要计算资源 |
| **内存** | 16GB | Redis 4GB + Manager 2GB + JTL 解析缓存 |
| **磁盘** | 500GB SSD | JTL 文件增长快，1000并发跑5分钟约 500MB-1GB |
| **带宽** | 100Mbps+ | 实时进度上报、结果传输 |
| **网络** | 内网千兆 | 与施压节点保持低延迟 |

### 3.2 施压节点（每台）

| 配置项 | 推荐值 | 说明 |
|--------|--------|------|
| **CPU** | 8核 | JMeter 线程池需要 CPU 资源 |
| **内存** | 16GB | JVM 给 4-6GB，系统留 4GB |
| **磁盘** | 100GB SSD | 存储 JTL 结果文件 |
| **带宽** | 100Mbps+ | 被测系统的网络吞吐 |
| **网络** | 内网千兆 | 与管理节点和被测系统保持低延迟 |

---

## 四、JVM 配置（关键）

JMeter 默认 JVM 内存太小，压测时容易 OOM。必须调整：

### 施压节点 JMeter JVM

```bash
# 编辑 apache-jmeter-5.6.3/bin/jmeter
# 找到 JVM_ARGS，修改为：

JVM_ARGS="-Xms4g -Xmx6g -XX:+UseG1GC -XX:MaxGCPauseMillis=200"

# 参数说明：
# -Xms4g: 初始堆内存 4GB
# -Xmx6g: 最大堆内存 6GB（留 2GB 给系统）
# -XX:+UseG1GC: 使用 G1 垃圾回收器，适合大内存
# -XX:MaxGCPauseMillis=200: 控制 GC 停顿时间
```

### 不同并发数的 JVM 建议

| 并发数 | JVM 堆内存 | 服务器内存 |
|--------|-----------|-----------|
| < 200 | 2GB | 8GB |
| 200-500 | 4GB | 16GB |
| 500-1000 | 6GB | 16GB |
| 1000-2000 | 8GB | 32GB |
| 2000+ | 12GB+ | 64GB |

---

## 五、Redis 配置

```bash
# /etc/redis/redis.conf

# 内存限制
maxmemory 4gb
maxmemory-policy allkeys-lru

# 持久化
save 60 10000

# 连接优化
tcp-backlog 511
timeout 300
tcp-keepalive 60
```

---

## 六、系统内核参数

### /etc/sysctl.conf

```bash
# 网络优化
net.core.somaxconn = 65535
net.core.netdev_max_backlog = 65535
net.ipv4.tcp_max_syn_backlog = 65535
net.ipv4.tcp_tw_reuse = 1
net.ipv4.tcp_fin_timeout = 15
net.ipv4.ip_local_port_range = 1024 65535

# 内存优化
vm.max_map_count = 262144
vm.overcommit_memory = 1

# 文件系统
fs.file-max = 2097152
```

### /etc/security/limits.conf

```bash
* soft nofile 1048576
* hard nofile 1048576
* soft nproc 65535
* hard nproc 65535
```

---

## 七、JTL 文件存储规划

### 存储空间估算

| 并发数 | 时长 | 预估 JTL 大小 | 100次压测 |
|--------|------|---------------|-----------|
| 100 | 5分钟 | ~50MB | ~5GB |
| 500 | 5分钟 | ~200MB | ~20GB |
| 1000 | 5分钟 | ~500MB | ~50GB |
| 1000 | 30分钟 | ~3GB | ~300GB |

### 存储建议

```bash
# 推荐使用独立磁盘或分区存储 JTL
# 挂载点: /data/jtl

# 创建软链接
ln -s /data/jtl /opt/performance-testing-platform/reports
```

### 自动清理脚本

```bash
#!/bin/bash
# /opt/scripts/clean_old_jtl.sh
# 清理 30 天前的 JTL 文件

REPORTS_DIR="/opt/performance-testing-platform/reports"
DAYS=30

find "$REPORTS_DIR" -name "*.jtl" -mtime +$DAYS -delete
find "$REPORTS_DIR" -name "*.xml" -mtime +$DAYS -delete
find "$REPORTS_DIR" -name "*.log" -mtime +$DAYS -delete
find "$REPORTS_DIR" -mindepth 1 -maxdepth 1 -type d -mtime +$DAYS -exec rm -rf {} \;

echo "$(date): 清理完成"
```

### 定时清理

```bash
# 添加 crontab
crontab -e

# 每天凌晨 3 点清理 30 天前的文件
0 3 * * * /opt/scripts/clean_old_jtl.sh >> /var/log/jtl_cleanup.log 2>&1
```

---

## 八、配置汇总表

| 组件 | CPU | 内存 | 磁盘 | 数量 |
|------|-----|------|------|------|
| **管理节点** | 8核 | 16GB | 500GB SSD | 1台 |
| **Redis** | - | 4GB(限制) | - | 共用管理节点 |
| **施压节点** | 8核 | 16GB | 100GB SSD | 3台 |
| **JMeter JVM** | - | 4-6GB(堆) | - | 每台施压节点 |

### 网络要求

| 链路 | 带宽 | 延迟要求 |
|------|------|----------|
| 管理节点 ↔ 施压节点 | 100Mbps+ | < 5ms |
| 施压节点 ↔ 被测系统 | 100Mbps+ | < 10ms |
| 管理节点 ↔ Redis | 本机 | < 1ms |

### 总预算参考（云服务器年费）

| 方案 | 配置 | 预估费用/年 |
|------|------|------------|
| 开发测试 | 4核8G × 1 | ¥3,000-5,000 |
| 小团队 | 4核8G × 1 + 8核16G × 2 | ¥15,000-25,000 |
| 中型团队 | 8核16G × 1 + 8核16G × 3 | ¥40,000-60,000 |
| 企业级 | 16核32G × 2 + 16核32G × 5 | ¥150,000+ |

---

## 九、快速部署命令

### 管理节点

```bash
# 1. 环境准备
sudo apt update && sudo apt install -y python3 python3-pip default-jre-headless redis-server
sudo systemctl start redis

# 2. 项目部署
git clone https://github.com/your-org/performance-testing-platform.git
cd performance-testing-platform

# 3. JMeter 安装
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
tar -xzf apache-jmeter-5.6.3.tgz && rm apache-jmeter-5.6.3.tgz

# 4. JVM 调优
sed -i 's/JVM_ARGS="-Xms1G -Xmax1G/JVM_ARGS="-Xms4g -Xmx6g/' apache-jmeter-5.6.3/bin/jmeter

# 5. 依赖安装
pip install -r manager/requirements.txt

# 6. 启动
bash deploy.sh start
```

### 施压节点

```bash
# 1. 环境准备
sudo apt update && sudo apt install -y python3 python3-pip default-jre-headless

# 2. 项目部署
git clone https://github.com/your-org/performance-testing-platform.git
cd performance-testing-platform

# 3. JMeter 安装
wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz
tar -xzf apache-jmeter-5.6.3.tgz && rm apache-jmeter-5.6.3.tgz

# 4. JVM 调优
sed -i 's/JVM_ARGS="-Xms1G -Xmax1G/JVM_ARGS="-Xms4g -Xmx6g/' apache-jmeter-5.6.3/bin/jmeter

# 5. 依赖安装
pip install -r agent/requirements.txt

# 6. 配置 Redis
export REDIS_HOST=管理节点IP

# 7. 启动 Agent
nohup python3 -m agent.main > agent.log 2>&1 &

# 8. 启动 Slave
nohup apache-jmeter-5.6.3/bin/jmeter-server \
    -Dserver_port=1100 \
    -Dserver.rmi.ssl.disable=true \
    -Djava.rmi.server.hostname=当前机器IP \
    > slave.log 2>&1 &
```

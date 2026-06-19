#!/bin/bash
#============================================================
# 性能测试平台 - 一键部署脚本
# 用法: bash deploy.sh [start|stop|restart|status|clean]
#============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info()  { echo -e "${BLUE}[i]${NC} $1"; }

#============================================================
# 环境检查
#============================================================
check_env() {
    info "检查环境..."

    # Python
    if ! command -v python3 &>/dev/null; then
        error "未找到 python3，请先安装 Python 3.10+"
    fi
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    log "Python: $PY_VER"

    # pip
    if ! command -v pip3 &>/dev/null; then
        error "未找到 pip3"
    fi
    log "pip3: 已安装"

    # Redis
    if command -v redis-cli &>/dev/null; then
        if redis-cli ping &>/dev/null; then
            log "Redis: 运行中"
        else
            warn "Redis 已安装但未运行，尝试启动..."
            if [[ "$OSTYPE" == "darwin"* ]]; then
                brew services start redis 2>/dev/null || redis-server --daemonize yes
            else
                sudo systemctl start redis 2>/dev/null || redis-server --daemonize yes
            fi
            sleep 1
            redis-cli ping &>/dev/null && log "Redis: 启动成功" || error "Redis 启动失败"
        fi
    else
        warn "未找到 Redis，尝试安装..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            brew install redis && brew services start redis
        elif command -v apt-get &>/dev/null; then
            sudo apt-get install -y redis-server && sudo systemctl start redis
        elif command -v yum &>/dev/null; then
            sudo yum install -y redis && sudo systemctl start redis
        else
            error "无法自动安装 Redis，请手动安装"
        fi
        log "Redis: 安装并启动"
    fi

    # Java (JMeter 需要)
    if ! command -v java &>/dev/null; then
        error "未找到 java，JMeter 需要 Java 8+"
    fi
    JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2)
    log "Java: $JAVA_VER"
}

#============================================================
# 安装依赖
#============================================================
install_deps() {
    info "安装 Python 依赖..."
    pip3 install -r manager/requirements.txt -q 2>/dev/null
    pip3 install -r agent/requirements.txt -q 2>/dev/null
    log "依赖安装完成"
}

#============================================================
# 配置 JMeter
#============================================================
setup_jmeter() {
    info "配置 JMeter..."

    # 创建符号链接（避免路径含空格问题）
    [ -L /tmp/jmeter ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3" /tmp/jmeter
    [ -L /tmp/jmeter-slave ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3-slave" /tmp/jmeter-slave

    # 检查 slave2/slave3
    [ -d "$SCRIPT_DIR/apache-jmeter-5.6.3-slave2" ] && \
        [ -L /tmp/jmeter-slave2 ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3-slave2" /tmp/jmeter-slave2 2>/dev/null
    [ -d "$SCRIPT_DIR/apache-jmeter-5.6.3-slave3" ] && \
        [ -L /tmp/jmeter-slave3 ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3-slave3" /tmp/jmeter-slave3 2>/dev/null

    # 禁用 SSL
    for props in \
        "$SCRIPT_DIR/apache-jmeter-5.6.3/bin/jmeter.properties" \
        "$SCRIPT_DIR/apache-jmeter-5.6.3-slave/bin/jmeter.properties"; do
        if [ -f "$props" ]; then
            sed -i.bak 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' "$props" 2>/dev/null || \
            sed -i '' 's/^#server.rmi.ssl.disable=false/server.rmi.ssl.disable=true/' "$props" 2>/dev/null
        fi
    done

    log "JMeter 配置完成"
}

#============================================================
# 创建目录
#============================================================
setup_dirs() {
    mkdir -p scripts reports config/csv
    log "目录初始化完成"
}

#============================================================
# 启动服务
#============================================================
start_manager() {
    if pgrep -f "manager.main" >/dev/null 2>&1; then
        warn "Manager 已运行"
        return
    fi

    info "启动 Manager..."
    nohup python3 -m manager.main > /tmp/manager.log 2>&1 &
    sleep 2

    if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
        log "Manager 启动成功 (http://localhost:8000)"
    else
        error "Manager 启动失败，查看 /tmp/manager.log"
    fi
}

start_agent() {
    if pgrep -f "agent.main" >/dev/null 2>&1; then
        warn "Agent 已运行"
        return
    fi

    info "启动 Agent..."
    cd "$SCRIPT_DIR"
    nohup python3 -m agent.main > /tmp/agent.log 2>&1 &
    sleep 3

    if pgrep -f "agent.main" >/dev/null 2>&1; then
        log "Agent 启动成功"
    else
        error "Agent 启动失败，查看 /tmp/agent.log"
    fi
}

start_slaves() {
    info "启动 Slave 节点..."

    # 获取本机 IP
    LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")

    # 启动 Slave (1100)
    if ! lsof -i :1100 >/dev/null 2>&1; then
        /tmp/jmeter-slave/bin/jmeter-server \
            -Dserver_port=1100 \
            -Dserver.rmi.ssl.disable=true \
            -Djava.rmi.server.hostname=$LOCAL_IP > /tmp/slave.log 2>&1 &
        sleep 3
        lsof -i :1100 >/dev/null 2>&1 && log "Slave1 启动成功 (端口 1100)" || warn "Slave1 启动失败"
    else
        warn "Slave1 已运行 (端口 1100)"
    fi

    # 启动 Slave2 (1200) - 如果存在
    if [ -d "$SCRIPT_DIR/apache-jmeter-5.6.3-slave2" ]; then
        sed -i.bak 's/^server_port=1100/server_port=1200/' /tmp/jmeter-slave2/bin/jmeter.properties 2>/dev/null || \
        sed -i '' 's/^server_port=1100/server_port=1200/' /tmp/jmeter-slave2/bin/jmeter.properties 2>/dev/null

        if ! lsof -i :1200 >/dev/null 2>&1; then
            /tmp/jmeter-slave2/bin/jmeter-server \
                -Dserver_port=1200 \
                -Dserver.rmi.ssl.disable=true \
                -Djava.rmi.server.hostname=$LOCAL_IP > /tmp/slave2.log 2>&1 &
            sleep 3
            lsof -i :1200 >/dev/null 2>&1 && log "Slave2 启动成功 (端口 1200)" || warn "Slave2 启动失败"
        else
            warn "Slave2 已运行 (端口 1200)"
        fi
    fi

    # 启动 Slave3 (1300) - 如果存在
    if [ -d "$SCRIPT_DIR/apache-jmeter-5.6.3-slave3" ]; then
        sed -i.bak 's/^server_port=1100/server_port=1300/' /tmp/jmeter-slave3/bin/jmeter.properties 2>/dev/null || \
        sed -i '' 's/^server_port=1100/server_port=1300/' /tmp/jmeter-slave3/bin/jmeter.properties 2>/dev/null

        if ! lsof -i :1300 >/dev/null 2>&1; then
            /tmp/jmeter-slave3/bin/jmeter-server \
                -Dserver_port=1300 \
                -Dserver.rmi.ssl.disable=true \
                -Djava.rmi.server.hostname=$LOCAL_IP > /tmp/slave3.log 2>&1 &
            sleep 3
            lsof -i :1300 >/dev/null 2>&1 && log "Slave3 启动成功 (端口 1300)" || warn "Slave3 启动失败"
        else
            warn "Slave3 已运行 (端口 1300)"
        fi
    fi
}

#============================================================
# 停止服务
#============================================================
stop_all() {
    info "停止所有服务..."

    pkill -f "manager.main" 2>/dev/null && log "Manager 已停止" || warn "Manager 未运行"
    pkill -f "agent.main" 2>/dev/null && log "Agent 已停止" || warn "Agent 未运行"
    pkill -f "jmeter-server" 2>/dev/null && log "Slaves 已停止" || warn "Slaves 未运行"
    pkill -f "ApacheJMeter.jar" 2>/dev/null

    sleep 1
    log "所有服务已停止"
}

#============================================================
# 状态检查
#============================================================
show_status() {
    echo ""
    echo -e "${BLUE}========== 服务状态 ==========${NC}"
    echo ""

    # Manager
    if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
        echo -e "  Manager:  ${GREEN}运行中${NC}  (http://localhost:8000)"
    else
        echo -e "  Manager:  ${RED}未运行${NC}"
    fi

    # Agent
    if pgrep -f "agent.main" >/dev/null 2>&1; then
        echo -e "  Agent:    ${GREEN}运行中${NC}"
    else
        echo -e "  Agent:    ${RED}未运行${NC}"
    fi

    # Slaves
    for port in 1100 1200 1300; do
        if lsof -i :$port >/dev/null 2>&1; then
            echo -e "  Slave:$port ${GREEN}运行中${NC}"
        else
            echo -e "  Slave:$port ${RED}未运行${NC}"
        fi
    done

    # Redis
    if redis-cli ping >/dev/null 2>&1; then
        echo -e "  Redis:    ${GREEN}运行中${NC}"
    else
        echo -e "  Redis:    ${RED}未运行${NC}"
    fi

    # 节点
    if curl -s http://localhost:8000/api/registry/ >/dev/null 2>&1; then
        NODES=$(curl -s http://localhost:8000/api/registry/ | python3 -c "import sys,json; d=json.load(sys.stdin); verified=sum(1 for n in d['nodes'] if n['status']=='verified'); print(f'{verified}/{d[\"total\"]}')" 2>/dev/null)
        echo -e "  已注册节点: ${GREEN}$NODES 已验证${NC}"
    fi

    echo ""
    echo -e "${BLUE}==============================${NC}"
    echo ""
}

#============================================================
# 清理
#============================================================
clean_all() {
    warn "即将清理所有数据（包括脚本、报告、配置）..."
    read -p "确认清理？(y/N): " confirm
    if [ "$confirm" != "y" ]; then
        info "已取消"
        return
    fi

    stop_all
    rm -rf scripts/* reports/* config/*
    redis-cli FLUSHDB 2>/dev/null
    log "清理完成"
}

#============================================================
# 主入口
#============================================================
main() {
    echo ""
    echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
    echo -e "${BLUE}║      性能测试平台 - 部署工具         ║${NC}"
    echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
    echo ""

    case "${1:-start}" in
        start)
            check_env
            install_deps
            setup_jmeter
            setup_dirs
            start_manager
            start_agent
            start_slaves
            show_status
            echo -e "${GREEN}部署完成！访问 http://localhost:8000${NC}"
            ;;
        stop)
            stop_all
            ;;
        restart)
            stop_all
            sleep 1
            start_manager
            start_agent
            start_slaves
            show_status
            ;;
        status)
            show_status
            ;;
        clean)
            clean_all
            ;;
        *)
            echo "用法: bash deploy.sh [start|stop|restart|status|clean]"
            echo ""
            echo "  start   - 一键部署并启动所有服务"
            echo "  stop    - 停止所有服务"
            echo "  restart - 重启所有服务"
            echo "  status  - 查看服务状态"
            echo "  clean   - 清理所有数据"
            ;;
    esac
}

main "$@"

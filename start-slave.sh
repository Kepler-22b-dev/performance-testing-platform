#!/bin/bash
#============================================================
# JMeter Slave 一键启动脚本
# 用法:
#   ./start-slave.sh              # 启动 1 个 Slave (端口 1100)
#   ./start-slave.sh 3            # 启动 3 个 Slave (端口 1100-1102)
#   ./start-slave.sh 5 1200       # 启动 5 个 Slave (端口 1200-1204)
#   ./start-slave.sh stop         # 停止所有 Slave
#   ./start-slave.sh status       # 查看 Slave 状态
#============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# JMeter 路径（优先使用环境变量，其次使用符号链接）
if [ -n "$JMETER_HOME" ]; then
    JMETER_DIR="$JMETER_HOME"
elif [ -L "/tmp/jmeter" ]; then
    JMETER_DIR="/tmp/jmeter"
else
    JMETER_DIR="$SCRIPT_DIR/apache-jmeter-5.6.3"
fi

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; }
info()  { echo -e "${BLUE}[i]${NC} $1"; }

# 获取本机 IP
get_local_ip() {
    if command -v ipconfig &>/dev/null; then
        ipconfig getifaddr en0 2>/dev/null || echo "127.0.0.1"
    elif command -v hostname &>/dev/null; then
        hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1"
    else
        echo "127.0.0.1"
    fi
}

# 检查端口是否被占用
check_port() {
    local port=$1
    if command -v lsof &>/dev/null; then
        lsof -i :$port >/dev/null 2>&1
    elif command -v netstat &>/dev/null; then
        netstat -tlnp 2>/dev/null | grep -q ":$port "
    else
        return 1
    fi
}

# 启动单个 Slave
start_one_slave() {
    local port=$1
    local local_ip=$(get_local_ip)

    if check_port $port; then
        warn "端口 $port 已被占用，跳过"
        return 0
    fi

    info "启动 Slave (端口 $port, IP: $local_ip)..."

    nohup "$JMETER_DIR/bin/jmeter-server" \
        -Dserver_port=$port \
        -Dserver.rmi.ssl.disable=true \
        -Djava.rmi.server.hostname="$local_ip" \
        > "/tmp/slave-${port}.log" 2>&1 &

    sleep 2

    if check_port $port; then
        log "Slave 启动成功 (端口 $port, PID: $!)"
        return 0
    else
        warn "Slave 启动可能失败，请检查日志: /tmp/slave-${port}.log"
        return 1
    fi
}

# 停止所有 Slave
stop_all_slaves() {
    info "停止所有 Slave..."
    pkill -f "jmeter-server" 2>/dev/null && log "已停止所有 JMeter Slave" || warn "没有运行中的 Slave"

    # 清理端口占用
    for port in $(seq 1100 1110); do
        if check_port $port; then
            pid=$(lsof -ti :$port 2>/dev/null)
            if [ -n "$pid" ]; then
                kill $pid 2>/dev/null
                log "已停止端口 $port 的进程 (PID: $pid)"
            fi
        fi
    done
}

# 查看 Slave 状态
show_status() {
    echo ""
    echo -e "${BLUE}========== JMeter Slave 状态 ==========${NC}"
    echo ""

    local running=0
    for port in $(seq 1100 1110); do
        if check_port $port; then
            echo -e "  端口 $port: ${GREEN}运行中${NC}"
            running=$((running + 1))
        fi
    done

    if [ $running -eq 0 ]; then
        echo -e "  ${YELLOW}没有运行中的 Slave${NC}"
    else
        echo -e "  共 ${GREEN}$running${NC} 个 Slave 运行中"
    fi

    echo ""
    echo -e "${BLUE}=========================================${NC}"
    echo ""
}

# 主函数
main() {
    case "${1:-1}" in
        stop)
            stop_all_slaves
            ;;
        status)
            show_status
            ;;
        *)
            local count=${1:-1}
            local start_port=${2:-1100}

            echo ""
            echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
            echo -e "${BLUE}║      JMeter Slave 一键启动          ║${NC}"
            echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
            echo ""

    # 检查 JMeter
    if [ ! -f "$JMETER_DIR/bin/jmeter-server" ]; then
        error "未找到 JMeter: $JMETER_DIR/bin/jmeter-server"
        error "请先下载 JMeter: wget https://archive.apache.org/dist/jmeter/binaries/apache-jmeter-5.6.3.tgz"
        exit 1
    fi

    info "JMeter 路径: $JMETER_DIR"
            info "启动 $count 个 Slave (端口 $start_port-$((start_port + count - 1)))"
            echo ""

            local success=0
            local fail=0
            for i in $(seq 0 $((count - 1))); do
                port=$((start_port + i))
                if start_one_slave $port; then
                    success=$((success + 1))
                else
                    fail=$((fail + 1))
                fi
            done

            echo ""
            log "启动完成: $success 成功, $fail 失败"
            show_status
            ;;
    esac
}

main "$@"

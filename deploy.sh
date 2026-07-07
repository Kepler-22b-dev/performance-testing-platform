#!/bin/bash
#============================================================
# 性能测试平台 - 一键部署脚本
# 用法: bash deploy.sh [start|stop|restart|status|clean|fix]
#============================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

log()   { echo -e "${GREEN}[✓]${NC} $1"; }
warn()  { echo -e "${YELLOW}[!]${NC} $1"; }
error() { echo -e "${RED}[✗]${NC} $1"; exit 1; }
info()  { echo -e "${BLUE}[i]${NC} $1"; }
step()  { echo -e "${CYAN}[→]${NC} $1"; }

#============================================================
# 镜像源配置
#============================================================
MIRRORS=(
    "https://mirrors.aliyun.com/pypi/simple/"
    "https://pypi.tuna.tsinghua.edu.cn/simple/"
    "https://mirrors.cloud.tencent.com/pypi/simple"
    "https://pypi.mirrors.ustc.edu.cn/simple/"
    "https://mirrors.huaweicloud.com/repository/pypi/simple/"
    "https://mirrors.163.com/pypi/simple/"
)

PIP_MIRROR=""
PIP_MIRROR_NAME=""

# 测试镜像源速度
test_mirror() {
    local url=$1
    local name=$2
    local start_time=$(date +%s%N)
    curl -s --connect-timeout 3 --max-time 5 "$url" > /dev/null 2>&1
    local exit_code=$?
    local end_time=$(date +%s%N)
    
    if [ $exit_code -eq 0 ]; then
        local elapsed=$(( (end_time - start_time) / 1000000 ))
        echo "$elapsed"
        return 0
    fi
    echo "9999"
    return 1
}

# 选择最快的镜像源
select_fastest_mirror() {
    info "正在测试镜像源速度..."
    
    local best_time=9999
    local best_mirror=""
    local best_name=""
    
    for i in "${!MIRRORS[@]}"; do
        local mirror=${MIRRORS[$i]}
        local names=("阿里云" "清华" "腾讯" "中科大" "华为" "网易")
        local name=${names[$i]}
        
        printf "  测试 %-8s ... " "$name"
        local time=$(test_mirror "$mirror" "$name")
        
        if [ "$time" != "9999" ]; then
            echo "${time}ms ✓"
            if [ "$time" -lt "$best_time" ]; then
                best_time=$time
                best_mirror=$mirror
                best_name=$name
            fi
        else
            echo "超时 ✗"
        fi
    done
    
    if [ -n "$best_mirror" ]; then
        PIP_MIRROR="$best_mirror"
        PIP_MIRROR_NAME="$best_name"
        log "选择最快镜像: $best_name (${best_time}ms)"
    else
        warn "所有镜像源不可达，使用默认源"
        PIP_MIRROR=""
    fi
}

# 设置 pip 镜像源
setup_pip_mirror() {
    if [ -n "$PIP_MIRROR" ]; then
        mkdir -p ~/.config/pip
        cat > ~/.config/pip/pip.conf << EOF
[global]
index-url = $PIP_MIRROR
trusted-host = $(echo $PIP_MIRROR | sed 's|https://||' | sed 's|/.*||')
timeout = 60

[install]
trusted-host = $(echo $PIP_MIRROR | sed 's|https://||' | sed 's|/.*||')
EOF
        log "pip 镜像源已配置: $PIP_MIRROR_NAME"
    fi
}

# 快速 pip install（带重试）
pip_install() {
    local req_file=$1
    local extra_args=${2:-""}
    
    if [ ! -f "$req_file" ]; then
        warn "文件不存在: $req_file"
        return 1
    fi
    
    local mirror_arg=""
    if [ -n "$PIP_MIRROR" ]; then
        mirror_arg="-i $PIP_MIRROR --trusted-host $(echo $PIP_MIRROR | sed 's|https://||' | sed 's|/.*||')"
    fi
    
    local max_retries=3
    local retry=0
    
    while [ $retry -lt $max_retries ]; do
        if pip3 install -r "$req_file" $mirror_arg $extra_args -q 2>/dev/null; then
            return 0
        fi
        
        retry=$((retry + 1))
        if [ $retry -lt $max_retries ]; then
            warn "安装失败，尝试其他镜像源... ($retry/$max_retries)"
            # 尝试切换镜像
            for mirror in "${MIRRORS[@]}"; do
                if pip3 install -r "$req_file" -i "$mirror" --trusted-host "$(echo $mirror | sed 's|https://||' | sed 's|/.*||')" $extra_args -q 2>/dev/null; then
                    PIP_MIRROR="$mirror"
                    log "切换到备用镜像源成功"
                    return 0
                fi
            done
        fi
    done
    
    return 1
}

#============================================================
# 环境检查与自动修复
#============================================================
check_env() {
    info "检查环境..."
    local need_fix=0

    # Python
    if ! command -v python3 &>/dev/null; then
        error "未找到 python3，请先安装 Python 3.10+"
    fi
    PY_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
    PY_MAJOR=$(echo $PY_VER | cut -d. -f1)
    PY_MINOR=$(echo $PY_VER | cut -d. -f2)
    
    if [ "$PY_MAJOR" -lt 3 ] || ([ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]); then
        error "Python 版本过低 ($PY_VER)，需要 3.10+"
    fi
    log "Python: $PY_VER"

    # pip
    if ! command -v pip3 &>/dev/null; then
        warn "未找到 pip3，尝试安装..."
        python3 -m ensurepip --upgrade 2>/dev/null || {
            if [[ "$OSTYPE" == "darwin"* ]]; then
                curl https://bootstrap.pypa.io/get-pip.py | python3
            else
                sudo apt-get install -y python3-pip 2>/dev/null || sudo yum install -y python3-pip 2>/dev/null
            fi
        }
    fi
    log "pip3: 已安装"

    # Redis
    step "检查 Redis..."
    if command -v redis-cli &>/dev/null; then
        if redis-cli ping &>/dev/null 2>&1; then
            log "Redis: 运行中"
        else
            warn "Redis 已安装但未运行，尝试启动..."
            if [[ "$OSTYPE" == "darwin"* ]]; then
                brew services start redis 2>/dev/null || redis-server --daemonize yes
            else
                sudo systemctl start redis 2>/dev/null || sudo service redis start 2>/dev/null || redis-server --daemonize yes
            fi
            sleep 2
            redis-cli ping &>/dev/null 2>&1 && log "Redis: 启动成功" || {
                warn "Redis 启动失败，尝试安装..."
                install_redis
            }
        fi
    else
        warn "未找到 Redis，尝试安装..."
        install_redis
    fi

    # Java
    step "检查 Java..."
    if ! command -v java &>/dev/null; then
        warn "未找到 Java，JMeter 需要 Java 8+"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            if command -v brew &>/dev/null; then
                info "使用 Homebrew 安装 Java..."
                brew install openjdk@11 2>/dev/null && log "Java 安装成功" || warn "Java 安装失败"
            fi
        elif command -v apt-get &>/dev/null; then
            sudo apt-get install -y default-jre-headless 2>/dev/null && log "Java 安装成功" || warn "Java 安装失败"
        elif command -v yum &>/dev/null; then
            sudo yum install -y java-11-openjdk 2>/dev/null && log "Java 安装成功" || warn "Java 安装失败"
        fi
    else
        JAVA_VER=$(java -version 2>&1 | head -1 | cut -d'"' -f2)
        log "Java: $JAVA_VER"
    fi

    # 检查 Java 版本是否满足要求
    if command -v java &>/dev/null; then
        JAVA_VER_NUM=$(java -version 2>&1 | head -1 | grep -oE '[0-9]+\.[0-9]+' | head -1)
        JAVA_MAJOR=$(echo $JAVA_VER_NUM | cut -d. -f1)
        if [ "$JAVA_MAJOR" -lt 8 ]; then
            warn "Java 版本过低 ($JAVA_VER_NUM)，建议升级到 8+"
        fi
    fi
}

install_redis() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &>/dev/null; then
            brew install redis && brew services start redis
        else
            error "请先安装 Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
        fi
    elif command -v apt-get &>/dev/null; then
        sudo apt-get update && sudo apt-get install -y redis-server && sudo systemctl start redis && sudo systemctl enable redis
    elif command -v yum &>/dev/null; then
        sudo yum install -y redis && sudo systemctl start redis && sudo systemctl enable redis
    else
        error "无法自动安装 Redis，请手动安装"
    fi
    log "Redis: 安装并启动"
}

#============================================================
# 安装依赖
#============================================================
install_deps() {
    info "安装 Python 依赖..."
    
    # 选择最快的镜像源
    select_fastest_mirror
    setup_pip_mirror
    
    # 安装 Manager 依赖
    step "安装 Manager 依赖..."
    if pip_install "manager/requirements.txt"; then
        log "Manager 依赖安装完成"
    else
        warn "Manager 依赖安装有问题，尝试逐个安装..."
        pip_install_one_by_one "manager/requirements.txt"
    fi
    
    # 安装 Agent 依赖
    step "安装 Agent 依赖..."
    if pip_install "agent/requirements.txt"; then
        log "Agent 依赖安装完成"
    else
        warn "Agent 依赖安装有问题，尝试逐个安装..."
        pip_install_one_by_one "agent/requirements.txt"
    fi
    
    # 验证关键依赖
    step "验证关键依赖..."
    local critical_deps=("fastapi" "uvicorn" "redis" "httpx" "pymobiledevice3")
    for dep in "${critical_deps[@]}"; do
        if python3 -c "import $dep" 2>/dev/null; then
            log "$dep: OK"
        else
            warn "$dep: 未安装，尝试单独安装..."
            pip3 install "$dep" -q 2>/dev/null && log "$dep: 安装成功" || warn "$dep: 安装失败"
        fi
    done
}

pip_install_one_by_one() {
    local req_file=$1
    local success=0
    local fail=0
    
    while IFS= read -r line; do
        # 跳过空行和注释
        [[ -z "$line" || "$line" =~ ^# ]] && continue
        # 去除版本号
        local pkg=$(echo "$line" | sed 's/[>=<!\[].*//')
        
        if pip3 install "$pkg" -q 2>/dev/null; then
            success=$((success + 1))
        else
            fail=$((fail + 1))
            warn "安装失败: $pkg"
        fi
    done < "$req_file"
    
    info "依赖安装完成: 成功 $success, 失败 $fail"
}

#============================================================
# 配置 JMeter
#============================================================
setup_jmeter() {
    info "配置 JMeter..."

    # 创建符号链接
    [ -L /tmp/jmeter ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3" /tmp/jmeter
    [ -L /tmp/jmeter-slave ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3-slave" /tmp/jmeter-slave

    # 检查 slave2/slave3
    for i in 2 3; do
        if [ -d "$SCRIPT_DIR/apache-jmeter-5.6.3-slave$i" ]; then
            [ -L /tmp/jmeter-slave$i ] || ln -sf "$SCRIPT_DIR/apache-jmeter-5.6.3-slave$i" /tmp/jmeter-slave$i
        fi
    done

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
    mkdir -p scripts reports config/csv logs
    log "目录初始化完成"
}

#============================================================
# 启动服务
#============================================================
start_manager() {
    if pgrep -f "manager.main" >/dev/null 2>&1; then
        warn "Manager 已运行，尝试重启..."
        pkill -f "manager.main" 2>/dev/null
        sleep 2
    fi

    info "启动 Manager..."
    cd "$SCRIPT_DIR"
    nohup python3 -m manager.main > /tmp/manager.log 2>&1 &
    
    # 等待启动
    local retries=0
    while [ $retries -lt 15 ]; do
        sleep 1
        if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
            log "Manager 启动成功 (http://localhost:8000)"
            return 0
        fi
        retries=$((retries + 1))
    done
    
    error "Manager 启动失败，查看 /tmp/manager.log"
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
        warn "Agent 启动失败，查看 /tmp/agent.log"
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

    # 启动 Slave2 (1200)
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

    # 启动 Slave3 (1300)
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
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo -e "${BLUE}          服务状态检查${NC}"
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo ""

    # Manager
    if curl -s http://localhost:8000/api/health >/dev/null 2>&1; then
        echo -e "  Manager:  ${GREEN}● 运行中${NC}  http://localhost:8000"
    else
        echo -e "  Manager:  ${RED}○ 未运行${NC}"
    fi

    # Agent
    if pgrep -f "agent.main" >/dev/null 2>&1; then
        echo -e "  Agent:    ${GREEN}● 运行中${NC}"
    else
        echo -e "  Agent:    ${RED}○ 未运行${NC}"
    fi

    # Slaves
    for port in 1100 1200 1300; do
        if lsof -i :$port >/dev/null 2>&1; then
            echo -e "  Slave:$port ${GREEN}● 运行中${NC}"
        else
            echo -e "  Slave:$port ${RED}○ 未运行${NC}"
        fi
    done

    # Redis
    if redis-cli ping >/dev/null 2>&1; then
        echo -e "  Redis:    ${GREEN}● 运行中${NC}"
    else
        echo -e "  Redis:    ${RED}○ 未运行${NC}"
    fi

    # 节点
    if curl -s http://localhost:8000/api/registry/ >/dev/null 2>&1; then
        NODES=$(curl -s http://localhost:8000/api/registry/ | python3 -c "import sys,json; d=json.load(sys.stdin); verified=sum(1 for n in d['nodes'] if n['status']=='verified'); print(f'{verified}/{d[\"total\"]}')" 2>/dev/null)
        echo -e "  节点:     ${GREEN}● $NODES 已验证${NC}"
    fi

    # iOS 设备
    if curl -s http://localhost:8000/api/mobile/detect >/dev/null 2>&1; then
        PLATFORM=$(curl -s http://localhost:8000/api/mobile/detect | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('platform','none'))" 2>/dev/null)
        if [ "$PLATFORM" != "none" ]; then
            echo -e "  移动设备: ${GREEN}● 已连接 ($PLATFORM)${NC}"
        else
            echo -e "  移动设备: ${YELLOW}○ 未检测到${NC}"
        fi
    fi

    echo ""
    echo -e "${BLUE}════════════════════════════════════════${NC}"
    echo ""
}

#============================================================
# 一键修复
#============================================================
fix_all() {
    info "开始自动修复..."
    
    # 1. 修复 pip 源
    step "配置 pip 镜像源..."
    select_fastest_mirror
    setup_pip_mirror
    
    # 2. 修复缺失的依赖
    step "检查并修复缺失依赖..."
    local all_deps=$(cat manager/requirements.txt agent/requirements.txt 2>/dev/null | grep -v '^#' | grep -v '^$' | sed 's/[>=<!\[].*//' | sort -u)
    
    local fixed=0
    local failed=0
    for dep in $all_deps; do
        if ! python3 -c "import $dep" 2>/dev/null; then
            printf "  安装 %-30s " "$dep"
            if pip3 install "$dep" -q 2>/dev/null; then
                echo "${GREEN}✓${NC}"
                fixed=$((fixed + 1))
            else
                echo "${RED}✗${NC}"
                failed=$((failed + 1))
            fi
        fi
    done
    
    if [ $fixed -gt 0 ]; then
        log "修复了 $fixed 个缺失依赖"
    fi
    if [ $failed -gt 0 ]; then
        warn "$failed 个依赖安装失败"
    fi
    
    # 3. 修复 Redis
    step "检查 Redis..."
    if ! redis-cli ping >/dev/null 2>&1; then
        warn "Redis 未运行，尝试启动..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            brew services start redis 2>/dev/null || redis-server --daemonize yes
        else
            sudo systemctl start redis 2>/dev/null || sudo service redis start 2>/dev/null || redis-server --daemonize yes
        fi
        sleep 2
        redis-cli ping >/dev/null 2>&1 && log "Redis 启动成功" || warn "Redis 启动失败"
    else
        log "Redis: 运行中"
    fi
    
    # 4. 清理旧进程
    step "清理旧进程..."
    pkill -f "pymobiledevice3" 2>/dev/null
    pkill -f "sysmon" 2>/dev/null
    rm -f /tmp/solox_ios_monitor.jsonl 2>/dev/null
    log "旧进程已清理"
    
    # 5. 重启服务
    step "重启服务..."
    stop_all 2>/dev/null
    sleep 2
    
    start_manager
    start_agent
    
    show_status
    log "修复完成！"
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
    rm -rf scripts/* reports/* config/csv/*
    redis-cli FLUSHDB 2>/dev/null
    log "清理完成"
}

#============================================================
# 主入口
#============================================================
main() {
    echo ""
    echo -e "${CYAN}╔══════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║     性能测试平台 - 智能部署工具          ║${NC}"
    echo -e "${CYAN}╚══════════════════════════════════════════╝${NC}"
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
            echo -e "${GREEN}══════════════════════════════════════════${NC}"
            echo -e "${GREEN}  部署完成！访问 http://localhost:8000${NC}"
            echo -e "${GREEN}══════════════════════════════════════════${NC}"
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
        fix)
            fix_all
            ;;
        *)
            echo "用法: bash deploy.sh [start|stop|restart|status|clean|fix]"
            echo ""
            echo "  start   - 一键部署并启动所有服务"
            echo "  stop    - 停止所有服务"
            echo "  restart - 重启所有服务"
            echo "  status  - 查看服务状态"
            echo "  clean   - 清理所有数据"
            echo "  fix     - 一键修复（检查依赖、切换镜像、重启服务）"
            ;;
    esac
}

main "$@"

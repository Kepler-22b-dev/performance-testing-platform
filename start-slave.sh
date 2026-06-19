#!/bin/bash
# JMeter Slave 启动脚本
# 用法: ./start-slave.sh [port]

SLAVE_DIR="/tmp/jmeter-slave"
PORT=${1:-1100}
LOCAL_IP=$(ipconfig getifaddr en0 2>/dev/null || echo "127.0.0.1")

echo "Starting JMeter Slave on port $PORT..."
echo "JMeter Home: $SLAVE_DIR"
echo "Binding to: $LOCAL_IP"

export SERVER_PORT=$PORT

exec "$SLAVE_DIR/bin/jmeter-server" \
    -Dserver_port=$PORT \
    -Dserver.rmi.ssl.disable=true \
    -Djava.rmi.server.hostname=$LOCAL_IP

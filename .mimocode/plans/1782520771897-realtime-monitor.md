# Plan: 实时压测监控曲线

## Context
用户需要在压测过程中实时监控数据，包括响应时间、TPS、并发用户数曲线，支持可选刷新间隔，X 轴为标准时间，并发用户曲线需同步展示设计的压测曲线。

## 现状分析
- **已有基础**：WebSocket 实时推送进度、ECharts 图表、基础 TPS/RT 曲线
- **缺失**：无并发用户曲线、无刷新间隔选择器、进度数据只存最新一条、无历史数据 API

## 修改文件

### 1. `manager/core/scheduler.py` — 进度历史缓冲
- 在 `handle_progress()` 中将每次进度追加到任务的历史列表（ring buffer，最多 3600 条）
- 新增 `get_progress_history(task_id)` 方法返回历史数据

### 2. `manager/api/tasks.py` — 新增历史 API
- `GET /api/tasks/{task_id}/progress/history` 返回进度历史数组

### 3. `manager/static/index.html` — 前端实时监控重写
重写 `renderLiveMonitor` 和 `renderLiveCharts`：

**布局改为 2x2 网格**：
- 左上：TPS 趋势
- 右上：响应时间趋势
- 左下：并发用户数（含设计曲线参考线）
- 右下：错误率趋势

**新增功能**：
- 刷新间隔选择器（1s / 5s / 10s / 30s），通过 setInterval 控制 WebSocket 消息节流
- X 轴使用 `MM:SS` 格式，基于任务 start_time 的相对时间
- 并发用户曲线：从 jmeter_args 中的 threads/ramp_time/duration 计算设计曲线，作为参考虚线
- 图表复用：ECharts 实例只创建一次，后续只 updateOption
- 数据点上限 600 个（10 分钟 @1s 刷新）

## 验证
1. 重启后端，创建一个短时任务（duration=30）
2. 打开任务详情，观察实时曲线是否正常显示
3. 切换刷新间隔，验证节流生效
4. 验证并发用户曲线与设计曲线一致

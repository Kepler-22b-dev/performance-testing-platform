# Samples 聚合表设计备注

## 目标

当前报告以 JTL 文件为准，适合短期落地和兼容 JMeter 原始结果。随着脚本、任务和采样数据增长，后续需要把原始 samples 解析成可查询的聚合数据，避免每次打开报告都重复扫描大文件。

## 建议表结构

### `sample_aggregates`

按任务、接口、秒级时间桶聚合。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `id` | bigint | 自增主键 |
| `task_id` | varchar(64) | 任务编号 |
| `agent_id` | varchar(64) | 施压节点 |
| `segment_id` | varchar(128) | 压力段，基础任务为 `base` |
| `label` | varchar(512) | JMeter sampler label |
| `bucket_ts` | bigint | 秒级时间桶，毫秒时间戳 |
| `sample_count` | int | 请求数 |
| `error_count` | int | 失败请求数 |
| `elapsed_sum` | bigint | 响应时间总和 |
| `elapsed_min` | int | 最小响应时间 |
| `elapsed_max` | int | 最大响应时间 |
| `latency_sum` | bigint | latency 总和 |
| `connect_sum` | bigint | connect time 总和 |
| `bytes_received` | bigint | 接收字节数 |
| `bytes_sent` | bigint | 发送字节数 |
| `status_codes` | json | 响应码计数 |
| `created_at` | float | 写入时间 |

建议索引：

- `(task_id, bucket_ts)`
- `(task_id, label, bucket_ts)`
- `(task_id, agent_id, segment_id, bucket_ts)`

### `sample_label_stats`

按任务、接口保存最终统计，支撑报告表格和趋势查询。

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `task_id` | varchar(64) | 任务编号 |
| `label` | varchar(512) | 接口标签 |
| `sample_count` | int | 请求数 |
| `error_count` | int | 错误数 |
| `avg_rt` | float | 平均响应时间 |
| `min_rt` | int | 最小响应时间 |
| `max_rt` | int | 最大响应时间 |
| `p50` / `p90` / `p95` / `p99` | int | 百分位 |
| `stable_qps` | float | 稳定期 QPS |
| `max_qps` | float | 峰值 QPS |
| `total_bytes` | bigint | 总流量 |

## 写入策略

1. 任务完成时解析 `reports/{task_id}/**/result.jtl`，递归包含 `segments/`。
2. 解析时按秒聚合写入 `sample_aggregates`，原始 JTL 仍保留用于审计和重新解析。
3. 聚合完成后写入 `sample_label_stats` 和任务级 summary。
4. 报告接口优先读聚合表；聚合缺失时回退到 JTL 文件解析。

## 迁移步骤

1. 先新增表，不改变现有报告逻辑。
2. 增加后台重建命令：按任务扫描历史 JTL 并生成聚合。
3. 报告接口增加 `prefer_aggregate=true` 的内部开关。
4. 验证新旧统计误差后，把报告默认切到聚合表。
5. 保留 JTL 文件清理策略：近期任务保留原始 JTL，历史任务只保留聚合和导出报告。

## 注意事项

- 百分位不能只靠秒级聚合精确计算；短期可以保留任务级样本解析计算最终百分位，中长期可引入 t-digest 或 HDR Histogram。
- 动态调压必须保留 `segment_id`，否则无法解释运行中加压/降压后的曲线变化。
- 采集落库应在 Agent 完成后异步执行，不要在压测请求链路内同步写数据库，避免影响测试结果。

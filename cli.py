#!/usr/bin/env python3
"""
性能测试平台 - 命令行工具
支持 CI/CD 集成，可在 Jenkins/GitHub Actions 等流水线中使用

用法:
    python cli.py run --script <script_id> --threads 10 --duration 60
    python cli.py status --task <task_id>
    python cli.py wait --task <task_id> --timeout 600
    python cli.py report --task <task_id> --format html/pdf
    python cli.py list-scripts
    python cli.py list-tasks
"""

import argparse
import sys
import os
import time
import json
import requests

sys.path.insert(0, os.path.dirname(__file__))


class PerfTestCLI:
    def __init__(self, base_url: str = None):
        self.base_url = base_url or os.getenv("PERFTEST_URL", "http://localhost:8000")
        self.api = f"{self.base_url}/api"

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.api}{path}"
        try:
            resp = requests.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.ConnectionError:
            print(f"错误: 无法连接到 {self.base_url}", file=sys.stderr)
            sys.exit(1)
        except requests.exceptions.HTTPError as e:
            data = e.response.json() if e.response.text else {}
            print(f"错误: {data.get('detail', str(e))}", file=sys.stderr)
            sys.exit(1)

    def run(self, args):
        data = {
            "script_id": args.script,
            "threads": args.threads,
            "ramp_time": args.ramp,
            "duration": args.duration,
            "timeout": args.timeout,
            "distributed": args.distributed,
        }
        if args.csv:
            data["csv_file"] = args.csv
        if args.csv_vars:
            data["csv_variable_names"] = args.csv_vars
        if args.agents:
            data["target_agents"] = args.agents.split(",")

        result = self._request("POST", "/tasks/quick-run", json=data)
        print(f"任务已启动: {result['task_id']}")
        return result["task_id"]

    def status(self, args):
        result = self._request("GET", f"/tasks/{args.task}")
        status = result["status"]
        print(f"任务: {result['task_id']}")
        print(f"状态: {status}")
        print(f"脚本: {result.get('script_id', '-')}")

        if result.get("results"):
            for agent_id, r in result["results"].items():
                s = r.get("summary", {})
                if s.get("total_samples"):
                    print(f"  {agent_id}: {s['total_samples']} 请求, 平均 {s.get('avg_response_time', 0)}ms, 错误率 {s.get('error_rate', 0)}%")

        if status == "completed":
            return 0
        elif status == "failed":
            return 1
        elif status == "running":
            return 2
        else:
            return 3

    def wait(self, args):
        timeout = args.timeout
        poll_interval = args.interval
        start = time.time()

        print(f"等待任务 {args.task} 完成 (超时: {timeout}s)...")

        while time.time() - start < timeout:
            result = self._request("GET", f"/tasks/{args.task}")
            status = result["status"]

            if status == "completed":
                print("任务已完成")
                self._print_summary(result)
                return 0
            elif status == "failed":
                print("任务失败")
                self._print_summary(result)
                return 1
            elif status == "stopped":
                print("任务已停止")
                return 2

            elapsed = int(time.time() - start)
            print(f"  [{elapsed}s] 状态: {status}")
            time.sleep(poll_interval)

        print(f"超时 ({timeout}s)")
        return 1

    def _print_summary(self, result):
        if result.get("results"):
            for agent_id, r in result["results"].items():
                s = r.get("summary", {})
                if s.get("total_samples"):
                    print(f"  {agent_id}:")
                    print(f"    总请求: {s['total_samples']}")
                    print(f"    平均RT: {s.get('avg_response_time', 0)}ms")
                    print(f"    P99: {s.get('p99', 0)}ms")
                    print(f"    错误率: {s.get('error_rate', 0)}%")

    def report(self, args):
        if args.format == "pdf":
            url = f"{self.api}/results/tasks/{args.task}/export-pdf"
        else:
            url = f"{self.api}/results/tasks/{args.task}/export"

        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()

            ext = "pdf" if args.format == "pdf" else "html"
            filename = args.output or f"report_{args.task}.{ext}"

            with open(filename, "wb") as f:
                f.write(resp.content)

            print(f"报告已保存: {filename}")
            return 0
        except Exception as e:
            print(f"导出失败: {e}", file=sys.stderr)
            return 1

    def list_scripts(self, args):
        result = self._request("GET", "/scripts/")
        scripts = result.get("scripts", [])
        print(f"共 {len(scripts)} 个脚本:")
        for s in scripts:
            print(f"  {s['script_id']:<25} {s['filename']:<30} {s['size']/1024:.1f}KB")

    def list_tasks(self, args):
        result = self._request("GET", "/tasks/")
        tasks = result.get("tasks", [])

        if args.status:
            tasks = [t for t in tasks if t["status"] == args.status]

        tasks.sort(key=lambda x: x.get("created_at", 0), reverse=True)
        tasks = tasks[:args.limit]

        print(f"最近 {len(tasks)} 个任务:")
        for t in tasks:
            created = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.get("created_at", 0)))
            print(f"  {t['task_id']:<25} {t['status']:<12} {t.get('script_id', '-'):<25} {created}")

    def check(self, args):
        thresholds = {
            "avg_rt": args.max_avg_rt,
            "p99": args.max_p99,
            "error_rate": args.max_error_rate,
        }

        result = self._request("GET", f"/tasks/{args.task}")
        if result["status"] != "completed":
            print(f"任务未完成: {result['status']}")
            return 1

        summary = {}
        for agent_id, r in result.get("results", {}).items():
            s = r.get("summary", {})
            if s.get("total_samples"):
                summary = s
                break

        if not summary:
            print("无结果数据")
            return 1

        failed = []
        avg_rt = summary.get("avg_response_time", 0)
        p99 = summary.get("p99", 0)
        error_rate = summary.get("error_rate", 0)

        print(f"性能检查:")
        print(f"  平均RT: {avg_rt}ms (阈值: {thresholds['avg_rt']}ms)")
        print(f"  P99: {p99}ms (阈值: {thresholds['p99']}ms)")
        print(f"  错误率: {error_rate}% (阈值: {thresholds['error_rate']}%)")

        if avg_rt > thresholds["avg_rt"]:
            failed.append(f"平均RT {avg_rt}ms > {thresholds['avg_rt']}ms")
        if p99 > thresholds["p99"]:
            failed.append(f"P99 {p99}ms > {thresholds['p99']}ms")
        if error_rate > thresholds["error_rate"]:
            failed.append(f"错误率 {error_rate}% > {thresholds['error_rate']}%")

        if failed:
            print(f"\n未通过: {', '.join(failed)}")
            return 1
        else:
            print("\n全部通过!")
            return 0


def main():
    parser = argparse.ArgumentParser(description="性能测试平台 CLI")
    parser.add_argument("--url", help="平台地址", default=None)
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    p_run = subparsers.add_parser("run", help="启动压测任务")
    p_run.add_argument("--script", required=True, help="脚本 ID")
    p_run.add_argument("--threads", type=int, default=1, help="并发线程数")
    p_run.add_argument("--ramp", type=int, default=1, help="预热时间(秒)")
    p_run.add_argument("--duration", type=int, default=10, help="持续时间(秒)")
    p_run.add_argument("--timeout", type=int, default=300, help="超时时间(秒)")
    p_run.add_argument("--distributed", action="store_true", help="分布式模式")
    p_run.add_argument("--csv", help="CSV 文件路径")
    p_run.add_argument("--csv-vars", help="CSV 变量名(逗号分隔)")
    p_run.add_argument("--agents", help="指定 Agent(逗号分隔)")

    p_status = subparsers.add_parser("status", help="查看任务状态")
    p_status.add_argument("--task", required=True, help="任务 ID")

    p_wait = subparsers.add_parser("wait", help="等待任务完成")
    p_wait.add_argument("--task", required=True, help="任务 ID")
    p_wait.add_argument("--timeout", type=int, default=600, help="超时时间(秒)")
    p_wait.add_argument("--interval", type=int, default=5, help="轮询间隔(秒)")

    p_report = subparsers.add_parser("report", help="导出报告")
    p_report.add_argument("--task", required=True, help="任务 ID")
    p_report.add_argument("--format", choices=["html", "pdf"], default="html", help="报告格式")
    p_report.add_argument("--output", help="输出文件名")

    p_check = subparsers.add_parser("check", help="性能阈值检查")
    p_check.add_argument("--task", required=True, help="任务 ID")
    p_check.add_argument("--max-avg-rt", type=float, default=1000, help="平均RT阈值(ms)")
    p_check.add_argument("--max-p99", type=float, default=3000, help="P99阈值(ms)")
    p_check.add_argument("--max-error-rate", type=float, default=1, help="错误率阈值(%)")

    subparsers.add_parser("list-scripts", help="列出所有脚本")

    p_list = subparsers.add_parser("list-tasks", help="列出任务")
    p_list.add_argument("--status", help="筛选状态")
    p_list.add_argument("--limit", type=int, default=10, help="显示数量")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    cli = PerfTestCLI(base_url=args.url)

    commands = {
        "run": cli.run,
        "status": cli.status,
        "wait": cli.wait,
        "report": cli.report,
        "check": cli.check,
        "list-scripts": cli.list_scripts,
        "list-tasks": cli.list_tasks,
    }

    result = commands[args.command](args)
    sys.exit(result or 0)


if __name__ == "__main__":
    main()

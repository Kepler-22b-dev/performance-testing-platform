"""
JMeter 执行器模块 - 负责实际运行 JMeter 测试
支持 CSV 参数化、分布式测试、实时进度上报
"""
import subprocess
import os
import signal
import time
import csv
import json
import xml.etree.ElementTree as ET
from typing import Optional, Callable


class JMeterRunner:
    """
    JMeter 执行器
    封装 JMeter CLI 命令，提供测试执行、进度监控、结果解析等功能
    """

    def __init__(self, jmeter_home: str):
        self.jmeter_home = jmeter_home
        self.jmeter_bin = os.path.join(jmeter_home, "bin", "jmeter")
        self._process: Optional[subprocess.Popen] = None
        self._result_dir: Optional[str] = None
        self._jtl_offset = 0
        self._jtl_line_count = 0
        self._jtl_error_count = 0
        self._jtl_recent_times = []

    def inject_csv_config(
        self,
        script_path: str,
        csv_file: str,
        variable_names: str = None,
        delimiter: str = ",",
        recycle: bool = True,
        stop_on_eof: bool = False,
    ) -> str:
        """
        注入 CSV Data Set Config 到 JMX 脚本
        在脚本的根 hashTree 下添加 CSV 配置节点

        Args:
            script_path: 原始 JMX 脚本路径
            csv_file: CSV 文件路径
            variable_names: 变量名列表(逗号分隔)
            delimiter: CSV 分隔符
            recycle: 是否循环读取
            stop_on_eof: 读完是否停止线程
        Returns:
            修改后的脚本路径
        """
        try:
            tree = ET.parse(script_path)
            root = tree.getroot()

            root_hash_tree = root.find("hashTree")
            if root_hash_tree is None:
                return script_path

            # 创建 CSVDataSet 元素
            csv_config = ET.SubElement(root_hash_tree, "CSVDataSet")
            csv_config.set("guiclass", "TestBeanGUI")
            csv_config.set("testclass", "CSVDataSet")
            csv_config.set("testname", "CSV Data Set Config")

            ET.SubElement(csv_config, "stringProp", name="filename").text = csv_file

            if variable_names:
                ET.SubElement(csv_config, "stringProp", name="variableNames").text = variable_names

            ET.SubElement(csv_config, "stringProp", name="delimiter").text = delimiter
            ET.SubElement(csv_config, "boolProp", name="recycle").text = str(recycle).lower()
            ET.SubElement(csv_config, "boolProp", name="stopThread").text = str(stop_on_eof).lower()
            ET.SubElement(csv_config, "boolProp", name="ignoreFirstLine").text = "false"
            ET.SubElement(csv_config, "boolProp", name="quotedData").text = "false"
            ET.SubElement(csv_config, "boolProp", name="collapseRows").text = "false"

            csv_hash = ET.SubElement(root_hash_tree, "hashTree")

            modified_path = script_path.replace(".jmx", "_csv.jmx")
            tree.write(modified_path, encoding="UTF-8", xml_declaration=True)

            return modified_path
        except Exception as e:
            print(f"CSV injection failed: {e}")
            return script_path

    def _inject_thread_config(self, script_path: str, threads: int, ramp_time: int, duration: int, scenario: dict = None) -> str:
        """
        动态注入线程组配置到 JMX 脚本
        覆盖 JMX 中的线程数、预热时间、持续时间

        Args:
            script_path: 原始 JMX 脚本路径
            threads: 线程数
            ramp_time: 预热时间(秒)
            duration: 持续时间(秒)
            scenario: 场景配置(可选)
        Returns:
            修改后的脚本路径
        """
        try:
            tree = ET.parse(script_path)
            root = tree.getroot()

            # 遍历所有 ThreadGroup 元素并修改配置
            for thread_group in root.iter("ThreadGroup"):
                num_threads = thread_group.find("intProp[@name='ThreadGroup.num_threads']")
                if num_threads is not None:
                    num_threads.text = str(threads)

                ramp = thread_group.find("intProp[@name='ThreadGroup.ramp_time']")
                if ramp is not None:
                    ramp.text = str(ramp_time)

                dur = thread_group.find("stringProp[@name='ThreadGroup.duration']")
                if dur is not None:
                    dur.text = str(duration)
                else:
                    ET.SubElement(thread_group, "stringProp", name="ThreadGroup.duration").text = str(duration)

                # 启用调度器
                sched = thread_group.find("boolProp[@name='ThreadGroup.scheduler']")
                if sched is not None:
                    sched.text = "true"
                else:
                    ET.SubElement(thread_group, "boolProp", name="ThreadGroup.scheduler").text = "true"

                # 设置无限循环
                loop = thread_group.find(".//intProp[@name='LoopController.loops']")
                if loop is not None:
                    loop.text = "-1"

            modified_path = script_path.replace(".jmx", "_exec.jmx")
            tree.write(modified_path, encoding="UTF-8", xml_declaration=True)
            return modified_path
        except Exception as e:
            print(f"Thread config injection failed: {e}")
            return script_path

    def execute(
        self,
        script_path: str,
        result_dir: str,
        jmeter_args: dict,
        on_progress: Optional[Callable] = None,
        timeout: int = 3600,
        distributed: bool = False,
        remote_hosts: str = None,
        csv_file: str = None,
        csv_variable_names: str = None,
        csv_delimiter: str = ",",
        csv_recycle: bool = True,
        csv_stop_on_eof: bool = False,
    ) -> dict:
        """
        执行 JMeter 测试

        Args:
            script_path: JMX 脚本路径
            result_dir: 结果输出目录
            jmeter_args: JMeter 参数
            on_progress: 进度回调函数
            timeout: 超时时间(秒)
            distributed: 是否分布式模式
            remote_hosts: 远程主机列表
            csv_file: CSV 文件路径
            csv_variable_names: CSV 变量名
            csv_delimiter: CSV 分隔符
            csv_recycle: 是否循环读取
            csv_stop_on_eof: 读完是否停止
        Returns:
            dict: 包含 status, report_path, summary 等
        """
        self._result_dir = result_dir
        os.makedirs(result_dir, exist_ok=True)
        self._jtl_offset = 0
        self._jtl_line_count = 0
        self._jtl_error_count = 0
        self._jtl_recent_times = []

        # 注入 CSV 配置
        if csv_file:
            script_path = self.inject_csv_config(
                script_path,
                csv_file,
                csv_variable_names,
                csv_delimiter,
                csv_recycle,
                csv_stop_on_eof,
            )

        jtl_path = os.path.join(result_dir, "result.xml")
        report_path = os.path.join(result_dir, "html-report")

        # 注入线程组配置
        scenario = None
        if jmeter_args.get("scenario"):
            try:
                scenario = json.loads(jmeter_args["scenario"])
            except Exception:
                pass

        if jmeter_args.get("threads") or jmeter_args.get("duration") or jmeter_args.get("ramp_time"):
            script_path = self._inject_thread_config(
                script_path,
                threads=int(jmeter_args.get("threads", 10)),
                ramp_time=int(jmeter_args.get("ramp_time", 1)),
                duration=int(jmeter_args.get("duration", 60)),
                scenario=scenario,
            )

        # 构建 JMeter 命令
        cmd = [
            self.jmeter_bin,
            "-n",  # 非 GUI 模式
            "-t", script_path,  # 测试脚本
            "-l", jtl_path,     # 结果文件
            "-j", os.path.join(result_dir, "jmeter.log"),  # 日志文件
            "-Jjmeter.save.saveservice.output_format=csv",
            "-Jjmeter.save.saveservice.print_field_names=true",
            "-Jjmeter.save.saveservice.successful=true",
            "-Jjmeter.save.saveservice.label=true",
            "-Jjmeter.save.saveservice.response_code=true",
            "-Jjmeter.save.saveservice.response_message=true",
            "-Jjmeter.save.saveservice.thread_name=true",
            "-Jjmeter.save.saveservice.data_type=true",
            "-Jjmeter.save.saveservice.assertion_results_failure_message=true",
            "-Jjmeter.save.saveservice.bytes=true",
            "-Jjmeter.save.saveservice.sent_bytes=true",
            "-Jjmeter.save.saveservice.url=true",
            "-Jjmeter.save.saveservice.thread_counts=true",
            "-Jjmeter.save.saveservice.idle_time=true",
            "-Jjmeter.save.saveservice.connect_time=true",
            "-Jjmeter.save.saveservice.latency=true",
            "-Jjmeter.save.saveservice.timestamp=true",
        ]

        # 分布式模式
        if distributed:
            if remote_hosts:
                cmd.extend(["-R", remote_hosts])
            else:
                cmd.append("-r")

        # 添加 JMeter 属性参数（跳过已通过 XML 注入的参数和非 JMeter 属性）
        skip_keys = {"threads", "ramp_time", "duration", "scenario"}
        for key, value in jmeter_args.items():
            if key not in skip_keys:
                cmd.extend([f"-J{key}={value}"])

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            start_time = time.time()
            last_progress_time = start_time

            # 主循环：监控进程状态和上报进度
            while self._process.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    self.stop()
                    return {"status": "failed", "error": "timeout"}

                # 每秒上报一次进度
                if on_progress and time.time() - last_progress_time >= 1.0:
                    progress = self._parse_progress(jtl_path)
                    on_progress(progress)
                    last_progress_time = time.time()

                time.sleep(0.5)

            exit_code = self._process.returncode
            summary = self._parse_final_result(jtl_path)

            if exit_code != 0:
                stderr = self._process.stderr.read().decode()
                return {"status": "failed", "error": stderr, "summary": summary}

            # 生成 HTML 报告
            self._generate_report(jtl_path, report_path)

            return {
                "status": "completed",
                "report_path": report_path,
                "summary": summary,
            }

        except Exception as e:
            self.stop()
            return {"status": "failed", "error": str(e)}

    def stop(self):
        """停止 JMeter 进程(包括子进程)"""
        if self._process and self._process.poll() is None:
            try:
                self._process.send_signal(signal.SIGINT)
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            except ProcessLookupError:
                pass

            import psutil
            try:
                parent = psutil.Process(self._process.pid)
                children = parent.children(recursive=True)
                for child in children:
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
            except (psutil.NoSuchProcess, OSError):
                pass

            finally:
                self._process = None

    def _parse_progress(self, jtl_path: str) -> dict:
        result = {
            "total_samples": 0,
            "error_count": 0,
            "elapsed_times": [],
            "timestamps": [],
            "bytes_received": 0,
            "avg_latency": 0,
            "avg_connect_time": 0,
        }
        if not os.path.exists(jtl_path):
            return result

        try:
            file_size = os.path.getsize(jtl_path)
            if file_size == 0:
                return result

            with open(jtl_path, "r", encoding="utf-8", errors="replace") as f:
                if self._jtl_offset == 0:
                    first_line = f.readline().strip()
                    self._jtl_offset = f.tell()
                    is_xml = first_line.startswith("<?xml") or first_line.startswith("<testResults")
                    self._jtl_is_xml = is_xml
                else:
                    f.seek(self._jtl_offset)

                new_count = 0
                new_errors = 0
                new_times = []
                new_ts = []
                total_bytes = 0
                total_latency = 0
                total_connect = 0
                latency_count = 0

                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if self._jtl_is_xml:
                        if line.startswith("<httpSample ") or line.startswith("<sample "):
                            new_count += 1
                            t_start = line.find('t="')
                            s_start = line.find(' s="')
                            if t_start > 0:
                                try:
                                    t_val = int(line[t_start+3:line.find('"', t_start+3)])
                                    new_times.append(t_val)
                                except (ValueError, IndexError):
                                    pass
                            if s_start > 0 and ' s="false"' in line[s_start:s_start+10]:
                                new_errors += 1
                            ts_start = line.find('ts="')
                            if ts_start > 0:
                                try:
                                    ts_val = int(line[ts_start+4:line.find('"', ts_start+4)])
                                    new_ts.append(ts_val)
                                except (ValueError, IndexError):
                                    pass
                            by_start = line.find('by="')
                            if by_start > 0:
                                try:
                                    total_bytes += int(line[by_start+4:line.find('"', by_start+4)])
                                except (ValueError, IndexError):
                                    pass
                            lt_start = line.find('lt="')
                            if lt_start > 0:
                                try:
                                    total_latency += int(line[lt_start+4:line.find('"', lt_start+4)])
                                    latency_count += 1
                                except (ValueError, IndexError):
                                    pass
                    else:
                        parts = line.split(",")
                        if len(parts) >= 17:
                            new_count += 1
                            try:
                                new_times.append(int(parts[1]))
                                new_ts.append(int(parts[0]))
                                if parts[3].strip() == "false":
                                    new_errors += 1
                                if len(parts) > 8 and parts[8].isdigit():
                                    total_bytes += int(parts[8])
                                if len(parts) > 13 and parts[13].isdigit():
                                    total_latency += int(parts[13])
                                    latency_count += 1
                            except (ValueError, IndexError):
                                pass

                self._jtl_offset = f.tell()
                self._jtl_line_count += new_count
                self._jtl_error_count += new_errors
                if new_times:
                    self._jtl_recent_times = new_times[-500:]

                result["total_samples"] = self._jtl_line_count
                result["error_count"] = self._jtl_error_count
                result["elapsed_times"] = self._jtl_recent_times
                result["timestamps"] = new_ts
                result["bytes_received"] = total_bytes
                result["avg_latency"] = round(total_latency / latency_count, 2) if latency_count > 0 else 0

        except Exception:
            pass

        return result

    def _parse_final_result(self, jtl_path: str) -> dict:
        """解析最终结果，计算汇总统计"""
        summary = {
            "total_samples": 0,
            "error_count": 0,
            "success_count": 0,
            "error_rate": 0.0,
            "success_rate": 100.0,
            "avg_response_time": 0.0,
            "min_response_time": 0,
            "max_response_time": 0,
            "p50": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
            "throughput": 0.0,
            "total_bytes_received": 0,
            "total_bytes_sent": 0,
            "avg_bytes_per_request": 0,
            "avg_latency": 0,
            "avg_connect_time": 0,
            "response_code_dist": {},
        }

        if not os.path.exists(jtl_path):
            return summary

        try:
            file_size = os.path.getsize(jtl_path)
            if file_size == 0:
                return summary

            with open(jtl_path, "r", encoding="utf-8", errors="replace") as f:
                first_line = f.readline().strip()

                if first_line.startswith("<?xml") or first_line.startswith("<testResults"):
                    summary = self._parse_xml_final(f, summary)
                else:
                    summary = self._parse_csv_final(f, summary)

        except Exception:
            pass

        return summary

    def _parse_xml_final(self, f, summary):
        """解析 XML 格式的最终结果"""
        import xml.etree.ElementTree as ET

        elapsed_times = []
        latency_times = []
        connect_times = []
        bytes_received = 0
        bytes_sent = 0
        error_count = 0
        response_codes = {}
        timestamps = []

        try:
            content = f.read()
            root = ET.fromstring(content)

            for elem in root:
                if elem.tag in ("httpSample", "sample"):
                    attrs = elem.attrib
                    elapsed = int(attrs.get("t", 0))
                    success = attrs.get("s", "true") == "true"
                    ts = int(attrs.get("ts", 0))
                    by = int(attrs.get("by", 0))
                    lt = int(attrs.get("lt", 0))
                    ct = int(attrs.get("ct", 0))
                    rc = attrs.get("rc", "")

                    elapsed_times.append(elapsed)
                    timestamps.append(ts)
                    bytes_received += by
                    latency_times.append(lt)
                    connect_times.append(ct)

                    if not success:
                        error_count += 1

                    response_codes[rc] = response_codes.get(rc, 0) + 1

        except Exception:
            pass

        if elapsed_times:
            elapsed_times.sort()
            duration = (max(timestamps) - min(timestamps)) / 1000 if timestamps and len(timestamps) > 1 else 1
            total = len(elapsed_times)

            summary["total_samples"] = total
            summary["error_count"] = error_count
            summary["success_count"] = total - error_count
            summary["error_rate"] = round(error_count / total * 100, 2) if total > 0 else 0
            summary["success_rate"] = round((total - error_count) / total * 100, 2) if total > 0 else 100.0
            summary["avg_response_time"] = round(sum(elapsed_times) / len(elapsed_times), 2)
            summary["min_response_time"] = min(elapsed_times)
            summary["max_response_time"] = max(elapsed_times)
            summary["p50"] = self._percentile(elapsed_times, 50)
            summary["p90"] = self._percentile(elapsed_times, 90)
            summary["p95"] = self._percentile(elapsed_times, 95)
            summary["p99"] = self._percentile(elapsed_times, 99)
            summary["throughput"] = round(total / duration, 2) if duration > 0 else 0
            summary["total_bytes_received"] = bytes_received
            summary["avg_bytes_per_request"] = round(bytes_received / total) if total > 0 else 0
            summary["avg_latency"] = round(sum(latency_times) / len(latency_times), 2) if latency_times else 0
            summary["avg_connect_time"] = round(sum(connect_times) / len(connect_times), 2) if connect_times else 0
            summary["response_code_dist"] = response_codes

        return summary

    def _parse_csv_final(self, f, summary):
        """解析 CSV 格式的最终结果"""
        elapsed_times = []
        latency_times = []
        connect_times = []
        bytes_received = 0
        bytes_sent = 0
        error_count = 0
        response_codes = {}
        timestamps = []

        for line in f:
            parts = line.strip().split(",")
            if len(parts) >= 4:
                try:
                    elapsed = int(parts[1])
                    success = parts[3].strip() == "true"
                    ts = int(parts[0]) if parts[0].isdigit() else 0
                    elapsed_times.append(elapsed)
                    timestamps.append(ts)

                    if not success:
                        error_count += 1

                    rc = parts[2] if len(parts) > 2 else ""
                    response_codes[rc] = response_codes.get(rc, 0) + 1

                    if len(parts) > 8 and parts[8].isdigit():
                        bytes_received += int(parts[8])
                    if len(parts) > 9 and parts[9].isdigit():
                        bytes_sent += int(parts[9])
                    if len(parts) > 13 and parts[13].isdigit():
                        latency_times.append(int(parts[13]))
                    if len(parts) > 15 and parts[15].isdigit():
                        connect_times.append(int(parts[15]))
                except:
                    pass

        if elapsed_times:
            elapsed_times.sort()
            duration = (max(timestamps) - min(timestamps)) / 1000 if timestamps and len(timestamps) > 1 else 1
            total = len(elapsed_times)

            summary["total_samples"] = total
            summary["error_count"] = error_count
            summary["success_count"] = total - error_count
            summary["error_rate"] = round(error_count / total * 100, 2) if total > 0 else 0
            summary["success_rate"] = round((total - error_count) / total * 100, 2) if total > 0 else 100.0
            summary["avg_response_time"] = round(sum(elapsed_times) / len(elapsed_times), 2)
            summary["min_response_time"] = min(elapsed_times)
            summary["max_response_time"] = max(elapsed_times)
            summary["p50"] = self._percentile(elapsed_times, 50)
            summary["p90"] = self._percentile(elapsed_times, 90)
            summary["p95"] = self._percentile(elapsed_times, 95)
            summary["p99"] = self._percentile(elapsed_times, 99)
            summary["throughput"] = round(total / duration, 2) if duration > 0 else 0
            summary["total_bytes_received"] = bytes_received
            summary["avg_bytes_per_request"] = round(bytes_received / total) if total > 0 else 0
            summary["avg_latency"] = round(sum(latency_times) / len(latency_times), 2) if latency_times else 0
            summary["avg_connect_time"] = round(sum(connect_times) / len(connect_times), 2) if connect_times else 0
            summary["response_code_dist"] = response_codes

        return summary

    def _percentile(self, data: list, p: int) -> int:
        """计算百分位数"""
        if not data:
            return 0
        k = (len(data) - 1) * (p / 100)
        f = int(k)
        c = f + 1
        if c >= len(data):
            return data[f]
        return int(data[f] + (k - f) * (data[c] - data[f]))

    def _generate_report(self, jtl_path: str, report_path: str):
        """使用 JMeter 生成 HTML Dashboard 报告"""
        try:
            result = subprocess.run(
                [
                    self.jmeter_bin,
                    "-g", jtl_path,
                    "-o", report_path,
                ],
                timeout=60,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"JMeter report generation failed: {result.stderr}")
        except Exception as e:
            print(f"JMeter report generation error: {e}")

    @property
    def is_running(self) -> bool:
        """检查 JMeter 进程是否正在运行"""
        return self._process is not None and self._process.poll() is None

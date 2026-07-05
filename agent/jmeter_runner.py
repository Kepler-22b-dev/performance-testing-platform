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
import logging
import socket
import xml.etree.ElementTree as ET
import random
from collections import deque
from typing import Optional, Callable

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from common.utils import fmt_pct, percentile


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
        self._jtl_is_xml = False
        self._jtl_csv_fields = []
        self._progress_callback_error_count = 0
        self._last_progress_callback_error = None
        self.logger = logging.getLogger("agent")

    def _as_bool(self, value, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _parse_heap_mb(self, value) -> Optional[int]:
        if value in (None, ""):
            return None
        try:
            heap_mb = int(value)
        except (TypeError, ValueError):
            return None
        return heap_mb if heap_mb > 0 else None

    def _parse_int_range(self, value, default: int, min_value: int, max_value: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = default
        return max(min_value, min(max_value, parsed))

    def _split_remote_hosts(self, remote_hosts: Optional[str]) -> list[str]:
        if not remote_hosts:
            return []
        return [host.strip() for host in str(remote_hosts).split(",") if host.strip()]

    def _read_jmeter_remote_hosts(self) -> list[str]:
        properties_path = os.path.join(self.jmeter_home, "bin", "jmeter.properties")
        if not os.path.exists(properties_path):
            return []
        try:
            with open(properties_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("remote_hosts="):
                        return self._split_remote_hosts(line.split("=", 1)[1])
        except Exception:
            return []
        return []

    def _parse_remote_host(self, remote_host: str) -> tuple[str, int] | None:
        remote_host = str(remote_host or "").strip()
        if not remote_host:
            return None
        if remote_host.startswith("[") and "]:" in remote_host:
            host, port = remote_host.rsplit("]:", 1)
            host = host[1:]
        elif ":" in remote_host:
            host, port = remote_host.rsplit(":", 1)
        else:
            host, port = remote_host, "1099"
        try:
            return host.strip(), int(port)
        except (TypeError, ValueError):
            return None

    def _is_remote_host_available(self, remote_host: str, timeout: float = 0.5) -> bool:
        parsed = self._parse_remote_host(remote_host)
        if not parsed:
            return False
        host, port = parsed
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    def _resolve_distributed_execution(
        self,
        distributed: bool,
        remote_hosts: Optional[str],
    ) -> tuple[bool, Optional[str], list[str]]:
        """过滤不可用 Slave；全部不可用时回退到本机执行。"""
        if not distributed:
            return False, None, []

        configured_hosts = self._split_remote_hosts(remote_hosts)
        source = "任务配置"
        if not configured_hosts:
            configured_hosts = self._read_jmeter_remote_hosts()
            source = "jmeter.properties"

        if not configured_hosts:
            warning = "分布式模式未找到可用 remote_hosts，已回退为本机执行"
            self.logger.warning(warning)
            return False, None, [warning]

        available_hosts = []
        unavailable_hosts = []
        for host in configured_hosts:
            if self._is_remote_host_available(host):
                available_hosts.append(host)
            else:
                unavailable_hosts.append(host)

        warnings = []
        if unavailable_hosts:
            warnings.append(
                f"{source} 中不可用的 Slave 已跳过: {', '.join(unavailable_hosts)}"
            )

        if available_hosts:
            if unavailable_hosts:
                self.logger.warning(
                    "Some JMeter slaves are unavailable and will be skipped: unavailable=%s, available=%s",
                    unavailable_hosts,
                    available_hosts,
                )
            return True, ",".join(available_hosts), warnings

        warning = (
            f"{source} 中配置的 Slave 全部不可用，已回退为本机执行: "
            + ", ".join(unavailable_hosts)
        )
        self.logger.warning(warning)
        warnings.append(warning)
        return False, None, warnings

    def _final_result_sample_limit(self) -> int:
        """Limit retained response times for final percentile calculation."""
        try:
            limit = int(os.getenv("JMETER_RESULT_SAMPLE_LIMIT", "500000"))
        except (TypeError, ValueError):
            limit = 500000
        return max(10000, limit)

    def _record_percentile_sample(
        self,
        samples: list[int],
        elapsed: int,
        total: int,
        rng: random.Random,
        limit: int,
    ):
        if len(samples) < limit:
            samples.append(elapsed)
            return

        index = rng.randrange(total)
        if index < limit:
            samples[index] = elapsed

    def _apply_stream_summary(
        self,
        summary: dict,
        total: int,
        error_count: int,
        elapsed_sum: int,
        min_elapsed: Optional[int],
        max_elapsed: int,
        percentile_times: list[int],
        min_ts: Optional[int],
        max_ts: Optional[int],
        ts_count: int,
        bytes_received: int,
        bytes_sent: int,
        latency_sum: int,
        latency_count: int,
        connect_sum: int,
        connect_count: int,
        response_codes: dict,
        sample_limit: int,
    ) -> dict:
        if total <= 0:
            return summary

        percentile_times.sort()
        duration = (
            (max_ts - min_ts) / 1000
            if min_ts is not None and max_ts is not None and ts_count > 1 and max_ts > min_ts
            else 1
        )

        summary["total_samples"] = total
        summary["error_count"] = error_count
        summary["success_count"] = total - error_count
        summary["error_rate"] = fmt_pct(error_count / total * 100)
        summary["success_rate"] = fmt_pct((total - error_count) / total * 100)
        summary["avg_response_time"] = round(elapsed_sum / total, 2)
        summary["min_response_time"] = min_elapsed if min_elapsed is not None else 0
        summary["max_response_time"] = max_elapsed
        summary["p50"] = percentile(percentile_times, 50)
        summary["p90"] = percentile(percentile_times, 90)
        summary["p95"] = percentile(percentile_times, 95)
        summary["p99"] = percentile(percentile_times, 99)
        summary["throughput"] = round(total / duration, 2) if duration > 0 else 0
        summary["total_bytes_received"] = bytes_received
        summary["total_bytes_sent"] = bytes_sent
        summary["avg_bytes_per_request"] = round(bytes_received / total)
        summary["avg_latency"] = round(latency_sum / latency_count, 2) if latency_count else 0
        summary["avg_connect_time"] = round(connect_sum / connect_count, 2) if connect_count else 0
        summary["response_code_dist"] = response_codes
        summary["percentiles_approximate"] = total > len(percentile_times)
        summary["percentile_sample_size"] = len(percentile_times)
        summary["percentile_sample_limit"] = sample_limit
        return summary

    def _find_child_hash_tree(self, root, target):
        """Return the hashTree that belongs to a JMeter tree element."""
        for hash_tree in root.iter("hashTree"):
            children = list(hash_tree)
            for index, child in enumerate(children[:-1]):
                if child is target and children[index + 1].tag == "hashTree":
                    return children[index + 1]
        return None

    def _get_image_resource_config(self, scenario: dict = None) -> Optional[dict]:
        if not isinstance(scenario, dict):
            return None

        resource_load = scenario.get("resource_load")
        if not isinstance(resource_load, dict):
            resource_load = {}

        enabled = self._as_bool(resource_load.get("enabled"), False) or scenario.get("type") == "image-load"
        if not enabled:
            return None

        return {
            "max_images_per_response": self._parse_int_range(
                resource_load.get("max_images_per_response"), 6, 1, 50
            ),
            "timeout_ms": self._parse_int_range(resource_load.get("timeout_ms"), 5000, 500, 60000),
            "max_body_chars": self._parse_int_range(
                resource_load.get("max_body_chars"), 2_000_000, 1000, 10_000_000
            ),
            "only_successful_parent": self._as_bool(
                resource_load.get("only_successful_parent"), True
            ),
            "label_prefix": str(resource_load.get("label_prefix") or "IMG").strip()[:40] or "IMG",
        }

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

    def _inject_thread_config(
        self,
        script_path: str,
        threads: int,
        ramp_time: int,
        duration: int,
        scenario: dict = None,
        error_data_path: str = None,
        error_sample_limit: int = 100,
        error_max_body_chars: int = 8192,
    ) -> str:
        """
        动态注入线程组配置到 JMX 脚本
        覆盖 JMX 中的线程数、预热时间、持续时间

        Args:
            script_path: 原始 JMX 脚本路径
            threads: 线程数
            ramp_time: 预热时间(秒)
            duration: 持续时间(秒)
            scenario: 场景配置(可选)
            error_data_path: 错误响应数据输出路径(可选)
        Returns:
            修改后的脚本路径
        """
        try:
            tree = ET.parse(script_path)
            root = tree.getroot()

            image_resource_config = self._get_image_resource_config(scenario)

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

                # 注入 JSR223 PostProcessor 以捕获错误响应数据
                if error_data_path:
                    self._inject_error_response_capture(
                        root,
                        thread_group,
                        error_data_path,
                        error_sample_limit,
                        error_max_body_chars,
                    )

                if image_resource_config:
                    self._inject_image_resource_loader(root, thread_group, image_resource_config)

            modified_path = script_path.replace(".jmx", "_exec.jmx")
            tree.write(modified_path, encoding="UTF-8", xml_declaration=True)
            return modified_path
        except Exception as e:
            print(f"Thread config injection failed: {e}")
            return script_path

    def _inject_error_response_capture(
        self,
        root,
        thread_group,
        error_data_path: str,
        sample_limit: int = 100,
        max_body_chars: int = 8192,
    ):
        """在 ThreadGroup 的 hashTree 中注入 JSR223 PostProcessor，仅在请求失败时捕获响应体。"""
        target_hash_tree = self._find_child_hash_tree(root, thread_group)

        if target_hash_tree is None:
            return

        # 创建 JSR223 PostProcessor
        jsr223 = ET.SubElement(target_hash_tree, "JSR223PostProcessor")
        jsr223.set("guiclass", "TestBeanGUI")
        jsr223.set("testclass", "JSR223PostProcessor")
        jsr223.set("testname", "Error Response Capture")
        jsr223.set("enabled", "true")

        ET.SubElement(jsr223, "stringProp", name="cacheKey").text = "true"
        ET.SubElement(jsr223, "stringProp", name="filename").text = ""
        ET.SubElement(jsr223, "stringProp", name="parameters").text = ""
        ET.SubElement(jsr223, "boolProp", name="executeOnEveryIteration").text = "false"

        safe_path = json.dumps(error_data_path, ensure_ascii=False)
        sample_limit = self._parse_int_range(sample_limit, 100, 0, 10000)
        max_body_chars = self._parse_int_range(max_body_chars, 8192, 256, 262144)
        script_text = "\n".join([
            "import groovy.json.JsonOutput",
            "import java.io.File",
            "",
            "if (!prev.isSuccessful()) {",
            f"    final int sampleLimit = {sample_limit}",
            f"    final int maxBodyChars = {max_body_chars}",
            "    if (sampleLimit <= 0) { return }",
            f"    def file = new File({safe_path})",
            "    def parent = file.getParentFile()",
            "    if (parent != null) { parent.mkdirs() }",
            "    synchronized(file.getAbsolutePath().intern()) {",
            "        def counterKey = '__ptp_error_capture_count_' + file.getAbsolutePath()",
            "        int current = (props.get(counterKey) ?: '0') as int",
            "        if (current >= sampleLimit) { return }",
            "        props.put(counterKey, String.valueOf(current + 1))",
            "        def responseData = prev.getResponseDataAsString() ?: ''",
            "        boolean truncated = false",
            "        if (responseData.length() > maxBodyChars) {",
            "            responseData = responseData.substring(0, maxBodyChars)",
            "            truncated = true",
            "        }",
            "        def data = [",
            "            ts: prev.getStartTime(),",
            "            label: prev.getSampleLabel(),",
            "            responseCode: prev.getResponseCode(),",
            "            responseMessage: prev.getResponseMessage(),",
            "            responseData: responseData,",
            "            truncated: truncated",
            "        ]",
            "        file.append(JsonOutput.toJson(data) + '\\n')",
            "    }",
            "}",
        ])
        ET.SubElement(jsr223, "stringProp", name="script").text = script_text
        ET.SubElement(jsr223, "stringProp", name="scriptLanguage").text = "groovy"

        # 添加对应的空 hashTree
        ET.SubElement(target_hash_tree, "hashTree")

    def _inject_image_resource_loader(self, root, thread_group, config: dict):
        """注入图片资源加载器，从接口响应中提取图片 URL 并同步下载。"""
        target_hash_tree = self._find_child_hash_tree(root, thread_group)
        if target_hash_tree is None:
            return

        jsr223 = ET.SubElement(target_hash_tree, "JSR223PostProcessor")
        jsr223.set("guiclass", "TestBeanGUI")
        jsr223.set("testclass", "JSR223PostProcessor")
        jsr223.set("testname", "Image Resource Loader")
        jsr223.set("enabled", "true")

        ET.SubElement(jsr223, "stringProp", name="cacheKey").text = "true"
        ET.SubElement(jsr223, "stringProp", name="filename").text = ""
        ET.SubElement(jsr223, "stringProp", name="parameters").text = ""
        ET.SubElement(jsr223, "boolProp", name="executeOnEveryIteration").text = "false"

        label_prefix = json.dumps(config["label_prefix"], ensure_ascii=False)
        only_successful = "true" if config["only_successful_parent"] else "false"
        script_lines = [
            "import org.apache.jmeter.samplers.SampleResult",
            "import java.net.HttpURLConnection",
            "import java.net.URL",
            "",
            f"final int maxImages = {config['max_images_per_response']}",
            f"final int timeoutMs = {config['timeout_ms']}",
            f"final int maxBodyChars = {config['max_body_chars']}",
            f"final boolean onlySuccessfulParent = {only_successful}",
            f"final String labelPrefix = {label_prefix}",
            "",
            "if (onlySuccessfulParent && !prev.isSuccessful()) {",
            "    return",
            "}",
            "",
            "def body = prev.getResponseDataAsString()",
            "if (!body) {",
            "    return",
            "}",
            "if (body.length() > maxBodyChars) {",
            "    body = body.substring(0, maxBodyChars)",
            "}",
            "body = body.replace('\\\\/', '/')",
            "def baseUrl = prev.getURL()",
            "def imagePattern = java.util.regex.Pattern.compile('(?i)(https?://[^\\\\s\"<>)}\\\\]]+\\\\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\\\\?[^\\\\s\"<>)}\\\\]]*)?|/[^\\\\s\"<>)}\\\\]]+\\\\.(?:png|jpe?g|gif|webp|bmp|svg)(?:\\\\?[^\\\\s\"<>)}\\\\]]*)?)')",
            "def matcher = imagePattern.matcher(body)",
            "def urls = new LinkedHashSet<String>()",
            "while (matcher.find() && urls.size() < maxImages) {",
            "    def raw = matcher.group(1).replace('&amp;', '&')",
            "    if (raw.startsWith('/') && baseUrl == null) {",
            "        continue",
            "    }",
            "    urls.add(raw.startsWith('/') ? new URL(baseUrl, raw).toString() : raw)",
            "}",
            "",
            "int imageIndex = 0",
            "urls.each { rawUrl ->",
            "    imageIndex++",
            "    def imageUrl = new URL(rawUrl)",
            "    def sample = new SampleResult()",
            "    sample.setSampleLabel(labelPrefix + ' ' + imageIndex + ' - ' + prev.getSampleLabel())",
            "    sample.setURL(imageUrl)",
            "    sample.setDataType(SampleResult.BINARY)",
            "    long bytes = 0",
            "    def conn = null",
            "    def input = null",
            "    sample.sampleStart()",
            "    try {",
            "        conn = imageUrl.openConnection()",
            "        conn.setConnectTimeout(timeoutMs)",
            "        conn.setReadTimeout(timeoutMs)",
            "        conn.setUseCaches(false)",
            "        conn.setRequestProperty('User-Agent', 'Mozilla/5.0 JMeter image resource loader')",
            "        conn.setRequestProperty('Accept', 'image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8')",
            "        if (baseUrl != null) {",
            "            conn.setRequestProperty('Referer', baseUrl.toString())",
            "        }",
            "        int code = 200",
            "        if (conn instanceof HttpURLConnection) {",
            "            code = conn.getResponseCode()",
            "        }",
            "        input = (conn instanceof HttpURLConnection && code >= 400) ? conn.getErrorStream() : conn.getInputStream()",
            "        byte[] buffer = new byte[8192]",
            "        int read = 0",
            "        while (input != null && (read = input.read(buffer)) != -1) {",
            "            bytes += read",
            "        }",
            "        sample.setBytes(bytes)",
            "        sample.setResponseCode(String.valueOf(code))",
            "        sample.setResponseMessage(code >= 200 && code < 400 ? 'OK' : 'Image load failed')",
            "        sample.setSuccessful(code >= 200 && code < 400)",
            "    } catch (Exception ex) {",
            "        sample.setBytes(bytes)",
            "        sample.setResponseCode('599')",
            "        sample.setResponseMessage(ex.getClass().getSimpleName() + ': ' + ex.getMessage())",
            "        sample.setSuccessful(false)",
            "    } finally {",
            "        try { if (input != null) input.close() } catch (ignored) {}",
            "        if (conn instanceof HttpURLConnection) {",
            "            conn.disconnect()",
            "        }",
            "        sample.sampleEnd()",
            "        prev.addSubResult(sample)",
            "    }",
            "}",
        ]
        ET.SubElement(jsr223, "stringProp", name="script").text = "\n".join(script_lines)
        ET.SubElement(jsr223, "stringProp", name="scriptLanguage").text = "groovy"
        ET.SubElement(target_hash_tree, "hashTree")

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
        self._jtl_is_xml = False
        self._jtl_csv_fields = []
        self._progress_callback_error_count = 0
        self._last_progress_callback_error = None

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

        result_format = str(jmeter_args.get("result_format") or "csv").strip().lower()
        if self._as_bool(jmeter_args.get("debug_result_xml"), False):
            result_format = "xml"
        result_format = "xml" if result_format == "xml" else "csv"
        jtl_path = os.path.join(result_dir, "result.xml" if result_format == "xml" else "result.jtl")
        report_path = os.path.join(result_dir, "html-report")
        capture_error_log = self._as_bool(jmeter_args.get("capture_error_log"), True)
        error_sample_limit = self._parse_int_range(jmeter_args.get("error_log_sample_limit"), 100, 0, 10000)
        error_max_body_chars = self._parse_int_range(jmeter_args.get("error_log_max_body_chars"), 8192, 256, 262144)
        jvm_heap_mb = self._parse_heap_mb(jmeter_args.get("jvm_heap_mb"))
        distributed, remote_hosts, execution_warnings = self._resolve_distributed_execution(distributed, remote_hosts)

        # 注入线程组配置
        scenario = None
        if jmeter_args.get("scenario"):
            try:
                scenario = json.loads(jmeter_args["scenario"])
            except Exception:
                pass
        image_resource_config = self._get_image_resource_config(scenario)

        if jmeter_args.get("threads") or jmeter_args.get("duration") or jmeter_args.get("ramp_time"):
            error_data_path = os.path.join(result_dir, "error_responses.jsonl") if capture_error_log else None
            script_path = self._inject_thread_config(
                script_path,
                threads=int(jmeter_args.get("threads", 10)),
                ramp_time=int(jmeter_args.get("ramp_time", 1)),
                duration=int(jmeter_args.get("duration", 60)),
                scenario=scenario,
                error_data_path=error_data_path,
                error_sample_limit=error_sample_limit,
                error_max_body_chars=error_max_body_chars,
            )

        # 构建 JMeter 命令
        cmd = [
            self.jmeter_bin,
            "-n",
            "-t", script_path,
            "-l", jtl_path,
            "-j", os.path.join(result_dir, "jmeter.log"),
            f"-Jjmeter.save.saveservice.output_format={result_format}",
            "-Jjmeter.save.saveservice.print_field_names=true",
            "-Jjmeter.save.saveservice.successful=true",
            "-Jjmeter.save.saveservice.label=true",
            "-Jjmeter.save.saveservice.response_code=true",
            "-Jjmeter.save.saveservice.response_message=true",
            "-Jjmeter.save.saveservice.thread_name=true",
            "-Jjmeter.save.saveservice.assertion_results_failure_message=true",
            "-Jjmeter.save.saveservice.bytes=true",
            "-Jjmeter.save.saveservice.sent_bytes=true",
            "-Jjmeter.save.saveservice.url=true",
            "-Jjmeter.save.saveservice.thread_counts=true",
            "-Jjmeter.save.saveservice.connect_time=true",
            "-Jjmeter.save.saveservice.latency=true",
            "-Jjmeter.save.saveservice.timestamp=true",
        ]

        if image_resource_config:
            cmd.append("-Jjmeter.save.saveservice.subresults=true")

        # 分布式模式
        if distributed:
            if remote_hosts:
                cmd.extend(["-R", remote_hosts])
            else:
                cmd.append("-r")

        # 添加 JMeter 属性参数（跳过已通过 XML 注入的参数和非 JMeter 属性）
        skip_keys = {
            "threads", "ramp_time", "duration", "scenario",
            "jvm_heap_mb", "capture_error_log", "enforce_single_agent_task",
            "result_format", "debug_result_xml",
            "error_log_sample_limit", "error_log_max_body_chars",
        }
        for key, value in jmeter_args.items():
            if key not in skip_keys:
                cmd.extend([f"-J{key}={value}"])

        try:
            env = os.environ.copy()
            if jvm_heap_mb:
                env["HEAP"] = f"-Xms{jvm_heap_mb}m -Xmx{jvm_heap_mb}m"

            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )

            start_time = time.time()
            last_progress_time = start_time

            # 主循环：监控进程状态和上报进度
            while True:
                exit_code = self._process.poll()
                if exit_code is not None:
                    break

                elapsed = time.time() - start_time
                if elapsed > timeout:
                    self.stop()
                    return {"status": "failed", "error": "timeout"}

                # 每秒上报一次进度
                if on_progress and time.time() - last_progress_time >= 1.0:
                    try:
                        progress = self._parse_progress(jtl_path)
                        on_progress(progress)
                    except Exception as progress_error:
                        self._progress_callback_error_count += 1
                        self._last_progress_callback_error = str(progress_error)
                        if (
                            self._progress_callback_error_count <= 3
                            or self._progress_callback_error_count % 30 == 0
                        ):
                            self.logger.warning(
                                "JMeter progress callback failed, test process will keep running: %s",
                                progress_error,
                                exc_info=True,
                            )
                    last_progress_time = time.time()

                time.sleep(0.5)

            exit_code = self._process.returncode

            # 等待文件系统刷新，确保结果文件写入完成
            time.sleep(1)

            if result_format == "xml":
                self._ensure_xml_complete(jtl_path)

            summary = self._parse_final_result(jtl_path)
            if execution_warnings:
                summary = dict(summary or {})
                summary["execution_warnings"] = execution_warnings

            if exit_code != 0:
                return {
                    "status": "failed",
                    "error": f"JMeter exit code {exit_code}",
                    "summary": summary,
                    "warnings": execution_warnings,
                }

            # 生成 HTML 报告
            self._generate_report(jtl_path, report_path, jmeter_args)

            return {
                "status": "completed",
                "report_path": report_path,
                "summary": summary,
                "warnings": execution_warnings,
            }

        except Exception as e:
            self.stop()
            return {"status": "failed", "error": str(e)}

    def _ensure_xml_complete(self, jtl_path: str):
        """确保 XML 结果文件有正确的闭合标签。"""
        if not os.path.exists(jtl_path):
            return
        try:
            file_size = os.path.getsize(jtl_path)
            if file_size == 0:
                return

            # 根据文件大小决定检查范围：小文件读全部，大文件读最后 2KB
            check_size = min(file_size, 2048)
            with open(jtl_path, "rb") as f:
                f.seek(-check_size, 2)
                tail = f.read().decode("utf-8", errors="replace").strip()

            if tail.endswith("</testResults>"):
                return
            if not tail:
                return

            # 文件未正确关闭，追加闭合标签
            with open(jtl_path, "a", encoding="utf-8") as f:
                f.write("\n</testResults>\n")
        except Exception:
            pass

    def stop(self):
        """停止 JMeter 进程(包括子进程)"""
        process = self._process
        if not process:
            return

        try:
            if process.poll() is None:
                children = []
                try:
                    import psutil
                    try:
                        children = psutil.Process(process.pid).children(recursive=True)
                    except (psutil.NoSuchProcess, OSError):
                        children = []
                except ImportError:
                    psutil = None

                try:
                    self._send_process_signal(process, signal.SIGINT)
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._send_process_signal(process, signal.SIGKILL)
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                except ProcessLookupError:
                    pass

                if psutil:
                    for child in children:
                        try:
                            if child.is_running():
                                child.kill()
                        except psutil.NoSuchProcess:
                            pass
        finally:
            self._process = None

    def _send_process_signal(self, process: subprocess.Popen, sig: signal.Signals):
        """向 JMeter 独立进程组发信号，无法定位进程组时退回到父进程。"""
        try:
            pgid = os.getpgid(process.pid)
            if pgid != os.getpgrp():
                os.killpg(pgid, sig)
                return
        except (AttributeError, ProcessLookupError, PermissionError, OSError):
            pass
        process.send_signal(sig)

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
                    if not is_xml:
                        self._jtl_csv_fields = next(csv.reader([first_line]), [])
                else:
                    f.seek(self._jtl_offset)

                new_count = 0
                new_errors = 0
                new_times = deque(maxlen=500)
                new_ts = deque(maxlen=500)
                total_bytes = 0
                total_latency = 0
                total_connect = 0
                latency_count = 0
                connect_count = 0

                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    if self._jtl_is_xml:
                        if line.startswith("<httpSample ") or line.startswith("<sample "):
                            new_count += 1

                            # 使用更健壮的属性解析
                            attrs = self._parse_xml_attrs(line)

                            t_val = attrs.get("t")
                            if t_val is not None:
                                try:
                                    new_times.append(int(t_val))
                                except (ValueError, TypeError):
                                    pass

                            if attrs.get("s") == "false":
                                new_errors += 1

                            ts_val = attrs.get("ts")
                            if ts_val is not None:
                                try:
                                    new_ts.append(int(ts_val))
                                except (ValueError, TypeError):
                                    pass

                            by_val = attrs.get("by")
                            if by_val is not None:
                                try:
                                    total_bytes += int(by_val)
                                except (ValueError, TypeError):
                                    pass

                            lt_val = attrs.get("lt")
                            if lt_val is not None:
                                try:
                                    total_latency += int(lt_val)
                                    latency_count += 1
                                except (ValueError, TypeError):
                                    pass

                            ct_val = attrs.get("ct")
                            if ct_val is not None:
                                try:
                                    total_connect += int(ct_val)
                                    connect_count += 1
                                except (ValueError, TypeError):
                                    pass
                    else:
                        try:
                            parts = next(csv.reader([line]))
                            row = {
                                field: parts[index]
                                for index, field in enumerate(self._jtl_csv_fields)
                                if index < len(parts)
                            }
                        except Exception:
                            continue

                        new_count += 1
                        new_times.append(self._csv_int(row, "elapsed"))
                        ts_value = self._csv_int(row, "timeStamp")
                        if ts_value:
                            new_ts.append(ts_value)
                        if not self._csv_bool(row, "success", True):
                            new_errors += 1

                        total_bytes += self._csv_int(row, "bytes")
                        total_latency += self._csv_int(row, "Latency")
                        total_connect += self._csv_int(row, "Connect")
                        latency_count += 1
                        connect_count += 1

                self._jtl_offset = f.tell()
                self._jtl_line_count += new_count
                self._jtl_error_count += new_errors
                if new_times:
                    self._jtl_recent_times = list(new_times)

                result["total_samples"] = self._jtl_line_count
                result["error_count"] = self._jtl_error_count
                result["elapsed_times"] = self._jtl_recent_times
                result["timestamps"] = list(new_ts)
                result["bytes_received"] = total_bytes
                result["avg_latency"] = round(total_latency / latency_count, 2) if latency_count > 0 else 0
                result["avg_connect_time"] = round(total_connect / connect_count, 2) if connect_count > 0 else 0

        except Exception:
            pass

        return result

    def _parse_xml_attrs(self, line: str) -> dict:
        """从 XML 行中解析属性，支持带命名空间的属性名（如 lt, ct, sby 等）。"""
        import re
        attrs = {}
        # 匹配所有 name="value" 形式的属性
        for match in re.finditer(r'(\w+)="([^"]*)"', line):
            attrs[match.group(1)] = match.group(2)
        return attrs

    def _csv_get(self, row: dict, key: str, default: str = "") -> str:
        if key in row and row[key] is not None:
            return row[key]
        target = key.lower()
        for row_key, value in row.items():
            if str(row_key).lower() == target:
                return value if value is not None else default
        return default

    def _csv_int(self, row: dict, key: str, default: int = 0) -> int:
        value = self._csv_get(row, key, "")
        try:
            return int(float(value)) if str(value).strip() else default
        except (TypeError, ValueError):
            return default

    def _csv_bool(self, row: dict, key: str, default: bool = False) -> bool:
        value = self._csv_get(row, key, None)
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

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

            with open(jtl_path, "r", encoding="utf-8", errors="replace", newline="") as f:
                first_line = f.readline().strip()

                if first_line.startswith("<?xml") or first_line.startswith("<testResults"):
                    summary = self._parse_xml_final(jtl_path, summary)
                else:
                    summary = self._parse_csv_final(f, summary, first_line)

        except Exception as e:
            print(f"解析 JTL 结果失败: {e}")

        return summary

    def _parse_xml_final(self, xml_path, summary):
        """解析 XML 格式的最终结果（流式，支持大文件）"""
        import xml.etree.ElementTree as ET

        sample_limit = self._final_result_sample_limit()
        rng = random.Random(0)
        percentile_times = []
        total = 0
        elapsed_sum = 0
        min_elapsed = None
        max_elapsed = 0
        latency_sum = 0
        latency_count = 0
        connect_sum = 0
        connect_count = 0
        bytes_received = 0
        error_count = 0
        response_codes = {}
        min_ts = None
        max_ts = None
        ts_count = 0

        try:
            for event, elem in ET.iterparse(xml_path, events=("end",)):
                if elem.tag in ("httpSample", "sample"):
                    attrs = elem.attrib
                    elapsed = int(attrs.get("t", 0))
                    success = attrs.get("s", "true") == "true"
                    ts = int(attrs.get("ts", 0))
                    by = int(attrs.get("by", 0))
                    lt = int(attrs.get("lt", 0))
                    ct = int(attrs.get("ct", 0))
                    rc = attrs.get("rc", "")

                    total += 1
                    elapsed_sum += elapsed
                    min_elapsed = elapsed if min_elapsed is None else min(min_elapsed, elapsed)
                    max_elapsed = max(max_elapsed, elapsed)
                    self._record_percentile_sample(percentile_times, elapsed, total, rng, sample_limit)

                    if ts:
                        min_ts = ts if min_ts is None else min(min_ts, ts)
                        max_ts = ts if max_ts is None else max(max_ts, ts)
                        ts_count += 1
                    bytes_received += by
                    latency_sum += lt
                    latency_count += 1
                    connect_sum += ct
                    connect_count += 1

                    if not success:
                        error_count += 1

                    response_codes[rc] = response_codes.get(rc, 0) + 1

                elem.clear()
        except Exception:
            pass

        return self._apply_stream_summary(
            summary,
            total=total,
            error_count=error_count,
            elapsed_sum=elapsed_sum,
            min_elapsed=min_elapsed,
            max_elapsed=max_elapsed,
            percentile_times=percentile_times,
            min_ts=min_ts,
            max_ts=max_ts,
            ts_count=ts_count,
            bytes_received=bytes_received,
            bytes_sent=0,
            latency_sum=latency_sum,
            latency_count=latency_count,
            connect_sum=connect_sum,
            connect_count=connect_count,
            response_codes=response_codes,
            sample_limit=sample_limit,
        )

    def _parse_csv_final(self, f, summary, header_line=None):
        """解析 CSV 格式的最终结果"""
        sample_limit = self._final_result_sample_limit()
        rng = random.Random(0)
        percentile_times = []
        total = 0
        elapsed_sum = 0
        min_elapsed = None
        max_elapsed = 0
        latency_sum = 0
        latency_count = 0
        connect_sum = 0
        connect_count = 0
        bytes_received = 0
        bytes_sent = 0
        error_count = 0
        response_codes = {}
        min_ts = None
        max_ts = None
        ts_count = 0

        if header_line is None:
            header_line = f.readline().strip()
        fieldnames = next(csv.reader([header_line]), [])
        reader = csv.DictReader(f, fieldnames=fieldnames)

        for row in reader:
            try:
                elapsed = self._csv_int(row, "elapsed")
                success = self._csv_bool(row, "success", True)
                ts = self._csv_int(row, "timeStamp")
                by = self._csv_int(row, "bytes")
                sby = self._csv_int(row, "sentBytes")
                lt = self._csv_int(row, "Latency")
                ct = self._csv_int(row, "Connect")

                total += 1
                elapsed_sum += elapsed
                min_elapsed = elapsed if min_elapsed is None else min(min_elapsed, elapsed)
                max_elapsed = max(max_elapsed, elapsed)
                self._record_percentile_sample(percentile_times, elapsed, total, rng, sample_limit)

                if ts:
                    min_ts = ts if min_ts is None else min(min_ts, ts)
                    max_ts = ts if max_ts is None else max(max_ts, ts)
                    ts_count += 1
                bytes_received += by
                bytes_sent += sby
                latency_sum += lt
                latency_count += 1
                connect_sum += ct
                connect_count += 1

                if not success:
                    error_count += 1

                rc = self._csv_get(row, "responseCode", "")
                response_codes[rc] = response_codes.get(rc, 0) + 1
            except Exception:
                pass

        return self._apply_stream_summary(
            summary,
            total=total,
            error_count=error_count,
            elapsed_sum=elapsed_sum,
            min_elapsed=min_elapsed,
            max_elapsed=max_elapsed,
            percentile_times=percentile_times,
            min_ts=min_ts,
            max_ts=max_ts,
            ts_count=ts_count,
            bytes_received=bytes_received,
            bytes_sent=bytes_sent,
            latency_sum=latency_sum,
            latency_count=latency_count,
            connect_sum=connect_sum,
            connect_count=connect_count,
            response_codes=response_codes,
            sample_limit=sample_limit,
        )

    def _generate_report(self, jtl_path: str, report_path: str, jmeter_args: dict = None):
        """使用 JMeter 生成 HTML Dashboard 报告"""
        try:
            cmd = [self.jmeter_bin]
            for key, value in (jmeter_args or {}).items():
                if str(key).startswith("jmeter.reportgenerator."):
                    cmd.append(f"-J{key}={value}")
            cmd.extend([
                "-g", jtl_path,
                "-o", report_path,
            ])

            env = os.environ.copy()
            heap_mb = self._parse_heap_mb((jmeter_args or {}).get("jvm_heap_mb"))
            if heap_mb:
                env["HEAP"] = f"-Xms{heap_mb}m -Xmx{heap_mb}m"

            os.makedirs(os.path.dirname(report_path), exist_ok=True)
            report_log_path = os.path.join(os.path.dirname(report_path), "jmeter-report.log")
            with open(report_log_path, "w", encoding="utf-8") as report_log:
                result = subprocess.run(
                    cmd,
                    timeout=60,
                    stdout=report_log,
                    stderr=report_log,
                    env=env,
                )
            if result.returncode != 0:
                print(f"JMeter report generation failed, see log: {report_log_path}")
        except Exception as e:
            print(f"JMeter report generation error: {e}")

    @property
    def is_running(self) -> bool:
        """检查 JMeter 进程是否正在运行"""
        return self._process is not None and self._process.poll() is None

import subprocess
import os
import signal
import time
import csv
import xml.etree.ElementTree as ET
from typing import Optional, Callable


class JMeterRunner:
    def __init__(self, jmeter_home: str):
        self.jmeter_home = jmeter_home
        self.jmeter_bin = os.path.join(jmeter_home, "bin", "jmeter")
        self._process: Optional[subprocess.Popen] = None
        self._result_dir: Optional[str] = None

    def inject_csv_config(
        self,
        script_path: str,
        csv_file: str,
        variable_names: str = None,
        delimiter: str = ",",
        recycle: bool = True,
        stop_on_eof: bool = False,
    ) -> str:
        try:
            tree = ET.parse(script_path)
            root = tree.getroot()

            # Find the root hashTree (child of jmeterTestPlan)
            root_hash_tree = root.find("hashTree")
            if root_hash_tree is None:
                return script_path

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
        self._result_dir = result_dir
        os.makedirs(result_dir, exist_ok=True)

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

        cmd = [
            self.jmeter_bin,
            "-n",
            "-t", script_path,
            "-l", jtl_path,
            "-j", os.path.join(result_dir, "jmeter.log"),
            "-Jjmeter.save.saveservice.output_format=xml",
            "-Jjmeter.save.saveservice.print_field_names=true",
        ]

        if distributed:
            if remote_hosts:
                cmd.extend(["-R", remote_hosts])
            else:
                cmd.append("-r")

        for key, value in jmeter_args.items():
            cmd.extend([f"-J{key}={value}"])

        try:
            self._process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            start_time = time.time()
            last_progress_time = start_time

            while self._process.poll() is None:
                elapsed = time.time() - start_time
                if elapsed > timeout:
                    self.stop()
                    return {"status": "failed", "error": "timeout"}

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
        }
        if not os.path.exists(jtl_path):
            return result

        try:
            with open(jtl_path, "r") as f:
                lines = f.readlines()
                result["total_samples"] = len(lines) - 1 if lines else 0
                for line in lines[1:]:
                    parts = line.strip().split(",")
                    if len(parts) >= 4:
                        elapsed = int(parts[1])
                        success = parts[3].strip() == "true"
                        result["elapsed_times"].append(elapsed)
                        if not success:
                            result["error_count"] += 1
        except Exception:
            pass

        return result

    def _parse_final_result(self, jtl_path: str) -> dict:
        summary = {
            "total_samples": 0,
            "error_count": 0,
            "error_rate": 0.0,
            "avg_response_time": 0.0,
            "min_response_time": 0,
            "max_response_time": 0,
            "p50": 0,
            "p90": 0,
            "p95": 0,
            "p99": 0,
            "throughput": 0.0,
        }

        if not os.path.exists(jtl_path):
            return summary

        try:
            elapsed_times = []
            with open(jtl_path, "r") as f:
                lines = f.readlines()
                total = len(lines) - 1 if lines else 0
                error_count = 0

                for line in lines[1:]:
                    parts = line.strip().split(",")
                    if len(parts) >= 4:
                        elapsed = int(parts[1])
                        success = parts[3].strip() == "true"
                        elapsed_times.append(elapsed)
                        if not success:
                            error_count += 1

            if elapsed_times:
                elapsed_times.sort()
                summary["total_samples"] = total
                summary["error_count"] = error_count
                summary["error_rate"] = round(error_count / total * 100, 2) if total > 0 else 0
                summary["avg_response_time"] = round(sum(elapsed_times) / len(elapsed_times), 2)
                summary["min_response_time"] = min(elapsed_times)
                summary["max_response_time"] = max(elapsed_times)
                summary["p50"] = self._percentile(elapsed_times, 50)
                summary["p90"] = self._percentile(elapsed_times, 90)
                summary["p95"] = self._percentile(elapsed_times, 95)
                summary["p99"] = self._percentile(elapsed_times, 99)

        except Exception:
            pass

        return summary

    def _percentile(self, data: list, p: int) -> int:
        if not data:
            return 0
        k = (len(data) - 1) * (p / 100)
        f = int(k)
        c = f + 1
        if c >= len(data):
            return data[f]
        return int(data[f] + (k - f) * (data[c] - data[f]))

    def _generate_report(self, jtl_path: str, report_path: str):
        try:
            subprocess.run(
                [
                    self.jmeter_bin,
                    "-g", jtl_path,
                    "-o", report_path,
                ],
                timeout=60,
                capture_output=True,
            )
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

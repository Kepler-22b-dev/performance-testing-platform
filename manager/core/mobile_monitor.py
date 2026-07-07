"""移动端性能监控核心模块。

支持双引擎：
- Android: SOLOX 服务
- iOS: pymobiledevice3 CLI
"""

import subprocess
import json
import re
import os
from typing import Optional, Dict, Any, List

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from common.config import SOLOX_BASE_URL


class AndroidMonitor:
    """Android设备监控（基于SOLOX）"""

    def __init__(self, base_url: str = None):
        self.base_url = base_url or SOLOX_BASE_URL

    async def get_status(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=3.0) as client:
                resp = await client.get(f"{self.base_url}/")
                return resp.status_code == 200
        except Exception:
            return False

    async def collect(self, pkgname: str, deviceid: Optional[str] = None,
                      target: str = "cpu") -> Dict[str, Any]:
        params = {"platform": "Android", "pkgname": pkgname, "target": target}
        if deviceid:
            params["deviceid"] = deviceid
        try:
            import httpx
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self.base_url}/apm/collect", params=params)
                if resp.status_code == 200:
                    return resp.json()
                return {"error": f"SOLOX returned {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    async def collect_all(self, pkgname: str, deviceid: Optional[str] = None) -> Dict[str, Any]:
        targets = ["cpu", "memory", "network", "fps", "battery", "gpu"]
        results = {}
        for target in targets:
            results[target] = await self.collect(pkgname, deviceid, target)
        return results


class IOSMonitor:
    """iOS设备监控（基于pymobiledevice3）"""

    @staticmethod
    def _run_cmd(cmd: List[str], timeout: int = 15) -> Optional[str]:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            output = result.stdout + result.stderr
            return output.strip() if output.strip() else None
        except (subprocess.TimeoutExpired, FileNotFoundError):
            return None

    @staticmethod
    async def _run_cmd_async(cmd: List[str], timeout: int = 15) -> Optional[str]:
        import asyncio
        return await asyncio.get_event_loop().run_in_executor(
            None, IOSMonitor._run_cmd, cmd, timeout
        )

    async def get_status(self) -> bool:
        try:
            output = await self._run_cmd_async(["pymobiledevice3", "usbmux", "list"], timeout=5)
            if output:
                try:
                    devices = json.loads(output)
                    if isinstance(devices, list) and len(devices) > 0:
                        return True
                except json.JSONDecodeError:
                    if "Identifier" in output:
                        return True
            return False
        except Exception:
            return False

    async def get_devices(self) -> List[Dict[str, str]]:
        output = await self._run_cmd_async(["pymobiledevice3", "usbmux", "list"])
        if not output:
            return []
        devices = []
        try:
            device_list = json.loads(output)
            if isinstance(device_list, list):
                seen = set()
                for device in device_list:
                    udid = device.get("UniqueDeviceID", "")
                    if udid in seen:
                        continue
                    seen.add(udid)
                    devices.append({
                        "udid": udid,
                        "name": device.get("DeviceName", "Unknown"),
                        "product_type": device.get("ProductType", ""),
                        "product_version": device.get("ProductVersion", ""),
                        "connection_type": device.get("ConnectionType", "USB")
                    })
        except json.JSONDecodeError:
            pass
        return devices

    async def get_installed_apps(self) -> List[Dict[str, str]]:
        output = await self._run_cmd_async(["pymobiledevice3", "apps", "list"], timeout=30)
        if not output:
            return []
        apps = []
        try:
            apps_dict = json.loads(output)
            if isinstance(apps_dict, dict):
                for bundle_id, info in apps_dict.items():
                    if isinstance(info, dict) and info.get("ApplicationType") == "User":
                        apps.append({
                            "bundle_id": bundle_id,
                            "name": info.get("CFBundleDisplayName", bundle_id),
                            "executable": info.get("CFBundleExecutable", ""),
                            "version": info.get("CFBundleShortVersionString", ""),
                        })
        except json.JSONDecodeError:
            pass
        apps.sort(key=lambda x: x.get("name", ""))
        return apps

    async def find_process_pid(self, app_name: str) -> Optional[str]:
        output = await self._run_cmd_async(
            ["pymobiledevice3", "processes", "pgrep", app_name, "--userspace"],
            timeout=5
        )
        if output:
            matches = re.findall(r'INFO\s+(\d+)\s+(\S+)', output)
            if matches:
                return matches[0][0]

        ps_output = await self._run_cmd_async(
            ["pymobiledevice3", "processes", "ps", "--userspace"],
            timeout=15
        )
        if ps_output:
            try:
                processes = json.loads(ps_output)
                if isinstance(processes, list):
                    for proc in processes:
                        exec_path = proc.get("execName", proc.get("comm", "")).lower()
                        comm = proc.get("comm", "").lower()
                        name = proc.get("name", "").lower()
                        search = app_name.lower()
                        if search in exec_path or search in comm or search in name:
                            return str(proc.get("pid", ""))
                        if search.endswith("." + comm):
                            return str(proc.get("pid", ""))
            except json.JSONDecodeError:
                pass

        return None

    async def collect_all(self, bundle_id: str, deviceid: Optional[str] = None) -> Dict[str, Any]:
        """采集所有性能指标（使用sysmon process single --userspace）"""
        pid = await self.find_process_pid(bundle_id)
        if not pid:
            return {"cpu": {"cpu": 0}, "memory": {"total": 0}, "pid": None, "error": "process not found"}

        output = await self._run_cmd_async(
            ["pymobiledevice3", "developer", "dvt", "sysmon", "process", "single", "--userspace"],
            timeout=20
        )
        if not output:
            return {"cpu": {"cpu": 0}, "memory": {"total": 0}, "pid": pid, "error": "sysmon failed"}

        try:
            processes = json.loads(output)
            if isinstance(processes, list):
                for proc in processes:
                    if str(proc.get("pid", "")) == pid:
                        return {
                            "cpu": {"cpu": round(proc.get("cpuUsage", 0), 2)},
                            "memory": {"total": round(proc.get("physFootprint", 0) / (1024 * 1024), 2)},
                            "pid": pid,
                            "name": proc.get("comm", bundle_id),
                        }
        except json.JSONDecodeError:
            pass

        return {"cpu": {"cpu": 0}, "memory": {"total": 0}, "pid": pid, "error": "process not found in sysmon"}


class MobileMonitor:
    """移动端性能监控器（统一接口）"""

    def __init__(self):
        self.android = AndroidMonitor()
        self.ios = IOSMonitor()

    async def get_status(self, platform: str) -> bool:
        if platform.lower() == "android":
            return await self.android.get_status()
        elif platform.lower() == "ios":
            return await self.ios.get_status()
        return False

    async def detect_platform(self) -> Optional[str]:
        ios_connected = await self.ios.get_status()
        if ios_connected:
            return "ios"
        android_connected = await self.android.get_status()
        if android_connected:
            return "android"
        return None

    async def get_devices(self, platform: str) -> List[Dict[str, Any]]:
        if platform.lower() == "ios":
            return await self.ios.get_devices()
        return []

    async def get_installed_apps(self, platform: str) -> List[Dict[str, str]]:
        if platform.lower() == "ios":
            return await self.ios.get_installed_apps()
        return []

    async def collect_all(self, platform: str, pkgname: str,
                          deviceid: Optional[str] = None) -> Dict[str, Any]:
        if platform.lower() == "android":
            return await self.android.collect_all(pkgname, deviceid)
        elif platform.lower() == "ios":
            return await self.ios.collect_all(pkgname, deviceid)
        return {"error": f"Unsupported platform: {platform}"}


mobile_monitor = MobileMonitor()

"""移动端性能监控 API 模块。

提供与移动设备交互的接口，用于采集 Android/iOS 设备的
CPU、内存、FPS、网络、电池等性能数据。

支持双引擎：
- Android: SOLOX 服务
- iOS: pymobiledevice3 CLI
"""

import sys
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.mobile_monitor import mobile_monitor

router = APIRouter(prefix="/api/mobile", tags=["mobile"])


class CollectRequest(BaseModel):
    platform: str = "Android"
    deviceid: Optional[str] = None
    pkgname: str
    target: str = "cpu"


class DeviceListRequest(BaseModel):
    platform: str = "Android"


@router.get("/status")
async def check_status():
    """检查移动端监控服务状态"""
    android_status = await mobile_monitor.get_status("android")
    ios_status = await mobile_monitor.get_status("ios")
    
    return {
        "android": {"available": android_status, "engine": "SOLOX"},
        "ios": {"available": ios_status, "engine": "pymobiledevice3"},
    }


@router.get("/detect")
async def detect_device():
    """自动检测连接的设备平台和信息"""
    platform = await mobile_monitor.detect_platform()
    
    result = {
        "detected": platform is not None,
        "platform": platform,
        "devices": []
    }
    
    if platform:
        devices = await mobile_monitor.get_devices(platform)
        result["devices"] = devices
    
    return result


@router.get("/status/{platform}")
async def check_platform_status(platform: str):
    """检查指定平台的监控服务状态"""
    if platform.lower() not in ["android", "ios"]:
        raise HTTPException(status_code=400, detail="Platform must be 'android' or 'ios'")
    
    status = await mobile_monitor.get_status(platform)
    engine = "SOLOX" if platform.lower() == "android" else "pymobiledevice3"
    
    return {
        "platform": platform,
        "available": status,
        "engine": engine,
    }


@router.post("/devices")
async def get_devices(req: DeviceListRequest):
    """获取已连接的移动设备列表"""
    devices = await mobile_monitor.get_devices(req.platform)
    return {"devices": devices}


@router.get("/apps/{platform}")
async def get_installed_apps(platform: str, deviceid: Optional[str] = None):
    """获取设备上已安装的应用列表"""
    if platform.lower() not in ["android", "ios"]:
        raise HTTPException(status_code=400, detail="Platform must be 'android' or 'ios'")
    
    apps = await mobile_monitor.get_installed_apps(platform)
    return {"apps": apps}


@router.post("/collect")
async def collect_performance(req: CollectRequest):
    """采集指定设备的性能数据"""
    result = await mobile_monitor.collect_all(
        platform=req.platform,
        pkgname=req.pkgname,
        deviceid=req.deviceid
    )
    return result


@router.post("/collect/all")
async def collect_all_performance(req: CollectRequest):
    """一次性采集所有性能指标"""
    result = await mobile_monitor.collect_all(
        platform=req.platform,
        pkgname=req.pkgname,
        deviceid=req.deviceid
    )
    return result


@router.get("/config")
def get_config():
    """获取当前移动端监控配置"""
    return {
        "android": {
            "engine": "SOLOX",
            "base_url": "SOLOX_BASE_URL",
        },
        "ios": {
            "engine": "pymobiledevice3",
            "method": "CLI",
            "note": "iOS 17+ requires tunnel setup",
        }
    }

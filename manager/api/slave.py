import sys
import os
from fastapi import APIRouter

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from manager.core.slave_manager import get_slave_status, start_slave, stop_slave

router = APIRouter(prefix="/api/slave", tags=["slave"])


@router.get("/status")
def slave_status():
    return get_slave_status()


@router.post("/start")
def slave_start(port: int = None):
    return start_slave(port)


@router.post("/stop")
def slave_stop():
    return stop_slave()

"""压测场景模板管理 API 模块。"""

import sys
import os
import time
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from common.database import get_db
from manager.core.db import db_get_all_templates, db_create_template, db_delete_template

router = APIRouter(prefix="/api/templates", tags=["templates"])

BUILTIN_TEMPLATES = [
    {"template_id": "builtin-smoke", "name": "冒烟测试", "description": "低并发快速验证接口可用性，确认基本功能正常", "category": "基础", "config": {"threads": 1, "ramp_time": 1, "duration": 30, "timeout": 120}, "builtin": True},
    {"template_id": "builtin-load", "name": "负载测试", "description": "中等并发持续压测，评估系统在正常负载下的表现", "category": "基础", "config": {"threads": 50, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-stress", "name": "压力测试", "description": "高并发持续压测，找到系统性能瓶颈和极限", "category": "基础", "config": {"threads": 200, "ramp_time": 30, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-step", "name": "阶梯加压", "description": "逐步增加并发数，观察系统性能随负载变化的趋势", "category": "进阶", "config": {"threads": 100, "ramp_time": 60, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-peak", "name": "峰值测试", "description": "短时间大量并发冲击，验证系统抗突发流量能力", "category": "进阶", "config": {"threads": 500, "ramp_time": 5, "duration": 120, "timeout": 300}, "builtin": True},
    {"template_id": "builtin-endurance", "name": "稳定性测试", "description": "长时间中等负载运行，检测内存泄漏和性能衰退", "category": "进阶", "config": {"threads": 50, "ramp_time": 10, "duration": 3600, "timeout": 4200}, "builtin": True},
    {"template_id": "builtin-spike", "name": "尖刺测试", "description": "瞬间极高并发后快速释放，测试系统恢复能力", "category": "进阶", "config": {"threads": 1000, "ramp_time": 1, "duration": 30, "timeout": 120}, "builtin": True},
    {"template_id": "builtin-api", "name": "API 接口测试", "description": "单接口循环调用，适合功能验证和基础性能评估", "category": "场景", "config": {"threads": 10, "ramp_time": 1, "duration": 60, "timeout": 120}, "builtin": True},

    {"template_id": "builtin-flash-sale", "name": "秒杀/抢购", "description": "模拟电商秒杀场景：瞬间涌入大量用户抢购同一商品，测试库存扣减和订单创建的并发能力", "category": "电商业态", "config": {"threads": 2000, "ramp_time": 3, "duration": 60, "timeout": 300}, "builtin": True},
    {"template_id": "builtin-order-flow", "name": "下单全流程", "description": "模拟用户完整购物链路：浏览→加购→下单→支付，按真实业务比例混合压测", "category": "电商业态", "config": {"threads": 200, "ramp_time": 30, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-product-browse", "name": "商品浏览", "description": "模拟用户高频浏览商品列表和详情页，适合搜索/推荐接口的读性能评估", "category": "电商业态", "config": {"threads": 300, "ramp_time": 15, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-coupon-grab", "name": "优惠券抢领", "description": "模拟大促期间用户集中抢领优惠券，验证券池扣减和防超卖机制", "category": "电商业态", "config": {"threads": 1500, "ramp_time": 2, "duration": 30, "timeout": 180}, "builtin": True},
    {"template_id": "builtin-cart-concurrent", "name": "购物车并发", "description": "模拟多用户同时操作购物车：加购、修改数量、删除、凑单，测试库存实时校验", "category": "电商业态", "config": {"threads": 500, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},

    {"template_id": "builtin-feed-load", "name": "信息流加载", "description": "模拟社交APP首页信息流下拉刷新，测试推荐接口的读性能和缓存命中率", "category": "社交内容", "config": {"threads": 500, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-post-publish", "name": "发帖/发布", "description": "模拟用户集中发布内容（图文/短视频），测试写入密集型接口的吞吐", "category": "社交内容", "config": {"threads": 200, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-live-room", "name": "直播间涌入", "description": "模拟直播间开播瞬间大量用户同时进入，测试弹幕/礼物/在线人数接口", "category": "社交内容", "config": {"threads": 3000, "ramp_time": 5, "duration": 120, "timeout": 300}, "builtin": True},
    {"template_id": "builtin-comment-hot", "name": "热评并发", "description": "模拟热门内容下大量用户同时评论/点赞，测试写扩散和缓存一致性", "category": "社交内容", "config": {"threads": 800, "ramp_time": 5, "duration": 180, "timeout": 360}, "builtin": True},

    {"template_id": "builtin-login-burst", "name": "登录风暴", "description": "模拟早高峰或活动开始时大量用户同时登录，测试鉴权服务和Token签发能力", "category": "API网关", "config": {"threads": 1000, "ramp_time": 10, "duration": 120, "timeout": 300}, "builtin": True},
    {"template_id": "builtin-gateway-passthrough", "name": "网关透传", "description": "模拟API网关高并发透传请求，测试网关路由、限流、熔断等中间件性能", "category": "API网关", "config": {"threads": 500, "ramp_time": 15, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-token-refresh", "name": "Token刷新", "description": "模拟大量用户Token过期后集中刷新，测试认证服务的并发处理能力", "category": "API网关", "config": {"threads": 400, "ramp_time": 5, "duration": 120, "timeout": 300}, "builtin": True},

    {"template_id": "builtin-file-upload", "name": "文件上传", "description": "模拟多用户同时上传文件（图片/文档），测试存储服务的写入吞吐和带宽", "category": "场景", "config": {"threads": 100, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-file-download", "name": "文件下载", "description": "模拟多用户并发下载文件，测试CDN/对象存储的读取带宽和连接数", "category": "场景", "config": {"threads": 300, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-search-mix", "name": "搜索混合场景", "description": "模拟搜索引擎的读写混合：80%搜索查询 + 20%索引更新，评估搜索集群性能", "category": "场景", "config": {"threads": 200, "ramp_time": 15, "duration": 600, "timeout": 900}, "builtin": True},
    {"template_id": "builtin-db-read-heavy", "name": "数据库读密集", "description": "模拟高并发数据库查询场景，适合评估读写分离和缓存策略效果", "category": "场景", "config": {"threads": 500, "ramp_time": 15, "duration": 300, "timeout": 600}, "builtin": True},
    {"template_id": "builtin-db-write-heavy", "name": "数据库写密集", "description": "模拟高并发写入场景（日志/流水/埋点），评估数据库写入性能和锁竞争", "category": "场景", "config": {"threads": 200, "ramp_time": 10, "duration": 300, "timeout": 600}, "builtin": True},

    {"template_id": "builtin-weekly-peak", "name": "周末高峰", "description": "模拟周末晚间用户活跃高峰：1小时逐步加压到峰值，保持2小时，缓慢下降", "category": "电商业态", "config": {"threads": 400, "ramp_time": 60, "duration": 1800, "timeout": 2400}, "builtin": True},
    {"template_id": "builtin-double11", "name": "双11大促", "description": "模拟双11零点瞬间爆发：预热3分钟→零点尖刺→持续高水位2小时→缓慢回落", "category": "电商业态", "config": {"threads": 5000, "ramp_time": 10, "duration": 7200, "timeout": 7800}, "builtin": True},
    {"template_id": "builtin-api-breakpoint", "name": "接口拐点探测", "description": "从10并发开始每分钟递增10，持续30分钟，精确找到系统性能拐点", "category": "进阶", "config": {"threads": 300, "ramp_time": 1800, "duration": 1800, "timeout": 2400}, "builtin": True},
]


class TemplateCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "自定义"
    config: dict = {}


@router.get("/")
async def list_templates(db: AsyncSession = Depends(get_db)):
    custom = await db_get_all_templates(db)
    templates = BUILTIN_TEMPLATES + custom
    return {"total": len(templates), "templates": templates}


@router.get("/{template_id}")
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    templates = BUILTIN_TEMPLATES + await db_get_all_templates(db)
    for t in templates:
        if t["template_id"] == template_id:
            return t
    raise HTTPException(status_code=404, detail="模板不存在")


@router.post("/")
async def create_template(req: TemplateCreateRequest, db: AsyncSession = Depends(get_db)):
    template_id = f"tpl-{int(time.time()*1000)}"
    template = {
        "template_id": template_id,
        "name": req.name,
        "description": req.description,
        "category": req.category,
        "config": req.config,
        "builtin": False,
        "created_at": time.time(),
    }
    await db_create_template(db, template)
    return {"status": "created", "template": template}


@router.delete("/{template_id}")
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)):
    for t in BUILTIN_TEMPLATES:
        if t["template_id"] == template_id:
            raise HTTPException(status_code=400, detail="不能删除内置模板")

    deleted = await db_delete_template(db, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="模板不存在")
    return {"status": "deleted", "template_id": template_id}

"""Mock API Server for JMeter Testing.

Usage:
  python3 mock-server.py

The legacy /api/users endpoints are kept for existing scripts. The
/api/ecommerce endpoints provide a complete e-commerce flow for pressure tests:
register, login, search products, add cart item, create order, view order detail,
and cancel order. Every e-commerce request is appended to a JSONL trace file.
"""

from fastapi import FastAPI, Header, Request
from fastapi.responses import JSONResponse
import uvicorn
import time
import os
import json
import random
import threading
from typing import Optional

app = FastAPI(title="Mock API Server")

MOCK_USERS = [
    {"id": 1, "username": "user001", "email": "user001@example.com", "role": "admin"},
    {"id": 2, "username": "user002", "email": "user002@example.com", "role": "editor"},
    {"id": 3, "username": "user003", "email": "user003@example.com", "role": "viewer"},
    {"id": 4, "username": "user004", "email": "user004@example.com", "role": "admin"},
    {"id": 5, "username": "user005", "email": "user005@example.com", "role": "viewer"},
]

TRACE_FILE = os.getenv(
    "MOCK_ECOMMERCE_TRACE",
    os.path.join(os.path.dirname(__file__), "mock-data", "ecommerce-trace.jsonl"),
)
_lock = threading.Lock()
_users = {u["username"]: dict(u, password="pass1234") for u in MOCK_USERS}
_tokens = {}
_cart_items = {}
_orders = {}
_request_count = 0

_products = [
    {"product_id": "SKU-1001", "name": "Mock Phone 15", "category": "phone", "price": 5299, "stock": 5000},
    {"product_id": "SKU-1002", "name": "Mock Laptop Pro", "category": "computer", "price": 8999, "stock": 3000},
    {"product_id": "SKU-1003", "name": "Mock Wireless Earbuds", "category": "audio", "price": 699, "stock": 8000},
    {"product_id": "SKU-1004", "name": "Mock Coffee Machine", "category": "home", "price": 1299, "stock": 2000},
    {"product_id": "SKU-1005", "name": "Mock Running Shoes", "category": "sport", "price": 499, "stock": 6000},
]


def _trace(event: str, status: int = 200, **fields):
    global _request_count
    with _lock:
        _request_count += 1
        seq = _request_count
    record = {
        "seq": seq,
        "ts": round(time.time(), 3),
        "event": event,
        "status": status,
        **fields,
    }
    try:
        os.makedirs(os.path.dirname(TRACE_FILE), exist_ok=True)
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return record


def _sleep_like_service():
    time.sleep(random.uniform(0.004, 0.025))


def _token_username(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    token = authorization.replace("Bearer", "", 1).strip()
    return _tokens.get(token)


def _require_user(authorization: Optional[str]):
    username = _token_username(authorization)
    if not username:
        return None, JSONResponse(status_code=401, content={"error": "unauthorized"})
    return username, None

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


@app.get("/api/ecommerce/health")
async def ecommerce_health():
    return {
        "status": "ok",
        "service": "mock-ecommerce",
        "timestamp": time.time(),
        "trace_file": TRACE_FILE,
    }

@app.get("/api/users")
async def list_users():
    return {"users": MOCK_USERS, "total": len(MOCK_USERS)}

@app.get("/api/users/{user_id}")
async def get_user(user_id: int):
    for user in MOCK_USERS:
        if user["id"] == user_id:
            return user
    return JSONResponse(status_code=404, content={"error": "User not found"})

@app.post("/api/users")
async def create_user(request: Request):
    body = await request.json()
    new_user = {
        "id": len(MOCK_USERS) + 1,
        "username": body.get("username", "unknown"),
        "password": body.get("password", ""),
        "email": body.get("email", ""),
        "role": body.get("role", "viewer"),
        "created_at": time.time(),
    }
    return {"message": "User created", "user": new_user}

@app.post("/api/login")
async def login(request: Request):
    body = await request.json()
    username = body.get("username", "")
    password = body.get("password", "")
    if username and password:
        return {"token": f"mock-jwt-token-{username}", "expires_in": 3600}
    return JSONResponse(status_code=401, content={"error": "Invalid credentials"})


@app.post("/api/ecommerce/users/register")
async def ecommerce_register(request: Request):
    _sleep_like_service()
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    email = str(body.get("email", "")).strip()
    if not username or not password:
        _trace("register", 400, username=username)
        return JSONResponse(status_code=400, content={"error": "username and password required"})

    with _lock:
        created = username not in _users
        if created:
            _users[username] = {
                "id": len(_users) + 1,
                "username": username,
                "password": password,
                "email": email,
                "created_at": time.time(),
            }
    _trace("register", 200, username=username, created=created)
    return {
        "ok": True,
        "created": created,
        "user_id": _users[username]["id"],
        "username": username,
    }


@app.post("/api/ecommerce/auth/login")
async def ecommerce_login(request: Request):
    _sleep_like_service()
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", "")).strip()
    user = _users.get(username)
    if not user or not password:
        _trace("login", 401, username=username)
        return JSONResponse(status_code=401, content={"error": "invalid credentials"})

    token = f"token-{username}-{int(time.time() * 1000)}"
    with _lock:
        _tokens[token] = username
    _trace("login", 200, username=username)
    return {"ok": True, "token": token, "user_id": user["id"], "expires_in": 3600}


@app.get("/api/ecommerce/products/search")
async def ecommerce_search(q: str = "mock", page: int = 1, size: int = 10):
    _sleep_like_service()
    keyword = str(q or "").lower()
    matched = [
        p for p in _products
        if keyword in p["name"].lower()
        or keyword in p["category"].lower()
        or keyword in p["product_id"].lower()
        or keyword in {"mock", "all", ""}
    ]
    if not matched:
        matched = _products[:]
    start = max(0, (page - 1) * size)
    page_items = matched[start:start + size]
    _trace("search_products", 200, keyword=q, count=len(page_items))
    return {"ok": True, "total": len(matched), "products": page_items}


@app.post("/api/ecommerce/cart/items")
async def ecommerce_add_cart(request: Request, authorization: Optional[str] = Header(default=None)):
    _sleep_like_service()
    username, error = _require_user(authorization)
    if error:
        _trace("add_cart", 401)
        return error

    body = await request.json()
    product_id = str(body.get("product_id", "")).strip()
    quantity = max(1, int(body.get("quantity", 1) or 1))
    product = next((p for p in _products if p["product_id"] == product_id), None)
    if not product:
        _trace("add_cart", 404, username=username, product_id=product_id)
        return JSONResponse(status_code=404, content={"error": "product not found"})

    cart_item_id = f"CART-{username}-{int(time.time() * 1000)}-{random.randint(1000, 9999)}"
    cart_item = {
        "cart_item_id": cart_item_id,
        "username": username,
        "product_id": product_id,
        "quantity": quantity,
        "amount": product["price"] * quantity,
        "created_at": time.time(),
    }
    with _lock:
        _cart_items[cart_item_id] = cart_item
    _trace("add_cart", 200, username=username, product_id=product_id, cart_item_id=cart_item_id)
    return {"ok": True, **cart_item}


@app.post("/api/ecommerce/orders")
async def ecommerce_create_order(request: Request, authorization: Optional[str] = Header(default=None)):
    _sleep_like_service()
    username, error = _require_user(authorization)
    if error:
        _trace("create_order", 401)
        return error

    body = await request.json()
    cart_item_id = str(body.get("cart_item_id", "")).strip()
    cart_item = _cart_items.get(cart_item_id)
    if not cart_item or cart_item["username"] != username:
        _trace("create_order", 404, username=username, cart_item_id=cart_item_id)
        return JSONResponse(status_code=404, content={"error": "cart item not found"})

    order_id = f"ORD-{int(time.time() * 1000)}-{random.randint(10000, 99999)}"
    order = {
        "order_id": order_id,
        "username": username,
        "items": [cart_item],
        "amount": cart_item["amount"],
        "status": "CREATED",
        "created_at": time.time(),
    }
    with _lock:
        _orders[order_id] = order
    _trace("create_order", 201, username=username, order_id=order_id, amount=order["amount"])
    return JSONResponse(status_code=201, content={"ok": True, **order})


@app.get("/api/ecommerce/orders/{order_id}")
async def ecommerce_order_detail(order_id: str, authorization: Optional[str] = Header(default=None)):
    _sleep_like_service()
    username, error = _require_user(authorization)
    if error:
        _trace("order_detail", 401, order_id=order_id)
        return error

    order = _orders.get(order_id)
    if not order or order["username"] != username:
        _trace("order_detail", 404, username=username, order_id=order_id)
        return JSONResponse(status_code=404, content={"error": "order not found"})
    _trace("order_detail", 200, username=username, order_id=order_id, order_status=order["status"])
    return {"ok": True, "order": order}


@app.post("/api/ecommerce/orders/{order_id}/cancel")
async def ecommerce_cancel_order(order_id: str, authorization: Optional[str] = Header(default=None)):
    _sleep_like_service()
    username, error = _require_user(authorization)
    if error:
        _trace("cancel_order", 401, order_id=order_id)
        return error

    order = _orders.get(order_id)
    if not order or order["username"] != username:
        _trace("cancel_order", 404, username=username, order_id=order_id)
        return JSONResponse(status_code=404, content={"error": "order not found"})
    with _lock:
        order["status"] = "CANCELLED"
        order["cancelled_at"] = time.time()
    _trace("cancel_order", 200, username=username, order_id=order_id)
    return {"ok": True, "order_id": order_id, "status": "CANCELLED"}


@app.get("/api/ecommerce/trace/summary")
async def ecommerce_trace_summary():
    return {
        "users": len(_users),
        "tokens": len(_tokens),
        "cart_items": len(_cart_items),
        "orders": len(_orders),
        "requests": _request_count,
        "trace_file": TRACE_FILE,
    }

if __name__ == "__main__":
    host = os.getenv("MOCK_HOST", "0.0.0.0")
    port = int(os.getenv("MOCK_PORT", "9000"))
    print(f"Mock API Server starting on http://localhost:{port}")
    print("Endpoints:")
    print(f"  GET  http://localhost:{port}/api/health")
    print(f"  GET  http://localhost:{port}/api/users")
    print(f"  GET  http://localhost:{port}/api/users/{{id}}")
    print(f"  POST http://localhost:{port}/api/users")
    print(f"  POST http://localhost:{port}/api/login")
    print(f"  POST http://localhost:{port}/api/ecommerce/users/register")
    print(f"  POST http://localhost:{port}/api/ecommerce/auth/login")
    print(f"  GET  http://localhost:{port}/api/ecommerce/products/search?q=mock")
    print(f"  POST http://localhost:{port}/api/ecommerce/cart/items")
    print(f"  POST http://localhost:{port}/api/ecommerce/orders")
    print(f"  GET  http://localhost:{port}/api/ecommerce/orders/{{order_id}}")
    print(f"  POST http://localhost:{port}/api/ecommerce/orders/{{order_id}}/cancel")
    print(f"Trace: {TRACE_FILE}")
    uvicorn.run(app, host=host, port=port)

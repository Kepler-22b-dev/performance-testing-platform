"""
Mock API Server for JMeter Testing
Usage: python3 mock-server.py
Endpoints:
  GET  /api/users       - List users (returns CSV-formatted response)
  GET  /api/users/{id}  - Get user by ID
  POST /api/users       - Create user (echo back request body)
  GET  /api/health      - Health check
"""
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
import uvicorn
import time

app = FastAPI(title="Mock API Server")

MOCK_USERS = [
    {"id": 1, "username": "user001", "email": "user001@example.com", "role": "admin"},
    {"id": 2, "username": "user002", "email": "user002@example.com", "role": "editor"},
    {"id": 3, "username": "user003", "email": "user003@example.com", "role": "viewer"},
    {"id": 4, "username": "user004", "email": "user004@example.com", "role": "admin"},
    {"id": 5, "username": "user005", "email": "user005@example.com", "role": "viewer"},
]

@app.get("/api/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}

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

if __name__ == "__main__":
    print("Mock API Server starting on http://localhost:9000")
    print("Endpoints:")
    print("  GET  http://localhost:9000/api/health")
    print("  GET  http://localhost:9000/api/users")
    print("  GET  http://localhost:9000/api/users/{id}")
    print("  POST http://localhost:9000/api/users")
    print("  POST http://localhost:9000/api/login")
    uvicorn.run(app, host="0.0.0.0", port=9000)

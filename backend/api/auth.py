import secrets
import time

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from backend.api.deps import admin, viewer
from backend.errors import DomainError
from backend.security.auth import create_user, digest, hash_password, verify_password
from backend.storage.db import new_id

router = APIRouter(prefix="/api/v1/auth", tags=["Authentication"])


class Login(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


@router.post("/login")
async def login(data: Login, request: Request, response: Response):
    app = request.app.state
    app.limiter.check("login:" + (request.client.host if request.client else "unknown"), 6)
    user = app.store.one("SELECT * FROM users WHERE username=?", (data.username,))
    # Perform the same expensive work for unknown users to reduce username disclosure.
    valid = verify_password(data.password, user["password"] if user else app.dummy_password)
    if not user or not valid:
        raise DomainError("invalid_credentials", "Username or password is incorrect.", 401)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
    expires = time.time() + app.settings.session_hours * 3600
    app.store.execute("INSERT INTO sessions VALUES(?,?,?,?)", (digest(token), user["id"], csrf, expires))
    response.set_cookie(
        "pi_session",
        token,
        httponly=True,
        secure=app.settings.secure_cookie,
        samesite="strict",
        max_age=app.settings.session_hours * 3600,
        path="/",
    )
    app.store.audit(user["username"], "auth.login")
    return {"username": user["username"], "role": user["role"], "csrf": csrf, "expires": expires}


@router.get("/me")
async def me(user=Depends(viewer)):
    return {key: user.get(key) for key in ("username", "role", "csrf", "expires", "method")}


@router.post("/logout")
async def logout(request: Request, response: Response, user=Depends(viewer)):
    request.app.state.store.execute(
        "DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies.get("pi_session", "")),)
    )
    response.delete_cookie("pi_session", path="/")
    return {"success": True}


@router.post("/password")
async def password(data: dict, request: Request, user=Depends(viewer)):
    if user["method"] != "session":
        raise DomainError("session_required", "Use a browser session to change your password.", 403)
    stored = request.app.state.store.one("SELECT password FROM users WHERE id=?", (user["id"],))
    if not verify_password(str(data.get("current_password", "")), stored["password"]):
        raise DomainError("invalid_credentials", "The current password is incorrect.", 401)
    hashed = hash_password(str(data.get("new_password", "")))
    request.app.state.store.execute("UPDATE users SET password=? WHERE id=?", (hashed, user["id"]))
    request.app.state.store.execute(
        "DELETE FROM sessions WHERE user_id=? AND token_hash<>?",
        (user["id"], digest(request.cookies.get("pi_session", ""))),
    )
    request.app.state.store.audit(user["username"], "auth.password_changed")
    return {"success": True}


@router.get("/tokens")
async def tokens(request: Request, user=Depends(admin)):
    return {
        "items": request.app.state.store.rows(
            "SELECT id,name,role,rate,created,last_used FROM tokens ORDER BY created DESC"
        )
    }


@router.post("/tokens", status_code=201)
async def new_token(data: dict, request: Request, user=Depends(admin)):
    role, name, rate = data.get("role", "operator"), str(data.get("name", "Agent")), int(data.get("rate", 60))
    if role not in {"viewer", "operator", "admin"} or not 1 <= rate <= 600 or not 1 <= len(name) <= 100:
        raise DomainError(
            "invalid_token", "Choose a valid name, role, and rate between 1 and 600 requests per minute."
        )
    token, token_id = "pidec_" + secrets.token_urlsafe(32), new_id()
    request.app.state.store.execute(
        "INSERT INTO tokens(id,name,token_hash,role,rate,created) VALUES(?,?,?,?,?,?)",
        (token_id, name, digest(token), role, rate, time.time()),
    )
    request.app.state.store.audit(user["username"], "auth.token_created", token_id)
    return {
        "id": token_id,
        "name": name,
        "role": role,
        "token": token,
        "notice": "Copy this token now. It will never be returned again.",
    }


@router.delete("/tokens/{token_id}")
async def revoke_token(token_id: str, request: Request, user=Depends(admin)):
    request.app.state.store.execute("DELETE FROM tokens WHERE id=?", (token_id,))
    request.app.state.store.audit(user["username"], "auth.token_revoked", token_id)
    return {"success": True}


@router.get("/users")
async def users(request: Request, user=Depends(admin)):
    return {
        "items": request.app.state.store.rows("SELECT id,username,role,created FROM users ORDER BY username")
    }


@router.post("/users", status_code=201)
async def new_user(data: dict, request: Request, user=Depends(admin)):
    result = create_user(
        request.app.state.store,
        str(data.get("username", "")),
        str(data.get("password", "")),
        str(data.get("role", "viewer")),
    )
    request.app.state.store.audit(user["username"], "auth.user_created", result["id"])
    return result

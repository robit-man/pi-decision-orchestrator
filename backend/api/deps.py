from fastapi import Request

from backend.security.auth import principal


def viewer(request: Request):
    return principal(request)


def operator(request: Request):
    return principal(request, "operator")


def admin(request: Request):
    return principal(request, "admin")


def services(request: Request):
    return request.app.state

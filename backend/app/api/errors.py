"""Translate request failures into the public error contract."""

import logging

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

from backend.app.core.request_context import get_request_id

_logger = logging.getLogger(__name__)

_HTTP_ERRORS = {
    400: ("INVALID_CURSOR", "分页游标无效"),
    401: ("AUTH_REQUIRED", "需要身份认证"),
    403: ("FORBIDDEN", "权限不足"),
    404: ("RESOURCE_NOT_FOUND", "资源不存在"),
    405: ("METHOD_NOT_ALLOWED", "请求方法不受支持"),
    422: ("VALIDATION_ERROR", "请求校验失败"),
    500: ("INTERNAL_ERROR", "请求处理失败"),
    503: ("SERVICE_NOT_READY", "服务尚未就绪"),
}
_VALIDATION_SOURCES = {"body", "query", "path", "header", "cookie"}
_HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "TRACE", "CONNECT"}


class InvalidTokenError(HTTPException):
    """The request supplied credentials that could not be authenticated."""

    def __init__(self) -> None:
        super().__init__(status_code=401)


class ValidationIssue(BaseModel):
    loc: list[str | int]
    type: str


class ErrorInfo(BaseModel):
    code: str
    message: str
    request_id: str
    details: list[ValidationIssue]


class ErrorResponse(BaseModel):
    error: ErrorInfo


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[ValidationIssue] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = get_request_id()
    assert request_id is not None
    envelope = ErrorResponse(
        error=ErrorInfo(code=code, message=message, request_id=request_id, details=details or []),
    )
    return JSONResponse(status_code=status_code, content=envelope.model_dump(), headers=headers)


async def handle_http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
    fallback = _HTTP_ERRORS[500] if exc.status_code >= 500 else ("HTTP_ERROR", "请求处理失败")
    code, message = _HTTP_ERRORS.get(exc.status_code, fallback)
    if isinstance(exc, InvalidTokenError):
        code, message = "INVALID_TOKEN", "身份凭据无效"

    headers: dict[str, str] = {}
    if exc.status_code == 401:
        headers["WWW-Authenticate"] = "Bearer"
    if exc.status_code == 405:
        allow = (exc.headers or {}).get("Allow", "")
        if allow and all(method.strip() in _HTTP_METHODS for method in allow.split(",")):
            headers["Allow"] = allow
    return _error_response(exc.status_code, code, message, headers=headers)


async def handle_validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    issues = []
    for error in exc.errors():
        loc = error.get("loc", ())
        # A key in a request map can itself contain credentials or private input.
        # Keep the standard source and numeric offsets without reflecting key names.
        safe_loc: list[str | int] = []
        if loc and loc[0] in _VALIDATION_SOURCES:
            safe_loc = [loc[0], *(part for part in loc[1:] if type(part) is int)]
        issues.append(ValidationIssue(loc=safe_loc, type=error["type"]))
    return _error_response(422, *_HTTP_ERRORS[422], details=issues)


def internal_error_response() -> JSONResponse:
    _logger.error("Unhandled exception while processing request")
    return _error_response(500, *_HTTP_ERRORS[500])

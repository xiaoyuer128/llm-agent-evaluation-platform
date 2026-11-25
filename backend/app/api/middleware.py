"""Associate responses and logs with a request-scoped identifier."""

from starlette.datastructures import Headers, MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.app.api.errors import internal_error_response
from backend.app.core.request_context import (
    get_request_id,
    is_valid_request_id,
    reset_request_id,
    set_request_id,
)

_REQUEST_ID_HEADER = "X-Request-ID"


class RequestIDMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope).getlist(_REQUEST_ID_HEADER)
        incoming = headers[0] if len(headers) == 1 else None
        token = set_request_id(incoming if is_valid_request_id(incoming) else None)
        request_id = get_request_id()
        assert request_id is not None
        response_started = False

        async def send_with_request_id(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)[_REQUEST_ID_HEADER] = request_id
                response_started = True
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            if response_started:
                # A partial response cannot be replaced. Abort without forwarding private data.
                raise RuntimeError("Request failed after response headers were sent") from None
            # Handle the 500 while the request ID is still bound. Forwarding the raw
            # exception to the ASGI server could expose its traceback and message.
            await internal_error_response()(scope, receive, send_with_request_id)
        finally:
            reset_request_id(token)

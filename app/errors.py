"""Réponses d'erreur des API exposées à la banque.

Seuls les codes de succès sont connus (DP02200000001 pour users, DP02800001001 pour notify).
Les codes d'erreur ci-dessous sont des valeurs du mock, à remplacer si la spec réelle les fournit.
"""

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

USERS_RESULT_CODES = {
    200: "DP02200000001",
    400: "DP02400000001",
    401: "DP02401000001",
    404: "DP02404000001",
    500: "DP02500000001",
}

NOTIFY_RESPONSE_CODES = {
    200: "DP02800001001",
    400: "DP02800004001",
    401: "DP02800004011",
    404: "DP02800004041",
    500: "DP02800005001",
}

AUTH_PATH_PREFIX = "/auth"
USERS_PATH_PREFIX = "/standard"
NOTIFY_PATH_PREFIX = "/term-loans"
PARTNER_PATH_PREFIXES = (AUTH_PATH_PREFIX, USERS_PATH_PREFIX, NOTIFY_PATH_PREFIX)


class ApiError(Exception):
    def __init__(self, status_code: int, message: str, headers: dict[str, str] | None = None):
        self.status_code = status_code
        self.message = message
        self.headers = headers


class OAuthError(Exception):
    """Erreur OAuth2 (RFC 6749 §5.2) renvoyée par /auth/oauth2/token."""

    def __init__(self, status_code: int, error: str, description: str):
        self.status_code = status_code
        self.error = error
        self.description = description


def users_status(code: int, message: str) -> dict:
    return {
        "code": str(code),
        "message": message,
        "result_code": USERS_RESULT_CODES.get(code, USERS_RESULT_CODES[500]),
        "success": code == 200,
    }


def notify_status(code: int, message: str) -> dict:
    return {
        "response_code": NOTIFY_RESPONSE_CODES.get(code, NOTIFY_RESPONSE_CODES[500]),
        "code": str(code),
        "success": code == 200,
        "message": message,
    }


def error_response(request: Request, status_code: int, message: str, headers=None) -> JSONResponse:
    if request.url.path.startswith(NOTIFY_PATH_PREFIX):
        status = notify_status(status_code, message)
    else:
        status = users_status(status_code, message)
    return JSONResponse(status_code=status_code, content={"data": None, "status": status}, headers=headers)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(request, exc.status_code, exc.message, exc.headers)


async def oauth_error_handler(request: Request, exc: OAuthError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.error, "error_description": exc.description},
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    if request.url.path.startswith((USERS_PATH_PREFIX, NOTIFY_PATH_PREFIX)):
        details = "; ".join(_format_error(e) for e in exc.errors())
        return error_response(request, 400, f"Invalid request: {details}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    if request.url.path.startswith((USERS_PATH_PREFIX, NOTIFY_PATH_PREFIX)):
        return error_response(request, 500, "Internal server error")
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})


def _format_error(error: dict) -> str:
    location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
    message = error.get("msg", "invalid value").removeprefix("Value error, ")
    return f"{location}: {message}" if location else message

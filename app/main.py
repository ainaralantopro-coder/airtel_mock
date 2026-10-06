from pathlib import Path

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles

from app.errors import (
    ApiError,
    OAuthError,
    api_error_handler,
    oauth_error_handler,
    unhandled_error_handler,
    validation_error_handler,
)
from app.middleware import CallReceiveMiddleware
from app.routers import auth, loans, notify, ui, users

app = FastAPI(
    title="Airtel Money mock",
    description="Mock of the Mobile Money platform for integration with the partner bank.",
    version="0.1.0",
)

app.add_middleware(CallReceiveMiddleware)
app.add_exception_handler(ApiError, api_error_handler)
app.add_exception_handler(OAuthError, oauth_error_handler)
app.add_exception_handler(RequestValidationError, validation_error_handler)
app.add_exception_handler(Exception, unhandled_error_handler)

app.mount("/static", StaticFiles(directory=Path(__file__).resolve().parent / "static"), name="static")

app.include_router(auth.router)
app.include_router(users.router)
app.include_router(notify.router)
app.include_router(ui.router)
app.include_router(loans.router)

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from api.routes import router
from api.v2_routes import router as v2_router
from web.routes import router as web_router


app = FastAPI(
    title="Genetic Circuit Design API",
    version="2.0.0",
    description=(
        "API foundation for computational genetic-circuit design imports, "
        "comparison, evaluation, and export."
    ),
)
default_origins = [
    "http://127.0.0.1:8501",
    "http://localhost:8501",
    "http://127.0.0.1:3000",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://localhost:5173",
]
allowed_origins = [
    item.strip()
    for item in os.getenv(
        "GENETIC_CIRCUIT_CORS_ORIGINS",
        ",".join(default_origins),
    ).split(",")
    if item.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)
app.include_router(router)
app.include_router(v2_router)
app.include_router(web_router)
app.mount(
    "/static",
    StaticFiles(directory=str(Path(__file__).resolve().parent.parent / "web" / "static")),
    name="static",
)


@app.exception_handler(HTTPException)
async def http_error(_request: Request, exc: HTTPException) -> JSONResponse:
    if isinstance(exc.detail, dict):
        error = exc.detail
    else:
        error = {
            "code": "HTTP_ERROR",
            "message": str(exc.detail),
            "details": [],
        }
    return JSONResponse(status_code=exc.status_code, content={"error": error})


@app.exception_handler(RequestValidationError)
async def validation_error(
    _request: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    details = []
    for item in exc.errors():
        detail = dict(item)
        context = detail.get("ctx")
        if isinstance(context, dict):
            detail["ctx"] = {
                key: str(value) if isinstance(value, BaseException) else value
                for key, value in context.items()
            }
        details.append(detail)
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "REQUEST_VALIDATION_FAILED",
                "message": "The request payload did not match the API contract.",
                "details": details,
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_error(_request: Request, _exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": "INTERNAL_ERROR",
                "message": "An unexpected server error occurred.",
                "details": [],
            }
        },
    )

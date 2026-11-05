"""ASGI entry point: `uvicorn orisun_iwadi.entrypoints.api.main:app`."""

from .app import create_app

app = create_app()

"""ASGI entrypoint:  uvicorn app.asgi:app"""
from app.api.main import create_app

app = create_app()

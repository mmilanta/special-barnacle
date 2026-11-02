import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from api import api
from db import get_store
from oauth import oauth_app
from ui import ui


@asynccontextmanager
async def lifespan(app: FastAPI):
    store = get_store()
    try:
        yield
    finally:
        await store.client.aclose()
        get_store.cache_clear()


app = FastAPI(lifespan=lifespan)
app.add_middleware(GZipMiddleware)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY"))

app.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static"
)
app.mount("/api/v1", api)
app.mount("/auth", oauth_app)
app.mount("/", ui)

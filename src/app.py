from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.sessions import SessionMiddleware
import os
from api import api
from ui import ui
from oauth import oauth_app

app = FastAPI()
app.add_middleware(GZipMiddleware)
app.add_middleware(SessionMiddleware, secret_key=os.getenv("SECRET_KEY"))
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/api/v1", api)
app.mount("/auth", oauth_app)
app.mount("/", ui)

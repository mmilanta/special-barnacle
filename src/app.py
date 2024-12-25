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


# get rid of HSTS header (https)
@app.middleware("http")
async def remove_hsts_header(request, call_next):
    response = await call_next(request)
    # Add or reset the Strict-Transport-Security header
    response.headers["Strict-Transport-Security"] = "max-age=0"
    return response


app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/api/v1", api)
app.mount("/auth", oauth_app)
app.mount("/", ui)

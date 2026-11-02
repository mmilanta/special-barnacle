import os
from typing import Annotated

from authlib.integrations.starlette_client import OAuth
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from starlette.config import Config

from data import fetch_superusers_email

oauth_app = FastAPI()

config = Config(environ=os.environ)
oauth = OAuth(config)
oauth.register(
    name="google",
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


@oauth_app.get("/login")
async def login(request: Request):
    redirect_uri = request.url_for("auth")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@oauth_app.get("/auth")
async def auth(request: Request):
    token = await oauth.google.authorize_access_token(request)
    user = token.get("userinfo")  # Contains user information
    user["is_superuser"] = user["email"] in await fetch_superusers_email()
    if user:
        request.session["user"] = user
    return RedirectResponse(url="/")


@oauth_app.get("/logout")
async def logout(request: Request):
    request.session.pop("user", None)
    return RedirectResponse(url="/")


# Dependency to verify the user is logged in
def get_current_user(request: Request) -> dict | None:
    return request.session.get("user")  # Replace with your auth logic


async def require_writer(
    user: Annotated[dict | None, Depends(get_current_user)],
) -> dict:
    if user is None:
        raise HTTPException(401, "Accedi con un account autorizzato")
    if user.get("email") not in await fetch_superusers_email():
        raise HTTPException(403, "Accesso riservato agli autori delle ricette")
    return user

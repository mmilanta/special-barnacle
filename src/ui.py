import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

import mistune
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from data import Recipe, fetch_superusers_email, fetch_valid_categories
from db import recipe_history, recipe_revision
from history_diff import recipe_diff
from oauth import get_current_user, require_writer

logger = logging.getLogger("uvicorn")

ui = FastAPI()
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")


@ui.get("/", response_class=HTMLResponse)
async def index_page(
    request: Request, user: Annotated[dict | None, Depends(get_current_user)]
):

    recipes = await Recipe.all()
    recipes_by_category: dict[str, list[dict[str, str]]] = {
        category: [] for category in await fetch_valid_categories()
    }
    for recipe in recipes:
        if recipe.category not in recipes_by_category:
            logger.error(f"Category {recipe.category} not found in valid categories")
            recipes_by_category[recipe.category] = []
        recipes_by_category[recipe.category].append(
            {"title": recipe.title, "id": recipe.id, "ingredients": recipe.ingredients}
        )
    for recipes_in_category in recipes_by_category.values():
        recipes_in_category.sort(key=lambda x: x["title"])
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "recipes_by_category": recipes_by_category,
            "valid_categories": await fetch_valid_categories(),
            "user": user,
        },
    )


@ui.get("/recipe/{recipe_id}", response_class=HTMLResponse)
async def recipe_page(
    request: Request, recipe_id, user: Annotated[dict | None, Depends(get_current_user)]
):
    try:
        recipe = await Recipe.load(id=recipe_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": "404", "message": "Recipe not found"},
            status_code=404,
        )
    recipe_dict = recipe_content(recipe)
    if user:
        user = {
            **user,
            "is_superuser": user.get("email") in await fetch_superusers_email(),
        }
    recipe_dict["user"] = user
    recipe_dict["last_edited"] = None
    try:
        history = await recipe_history(
            Recipe._to_path(Recipe.get_key(recipe_id)), per_page=1
        )
        if history["entries"]:
            recipe_dict["last_edited"] = revision_context(history["entries"][0])
    except HTTPException:
        # A metadata outage must not hide an otherwise available recipe.
        logger.warning("Recipe last-edited date is temporarily unavailable")
    return templates.TemplateResponse(
        request=request, name="recipe.html", context=recipe_dict
    )


@ui.get("/recipe/{recipe_id}/history", response_class=HTMLResponse)
async def recipe_history_page(
    request: Request,
    recipe_id: str,
    user: Annotated[dict, Depends(require_writer)],
    page: Annotated[int, Query(ge=1)] = 1,
    ref: Annotated[str | None, Query(pattern=r"^[0-9a-f]{40}$")] = None,
):
    history = await recipe_history(
        Recipe._to_path(Recipe.get_key(recipe_id)), page, ref
    )
    key = Recipe._to_path(Recipe.get_key(recipe_id))
    latest = await recipe_history(key, per_page=1)
    latest_sha = latest["entries"][0]["sha"] if latest["entries"] else None
    entries = [revision_context(item) for item in history["entries"]]
    for index, entry in enumerate(entries):
        entry["has_previous"] = index < len(entries) - 1 or history["has_more"]
    try:
        title = (await Recipe.load(recipe_id)).title
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        title = "Ricetta eliminata"
    url = request.url_for("recipe_history_page", recipe_id=recipe_id)
    anchor = history["ref"]
    return templates.TemplateResponse(
        request=request,
        name="recipe_history.html",
        context={
            "recipe_id": recipe_id,
            "title": title,
            "entries": entries,
            "latest_sha": latest_sha,
            "previous_url": url.include_query_params(page=page - 1, ref=anchor)
            if page > 1 and anchor
            else None,
            "next_url": url.include_query_params(page=page + 1, ref=anchor)
            if history["has_more"] and anchor
            else None,
        },
        headers={"Cache-Control": "private, no-store"},
    )


@ui.get("/recipe/{recipe_id}/history/{revision}/diff", response_class=HTMLResponse)
async def recipe_diff_page(
    request: Request,
    recipe_id: str,
    revision: Annotated[str, PathParam(pattern=r"^[0-9a-f]{40}$")],
    user: Annotated[dict, Depends(require_writer)],
    compare: Literal["previous", "current"] = "previous",
):
    key = Recipe._to_path(Recipe.get_key(recipe_id))
    selected = await recipe_history(key, ref=revision, per_page=2)
    if not selected["entries"] or selected["entries"][0]["sha"] != revision:
        raise HTTPException(404, "Versione non trovata nella cronologia della ricetta")
    if compare == "previous":
        before_sha = (
            selected["entries"][1]["sha"] if len(selected["entries"]) > 1 else None
        )
        after_sha = revision
    else:
        latest = await recipe_history(key, per_page=1)
        if not latest["entries"]:
            raise HTTPException(404, "Versione attuale non trovata")
        before_sha, after_sha = revision, latest["entries"][0]["sha"]
    if before_sha:
        before, after = await asyncio.gather(
            recipe_revision(key, before_sha), recipe_revision(key, after_sha)
        )
    else:
        before, after = None, await recipe_revision(key, after_sha)
    old_recipe = historical_recipe(before, recipe_id) if before else None
    new_recipe = historical_recipe(after, recipe_id)
    title_recipe = new_recipe or old_recipe
    changes = recipe_diff(
        old_recipe.model_dump() if old_recipe else None,
        new_recipe.model_dump() if new_recipe else None,
    )
    return templates.TemplateResponse(
        request=request,
        name="recipe_diff.html",
        context={
            "recipe_id": recipe_id,
            "title": title_recipe.title if title_recipe else "Ricetta eliminata",
            "compare": compare,
            "before": revision_context(before["revision"]) if before else None,
            "after": revision_context(after["revision"]),
            "changes": changes,
            "has_changes": any(not field["unchanged"] for field in changes),
        },
        headers={"Cache-Control": "private, no-store"},
    )


@ui.get("/recipe/{recipe_id}/history/{revision}", response_class=HTMLResponse)
async def recipe_version_page(
    request: Request,
    recipe_id: str,
    revision: Annotated[str, PathParam(pattern=r"^[0-9a-f]{40}$")],
    user: Annotated[dict, Depends(require_writer)],
):
    result = await recipe_revision(Recipe._to_path(Recipe.get_key(recipe_id)), revision)
    context = {
        "recipe_id": recipe_id,
        "revision": revision_context(result["revision"]),
        "deleted": result["data"] is None,
    }
    recipe = historical_recipe(result, recipe_id)
    if recipe is not None:
        context.update(recipe_content(recipe))
    else:
        context["title"] = "Ricetta eliminata"
    return templates.TemplateResponse(
        request=request,
        name="recipe_version.html",
        context=context,
        headers={"Cache-Control": "private, no-store"},
    )


def historical_recipe(result: dict, recipe_id: str) -> Recipe | None:
    if result["data"] is None:
        return None
    try:
        return Recipe.model_validate(
            {
                **json.loads(result["data"]),
                "id": recipe_id,
                "version": result["version"],
            }
        )
    except (ValueError, TypeError) as exc:
        raise HTTPException(502, "Questa versione non può essere visualizzata") from exc


def recipe_content(recipe: Recipe) -> dict:
    context = recipe.model_dump()
    context["ingredients"] = markdown_to_html(recipe.ingredients)
    context["steps"] = markdown_to_html(recipe.steps)
    notes = (recipe.notes or "").strip()
    context["notes"] = markdown_to_html(notes) if notes else ""
    return context


def revision_context(revision: dict) -> dict:
    date = revision.get("date")
    label = (
        datetime.fromisoformat(date).strftime("%d/%m/%Y")
        if date
        else "Data non disponibile"
    )
    return {**revision, "date_label": label}


@ui.get("/recipe/{recipe_id}/edit", response_class=HTMLResponse)
async def recipe_edit_page(request: Request, recipe_id):
    try:
        recipe = await Recipe.load(id=recipe_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        return templates.TemplateResponse(
            request=request,
            name="error.html",
            context={"status_code": "404", "message": "Recipe not found"},
            status_code=404,
        )
    return templates.TemplateResponse(
        request=request,
        name="recipe_edit.html",
        context={
            "recipe": recipe.model_dump(),
            "valid_categories": await fetch_valid_categories(),
        },
    )


@ui.get("/create-recipe", response_class=HTMLResponse)
async def create_recipe_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="create_recipe.html",
        context={"valid_categories": await fetch_valid_categories()},
    )


@ui.get("/error", response_class=HTMLResponse)
def error_page(request: Request, status: int, message: str):
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"status_code": str(status), "message": message},
    )


@ui.exception_handler(404)
def missing_page(request: Request, exc):
    return templates.TemplateResponse(
        request=request,
        name="error.html",
        context={"status_code": "404", "message": "Page not found"},
        status_code=404,
    )


def markdown_to_html(text: str) -> str:
    mkd = mistune.html(text)
    return mkd.replace("<ul>", '<ul class="ml-6 list-disc">')

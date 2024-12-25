import mistune
from fastapi import FastAPI, Request, Depends
from data import Recipe, fetch_valid_categories, GitStoreException
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from oauth import get_current_user

ui = FastAPI()

templates = Jinja2Templates(directory="templates")

@ui.get("/", response_class=HTMLResponse)
def index_page(request: Request, user: dict | None = Depends(get_current_user)):
    recipes = Recipe.all()
    recipes_by_category: dict[str, list[dict[str, str]]] = {}
    for recipe in recipes:
        recipes_by_category.setdefault(recipe.category, [])
        recipes_by_category[recipe.category].append(
            {"title": recipe.title, "id": recipe.id}
        )
    for category in recipes_by_category:
        recipes_by_category[category].sort(key=lambda x: x["title"])
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "recipes_by_category": recipes_by_category,
            "valid_categories": fetch_valid_categories(),
            "user": user
        },
    )


@ui.get("/recipe/{recipe_id}", response_class=HTMLResponse)
def recipe_page(request: Request, recipe_id, user: dict | None = Depends(get_current_user)):
    try:
        recipe = Recipe.load(id=recipe_id)
    except GitStoreException:
        return templates.TemplateResponse(request=request, name="error.html", context={"status_code": "404", "message": "Recipe not found"})
    recipe_dict = recipe.model_dump()
    recipe_dict["ingredients"] = markdown_to_html(recipe.ingredients)
    recipe_dict["steps"] = markdown_to_html(recipe.steps)
    recipe_dict["notes"] = markdown_to_html(recipe.notes)
    recipe_dict["user"] = user
    print(f"user: {user}")
    return templates.TemplateResponse(
        request=request, name="recipe.html", context=recipe_dict
    )


@ui.get("/recipe/{recipe_id}/edit", response_class=HTMLResponse)
def recipe_edit_page(request: Request, recipe_id):
    try:
        recipe = Recipe.load(id=recipe_id)
    except GitStoreException:
        return templates.TemplateResponse(request=request, name="error.html", context={"status_code": "404", "message": "Recipe not found"})
    return templates.TemplateResponse(
        request=request, name="recipe_edit.html", context={"recipe": recipe.model_dump(), "valid_categories": fetch_valid_categories()}
    )


@ui.get("/error", response_class=HTMLResponse)
def error_page(request: Request, status: int, message: str):
    return templates.TemplateResponse(request=request, name="error.html", context={"status_code": str(status), "message": message})



@ui.exception_handler(404)
def missing_page(request: Request, exc):
    return templates.TemplateResponse(request=request, name="error.html", context={"status_code": "404", "message": "Page not found"})


def markdown_to_html(text: str) -> str:
    mkd = mistune.html(text)
    return mkd.replace("<ul>", '<ul class="ml-6 list-disc">')

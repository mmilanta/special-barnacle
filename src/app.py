from fastapi import FastAPI, Request, HTTPException
import logging
from data import Recipe, fetch_valid_categories
from ui import markdown_to_html
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware


app = FastAPI()


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# API


@app.get("/api/recipe/{recipe_id}")
def get_recipe(recipe_id: str) -> Recipe:
    recipe = Recipe.load(id=recipe_id)
    return recipe


@app.get("/api/recipe")
def get_recipes_index() -> list[Recipe]:
    recipes = Recipe.all()
    return recipes


@app.put("/api/recipe/{recipe_id}")
def post_recipes_index(recipe_id: str, recipe: Recipe) -> Recipe:
    if recipe.id != recipe_id:
        raise HTTPException(status_code=422, detail="recipe id must match the url")
    recipe.save()
    return recipe


@app.delete("/api/recipe/{recipe_id}", response_class=HTMLResponse)
def delete_recipe(recipe_id: str):
    Recipe.delete(id=recipe_id)


@app.post("/api/recipe")
def post_recipe() -> Recipe:
    recipe = Recipe.new_empty()
    recipe.save()
    return recipe


# UI

app.mount("/static", StaticFiles(directory="static"), name="static")
app.add_middleware(GZipMiddleware)

templates = Jinja2Templates(directory="templates")


@app.get("/index", response_class=HTMLResponse)
def index_page(request: Request):
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
        context={"recipes_by_category": recipes_by_category, "valid_categories": fetch_valid_categories()},
    )


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
def recipe_page(request: Request, recipe_id):
    recipe = Recipe.load(id=recipe_id)
    recipe_dict = recipe.model_dump()
    recipe_dict["ingredients"] = markdown_to_html(recipe_dict["ingredients"])
    recipe_dict["steps"] = markdown_to_html(recipe_dict["steps"])
    return templates.TemplateResponse(
        request=request, name="recipe.html", context=recipe_dict
    )


@app.get("/recipe/{recipe_id}/edit", response_class=HTMLResponse)
def recipe_edit_page(request: Request, recipe_id):
    recipe = Recipe.load(id=recipe_id)
    return templates.TemplateResponse(
        request=request, name="recipe_edit.html", context={"recipe": recipe.model_dump(), "valid_categories": fetch_valid_categories()}
    )

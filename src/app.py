from fastapi import FastAPI, Request
import logging
from data import Recipe, RecipeRequest
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
from markdown import markdown

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


@app.patch("/api/recipe/{recipe_id}")
def post_recipes_index(recipe_id: str, recipe: Recipe) -> Recipe:
    recipe.save()
    return recipe


# UI


templates = Jinja2Templates(directory="templates")


@app.get("/index", response_class=HTMLResponse)
def index_page(request: Request):
    recipes = Recipe.all()
    recipes_by_category = {}
    for recipe in recipes:
        recipes_by_category.setdefault(recipe.category, [])
        recipes_by_category[recipe.category].append({
            "title": recipe.title,
            "id": recipe.id
        })
    return templates.TemplateResponse(
        request=request, name="index.html", context={"recipes_by_category": recipes_by_category}
    )


@app.get("/recipe/{recipe_id}", response_class=HTMLResponse)
def recipe_page(request: Request, recipe_id):
    recipe = Recipe.load(id=recipe_id)
    recipe_dict = recipe.model_dump()
    recipe_dict["ingredients"] = markdown(recipe_dict["ingredients"])
    recipe_dict["steps"] = markdown(recipe_dict["steps"])
    return templates.TemplateResponse(request=request, name="recipe.html", context=recipe_dict)


@app.get("/recipe/{recipe_id}/edit", response_class=HTMLResponse)
def recipe_edit_page(request: Request, recipe_id):
    recipe = Recipe.load(id=recipe_id)
    return templates.TemplateResponse(request=request, name="recipe_edit.html", context=recipe.model_dump())


@app.put("/recipe/{recipe_id}/edit")
def recipe_edit(recipe_id, recipe_request: RecipeRequest):
    recipe = Recipe(
        id=recipe_id,
        title=recipe_request.title,
        category=recipe_request.category,
        ingredients=recipe_request.ingredients,
        steps=recipe_request.steps,
    )
    recipe.save()
    return recipe

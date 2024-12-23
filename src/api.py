from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from data import Recipe

api = FastAPI()

@api.get("/recipe/{recipe_id}")
def get_recipe(recipe_id: str) -> Recipe:
    recipe = Recipe.load(id=recipe_id)
    return recipe


@api.get("/recipe")
def get_recipes_index() -> list[Recipe]:
    recipes = Recipe.all()
    return recipes


@api.put("/recipe/{recipe_id}")
def post_recipes_index(recipe_id: str, recipe: Recipe) -> Recipe:
    if recipe.id != recipe_id:
        raise HTTPException(status_code=422, detail="recipe id must match the url")
    recipe.save()
    return recipe


@api.delete("/recipe/{recipe_id}", response_class=HTMLResponse)
def delete_recipe(recipe_id: str):
    Recipe.delete(id=recipe_id)


@api.post("/recipe")
def post_recipe() -> Recipe:
    recipe = Recipe.new_empty()
    recipe.save()
    return recipe
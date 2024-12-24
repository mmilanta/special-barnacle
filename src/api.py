from fastapi import FastAPI, HTTPException, Depends
from fastapi.responses import HTMLResponse
from data import Recipe, fetch_superusers_email
from oauth import get_current_user

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
def post_recipes_index(recipe_id: str, recipe: Recipe, user: dict = Depends(get_current_user)) -> Recipe:
    print(f"user: {user}")
    if recipe.id != recipe_id:
        raise HTTPException(status_code=422, detail="Recipe id must match the url")
    if user is None:
        raise HTTPException(status_code=401, detail="User must be logged in to edit recipe")
    if user["email"] not in fetch_superusers_email():
        raise HTTPException(status_code=403, detail="User must be a superuser to edit recipe")
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
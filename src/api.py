from typing import Annotated
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse

from data import Recipe
from oauth import require_writer

api = FastAPI()


@api.get("/recipe/{recipe_id}")
async def get_recipe(recipe_id: str) -> Recipe:
    recipe = await Recipe.load(id=recipe_id)
    return recipe


@api.get("/recipe")
async def get_recipes_index() -> list[Recipe]:
    recipes = await Recipe.all()
    return recipes


@api.put("/recipe/{recipe_id}")
async def post_recipes_index(
    recipe_id: str,
    recipe: Recipe,
    user: Annotated[dict, Depends(require_writer)],
) -> Recipe:
    if recipe.id != recipe_id:
        raise HTTPException(400, "Recipe ID must match the URL")
    await recipe.save(
        author={"name": user.get("name") or user["email"], "email": user["email"]}
    )
    return recipe


@api.delete("/recipe/{recipe_id}", response_class=HTMLResponse)
async def delete_recipe(
    recipe_id: str,
    version: Annotated[str, Query(pattern=r"^[0-9a-f]{40}$")],
    user: Annotated[dict, Depends(require_writer)],
):
    await Recipe.delete(
        id=recipe_id,
        version=version,
        author={"name": user.get("name") or user["email"], "email": user["email"]},
    )


@api.get("/uuid")
async def get_uuid() -> UUID:
    return uuid4()

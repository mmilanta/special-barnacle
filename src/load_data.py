from data import Recipe
import uuid
import json
new_recipes = json.load(open("new_recipe.json"))
recipes = []
for nr in new_recipes:
    recipes.append(Recipe(
        id=str(uuid.uuid4()),
        title=nr["title"],
        category=nr["category"],
        ingredients=nr["ingredients"],
        steps=nr["steps"],
        notes=nr["notes"]
    ))
for recipe in recipes:
    recipe.save()
from src.gitdata.db import GitDB
from src.gitdata.schema import Recipe

db = GitDB("data")

r = db.get_recipe("xejc5l23r4")
r2 = Recipe(title=r.title, category=r.category, ingredients=r.ingredients, steps=r.steps, notes="Fantastico!!")
db.set_recipe("xejc5l23r4", r2, user="io")

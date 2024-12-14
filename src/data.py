from pydantic import BaseModel
import os
from typing import Literal
import requests
from typing import ClassVar
import json
from enum import Enum


class DBModel(BaseModel):
    prefix: ClassVar[Literal["prefix"]] = "prefix"
    id: str

    @classmethod
    def get_key(cls, id: str) -> str:
        return cls.prefix + ":" + id

    @classmethod
    def delete(cls, id: str) -> None:
        key = cls.get_key(id=id)
        delete_db(key)

    @classmethod
    def load(cls, id: str):
        key = cls.get_key(id=id)
        model_dict = get_db(key)
        return cls(**model_dict, id=id)

    def save(self):
        key = self.get_key(id=self.id)
        set_db(key, self.model_dump(exclude={"prefix", "id"}))

    @classmethod
    def all(cls):
        keys = [k for k in list_db() if k.startswith(cls.prefix)]
        ids = [k[len(cls.prefix) + 1 :] for k in keys]
        return [cls.load(id) for id in ids]


CACHE: dict[str, dict] = {}


def get_db(key: str) -> dict:
    if key in CACHE:
        return CACHE[key]
    request = requests.get(db_path(key))
    if request.status_code not in [200, 202]:
        raise GitStoreException()
    CACHE[key] = json.loads(request.content)
    return CACHE[key]


def set_db(key: str, data: dict) -> None:
    if key in CACHE:
        CACHE.pop(key)
    data_json = json.dumps(data, indent=4).encode("utf-8")
    request = requests.put(db_path(key), data=data_json)
    if request.status_code not in [200, 202]:
        raise GitStoreException()


def delete_db(key: str) -> None:
    if key in CACHE:
        CACHE.pop(key)
    request = requests.delete(db_path(key))
    if request.status_code not in [200, 202]:
        raise GitStoreException()


def list_db() -> list[str]:
    request = requests.get(db_path())
    if request.status_code not in [200, 202]:
        raise GitStoreException()
    paths = json.loads(request.content)
    keys = [path[:-5] for path in paths if path.endswith(".json")]
    return keys


def fetch_valid_categories() -> dict[str, str]:
    valid_categories = set(recipe.category for recipe in Recipe.all())
    return {category: category.replace("_", " ").title() for category in valid_categories}

class HTTPMethod(Enum):
    GET = "get"
    PUT = "put"
    DELETE = "delete"


def db_path(key: str = "", extension: str = ".json") -> str:
    if key:
        key += extension
    return f"http://{os.environ['GIT_STORE_HOST']}:{os.environ['GIT_STORE_PORT']}/{key}"


class GitStoreException(Exception):
    pass


class Recipe(DBModel):
    prefix: ClassVar[Literal["recipe"]] = "recipe"
    title: str
    category: str
    ingredients: str
    steps: str


class RecipeRequest(BaseModel):
    title: str
    category: str
    ingredients: str
    steps: str

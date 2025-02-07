from pydantic import BaseModel
from typing import Literal
from typing import ClassVar
import json
from db import get_data, set_data, delete_data, list_data


class DBModel(BaseModel):
    prefix: ClassVar[Literal["prefix"]] = "prefix"
    id: str

    @classmethod
    def get_key(cls, id: str) -> str:
        return cls.prefix + ":" + id

    @classmethod
    async def delete(cls, id: str) -> None:
        key = cls.get_key(id=id)
        await delete_db(db_path(key))

    @classmethod
    async def load(cls, id: str):
        key = cls.get_key(id=id)
        model_dict = await get_db(db_path(key))
        return cls(**model_dict, id=id)

    async def save(self):
        key = self.get_key(id=self.id)
        await set_db(db_path(key), self.model_dump(exclude={"prefix", "id"}))

    @classmethod
    async def all(cls):
        keys = [k for k in await list_db() if k.startswith(cls.prefix)]
        ids = [k[len(cls.prefix) + 1 :] for k in keys]
        return [await cls.load(id) for id in ids]


CACHE: dict[str, dict] = {}


async def get_db(key: str) -> dict:
    if key in CACHE:
        return CACHE[key]
    data = await get_data(key)
    CACHE[key] = json.loads(data)
    return CACHE[key]


async def set_db(key: str, data: dict) -> None:
    if key in CACHE:
        CACHE.pop(key)
    data_json = json.dumps(data, indent=4).encode("utf-8")
    await set_data(key, data_json)

async def delete_db(key: str) -> None:
    if key in CACHE:
        CACHE.pop(key)
    
    await delete_data(key)


async def list_db() -> list[str]:
    paths = await list_data()
    keys = [path[:-5] for path in paths if path.endswith(".json")]
    return keys


async def fetch_valid_categories() -> list[str]:
    if "valid_categories" not in CACHE:
        data = await get_data(db_path("valid_categories"))
        CACHE["valid_categories"] = json.loads(data)
    return CACHE["valid_categories"]


async def fetch_superusers_email() -> list[str]:
    if "superusers" not in CACHE:
        data = await get_data(db_path("superusers"))
        CACHE["superusers"] = json.loads(data)
    return CACHE["superusers"]


def db_path(key: str = "", extension: str = ".json") -> str:
    if key:
        key += extension
    return key


class GitStoreException(Exception):
    pass


class Recipe(DBModel):
    prefix: ClassVar[Literal["recipe"]] = "recipe"
    title: str
    category: str
    ingredients: str
    steps: str
    notes: str | None



class User(DBModel):
    prefix: ClassVar[Literal["user"]] = "user"
    name: str
    email: str
    is_superuser: bool

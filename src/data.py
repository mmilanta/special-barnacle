from pydantic import BaseModel
from typing import Literal
from typing import ClassVar
import json
from db import get_data, set_data, delete_data, list_data


class DBModel(BaseModel):
    prefix: ClassVar[str]
    file_extension: ClassVar[str]
    id: str

    @classmethod
    def get_key(cls, id: str) -> str:
        return cls.prefix + ":" + id

    @classmethod
    async def delete(cls, id: str) -> None:
        key = cls.get_key(id=id)
        await delete_data(cls._to_path(key))

    @classmethod
    async def load(cls, id: str):
        key = cls.get_key(id=id)
        model_raw = await get_data(cls._to_path(key))
        model_dict = json.loads(model_raw)
        model_dict["id"] = id
        return cls.model_validate(model_dict)

    async def save(self: "DBModel"):
        key = self.get_key(id=self.id)
        data_json = json.dumps(self.model_dump(exclude={"prefix", "id", "file_extension"}), indent=4).encode("utf-8")
        await set_data(self._to_path(key), data_json)

    @classmethod
    async def all(cls):
        keys = [k for k in await list_data() if k.startswith(cls.prefix)]
        ids = [k[len(cls.prefix) + 1 : - len(cls.file_extension)] for k in keys]
        return [await cls.load(id) for id in ids]

    @classmethod
    def _to_path(cls, key: str) -> str:
        return key + cls.file_extension


def to_path(key: str = "", extension: str = ".json") -> str:
    if key:
        key += extension
    return key


async def fetch_valid_categories() -> list[str]:
    data = await get_data("valid_categories.json")
    return json.loads(data)


async def fetch_superusers_email() -> list[str]:
    data = await get_data("superusers.json")
    return json.loads(data)


class Recipe(DBModel):
    prefix: ClassVar[Literal["recipe"]] = "recipe"
    file_extension: ClassVar[Literal[".json"]] = ".json"
    title: str
    category: str
    ingredients: str
    steps: str
    notes: str | None



class User(DBModel):
    prefix: ClassVar[Literal["user"]] = "user"
    file_extension: ClassVar[Literal[".json"]] = ".json"
    name: str
    email: str
    is_superuser: bool

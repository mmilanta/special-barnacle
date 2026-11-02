import json
from typing import ClassVar, Literal

from pydantic import BaseModel, Field

from db import delete_data, get_data, get_record, list_data, set_data


class DBModel(BaseModel):
    prefix: ClassVar[str]
    file_extension: ClassVar[str]
    id: str
    version: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    @classmethod
    def get_key(cls, id: str) -> str:
        return cls.prefix + ":" + id

    @classmethod
    async def delete(cls, id: str, version: str, author: dict | None = None) -> None:
        key = cls.get_key(id=id)
        await delete_data(cls._to_path(key), version, author)

    @classmethod
    async def load(cls, id: str):
        key = cls.get_key(id=id)
        model_raw, version = await get_record(cls._to_path(key))
        model_dict = json.loads(model_raw)
        model_dict["id"] = id
        model_dict["version"] = version
        return cls.model_validate(model_dict)

    async def save(self: "DBModel", author: dict | None = None):
        key = self.get_key(id=self.id)
        data_json = json.dumps(
            self.model_dump(exclude={"prefix", "id", "file_extension", "version"}),
            indent=4,
        ).encode("utf-8")
        self.version = await set_data(
            self._to_path(key), data_json, self.version, author
        )

    @classmethod
    async def all(cls):
        keys = [
            k
            for k in await list_data()
            if k.startswith(cls.prefix + ":") and k.endswith(cls.file_extension)
        ]
        ids = [k[len(cls.prefix) + 1 : -len(cls.file_extension)] for k in keys]
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

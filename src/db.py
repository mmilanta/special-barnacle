"""GitHub-backed JSON storage. No durable state lives in the container."""

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from functools import lru_cache
from io import BytesIO
from urllib.parse import quote
from zipfile import BadZipFile, ZipFile

import httpx
from fastapi import HTTPException

logger = logging.getLogger("uvicorn")
CONFLICT = "La ricetta è cambiata. Aprila in una nuova scheda e confronta le modifiche prima di riprovare."
UNAVAILABLE = (
    "Archivio GitHub non disponibile. Riprova; il salvataggio non è confermato."
)


class GitHubStore:
    def __init__(
        self,
        client: httpx.AsyncClient,
        repo: str,
        branch: str = "main",
        cache_seconds: float = 60,
    ):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo) or not branch:
            raise ValueError(
                "Set REPO to owner/repository and GITHUB_BRANCH to a branch name"
            )
        self.client = client
        self.base = f"https://api.github.com/repos/{repo}"
        self.branch = branch
        self.cache_seconds = cache_seconds
        self.files: dict[str, tuple[bytes, str]] = {}
        self.expires_at = 0.0
        self.lock = asyncio.Lock()

    async def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            self.expires_at = 0
            raise HTTPException(503, UNAVAILABLE) from exc
        if response.is_success:
            return response
        self.expires_at = 0
        if response.status_code in (409, 422):
            raise HTTPException(409, CONFLICT)
        # Never expose GitHub response bodies, signed archive URLs, or credentials.
        logger.warning(
            "GitHub storage request failed: %s %s", method, response.status_code
        )
        raise HTTPException(503, UNAVAILABLE)

    async def _refresh(self):
        if time.monotonic() < self.expires_at:
            return
        response = await self._request(
            "GET",
            f"{self.base}/zipball/{quote(self.branch, safe='')}",
            follow_redirects=True,
        )
        # One snapshot instead of an API call for every recipe. Never extract to disk.
        files = {}
        try:
            with ZipFile(BytesIO(response.content)) as archive:
                for entry in archive.infolist():
                    parts = entry.filename.split("/")
                    if (
                        len(parts) == 2
                        and parts[1].endswith(".json")
                        and not entry.is_dir()
                    ):
                        data = archive.read(entry)
                        sha = hashlib.sha1(
                            b"blob " + str(len(data)).encode() + b"\0" + data
                        ).hexdigest()
                        files[parts[1]] = (data, sha)
        except (BadZipFile, RuntimeError) as exc:
            raise HTTPException(503, UNAVAILABLE) from exc
        self.files = files
        self.expires_at = time.monotonic() + self.cache_seconds

    @staticmethod
    def _validate_key(key: str):
        if not re.fullmatch(r"[\w:.-]+\.json", key):
            raise HTTPException(400, "Nome del documento non valido")

    async def get(self, key: str) -> tuple[bytes, str]:
        self._validate_key(key)
        async with self.lock:
            await self._refresh()
            if key not in self.files:
                raise HTTPException(404, "Documento non trovato")
            return self.files[key]

    async def list(self) -> list[str]:
        async with self.lock:
            await self._refresh()
            return sorted(self.files)

    async def mutate(
        self, key: str, data: bytes | None, version: str | None
    ) -> str | None:
        self._validate_key(key)
        # GitHub requires Contents updates and deletions to be serialized.
        async with self.lock:
            await self._refresh()
            current = self.files.get(key)
            if (current[1] if current else None) != version:
                self.expires_at = 0
                raise HTTPException(409, CONFLICT)
            if data is None and current is None:
                raise HTTPException(404, "Documento non trovato")
            payload = {
                "message": f"{'deleted' if data is None else 'edit' if current else 'new'}: {key}",
                "branch": self.branch,
            }
            if version is not None:
                payload["sha"] = version
            if data is not None:
                payload["content"] = base64.b64encode(data).decode("ascii")
            response = await self._request(
                "DELETE" if data is None else "PUT",
                f"{self.base}/contents/{quote(key, safe='')}",
                json=payload,
            )
            if data is None:
                self.files.pop(key)
                return None
            sha = response.json()["content"]["sha"]
            self.files[key] = (data, sha)
            return sha


@lru_cache(maxsize=1)
def get_store() -> GitHubStore:
    token = os.environ["GITHUB_PAT"]
    if not token:
        raise ValueError(
            "GITHUB_PAT must contain a GitHub token with Contents read/write permission"
        )
    client = httpx.AsyncClient(
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
        timeout=httpx.Timeout(30, connect=10),
    )
    return GitHubStore(client, os.environ["REPO"], os.getenv("GITHUB_BRANCH", "main"))


async def get_record(key: str) -> tuple[bytes, str]:
    return await get_store().get(key)


async def get_data(key: str) -> bytes:
    data, _ = await get_record(key)
    return data


async def set_data(key: str, data: bytes, version: str | None = None) -> str:
    sha = await get_store().mutate(key, data, version)
    assert sha is not None
    return sha


async def delete_data(key: str, version: str) -> None:
    await get_store().mutate(key, None, version)


async def list_data() -> list[str]:
    return await get_store().list()

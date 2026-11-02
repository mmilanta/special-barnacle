"""GitHub-backed JSON storage. No durable state lives in the container."""

import asyncio
import base64
import hashlib
import logging
import os
import re
import time
from collections import OrderedDict
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
        self.history_lock = asyncio.Lock()
        self.history_cache = OrderedDict()

    async def _request(
        self, method: str, url: str, *, allow_not_found=False, **kwargs
    ) -> httpx.Response:
        unavailable = (
            UNAVAILABLE
            if method in ("PUT", "DELETE")
            else "Archivio GitHub non disponibile. Riprova tra poco."
        )
        try:
            response = await self.client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            self.expires_at = 0
            raise HTTPException(503, unavailable) from exc
        if response.is_success or (allow_not_found and response.status_code == 404):
            return response
        self.expires_at = 0
        if response.status_code in (409, 422):
            raise HTTPException(409, CONFLICT)
        # Never expose GitHub response bodies, signed archive URLs, or credentials.
        logger.warning(
            "GitHub storage request failed: %s %s", method, response.status_code
        )
        raise HTTPException(503, unavailable)

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

    @staticmethod
    def _validate_revision(revision: str):
        if not re.fullmatch(r"[0-9a-f]{40}", revision):
            raise HTTPException(400, "Versione non valida")

    @staticmethod
    def _revision_info(item: dict) -> dict:
        author = item["commit"].get("author") or {}
        committer = item["commit"].get("committer") or {}
        return {
            "sha": item["sha"],
            "date": author.get("date") or committer.get("date"),
            "editor": author.get("name")
            or committer.get("name")
            or "Autore non disponibile",
        }

    async def history(
        self, key: str, page: int = 1, ref: str | None = None, per_page: int = 20
    ) -> dict:
        self._validate_key(key)
        if ref is not None:
            self._validate_revision(ref)
        if page < 1 or not 1 <= per_page <= 100:
            raise HTTPException(400, "Pagina non valida")
        cache_key = (key, page, ref, per_page)
        async with self.history_lock:
            cached = self.history_cache.get(cache_key)
            if cached and time.monotonic() < cached[0]:
                self.history_cache.move_to_end(cache_key)
                return cached[1]
            response = await self._request(
                "GET",
                f"{self.base}/commits",
                params={
                    "path": key,
                    "sha": ref or self.branch,
                    "page": page,
                    "per_page": per_page,
                },
            )
            entries = [self._revision_info(item) for item in response.json()]
            result = {
                "entries": entries,
                "page": page,
                # Anchor subsequent pages so new saves cannot shift entries between pages.
                "ref": ref or (entries[0]["sha"] if entries else None),
                "has_more": "next" in response.links,
            }
            self.history_cache[cache_key] = (
                time.monotonic() + self.cache_seconds,
                result,
            )
            self.history_cache.move_to_end(cache_key)
            while len(self.history_cache) > 128:
                self.history_cache.popitem(last=False)
            return result

    async def revision(self, key: str, revision: str) -> dict:
        self._validate_key(key)
        self._validate_revision(revision)
        commit = await self._request(
            "GET", f"{self.base}/commits/{revision}", allow_not_found=True
        )
        if commit.status_code == 404:
            raise HTTPException(404, "Versione non trovata")
        response = await self._request(
            "GET",
            f"{self.base}/contents/{quote(key, safe='')}",
            params={"ref": revision},
            allow_not_found=True,
        )
        result = {
            "revision": self._revision_info(commit.json()),
            "data": None,
            "version": None,
        }
        # A deletion is itself a historical event; do not confuse it with API downtime.
        if response.status_code != 404:
            content = response.json()
            result["data"] = base64.b64decode(content["content"])
            result["version"] = content["sha"]
        return result

    async def mutate(
        self,
        key: str,
        data: bytes | None,
        version: str | None,
        author: dict | None = None,
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
            if author is not None:
                payload["author"] = author
            try:
                response = await self._request(
                    "DELETE" if data is None else "PUT",
                    f"{self.base}/contents/{quote(key, safe='')}",
                    json=payload,
                )
            finally:
                # Also invalidate after ambiguous failures: GitHub may have committed.
                async with self.history_lock:
                    for cached_key in list(self.history_cache):
                        if cached_key[0] == key:
                            self.history_cache.pop(cached_key, None)
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


async def set_data(
    key: str, data: bytes, version: str | None = None, author: dict | None = None
) -> str:
    sha = await get_store().mutate(key, data, version, author)
    assert sha is not None
    return sha


async def delete_data(key: str, version: str, author: dict | None = None) -> None:
    await get_store().mutate(key, None, version, author)


async def recipe_history(
    key: str, page: int = 1, ref: str | None = None, per_page: int = 20
) -> dict:
    return await get_store().history(key, page, ref, per_page)


async def recipe_revision(key: str, revision: str) -> dict:
    return await get_store().revision(key, revision)


async def list_data() -> list[str]:
    return await get_store().list()

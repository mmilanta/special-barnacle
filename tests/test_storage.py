"""Offline integration tests: GitHub is simulated at the HTTP boundary."""

import asyncio
import base64
import hashlib
import json
import sys
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import httpx
from fastapi import HTTPException

from api import api
from app import app
from data import Recipe
from db import GitHubStore
from oauth import get_current_user

KEY = "recipe:sample.json"
ORIGINAL = json.dumps(
    {
        "title": "Torta",
        "category": "Dolci",
        "ingredients": "Farina",
        "steps": "Cuocere",
        "notes": "",
    }
).encode()


def blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


class FakeGitHub:
    def __init__(self):
        self.files = {KEY: ORIGINAL, "valid_categories.json": b'["Dolci"]'}
        self.calls = []
        self.fail_status = None
        self.timeout = False
        self.active_writes = 0
        self.max_active_writes = 0
        self.write_gate = None
        self.write_entered = asyncio.Event()

    async def __call__(self, request):
        self.calls.append(request)
        if "/zipball/" in request.url.path:
            return httpx.Response(
                302, headers={"Location": "https://codeload.github.com/archive"}
            )
        if request.url.host == "codeload.github.com":
            # httpx must strip the GitHub bearer token on cross-origin redirects.
            assert "authorization" not in request.headers
            buffer = BytesIO()
            with ZipFile(buffer, "w") as archive:
                for key, value in self.files.items():
                    archive.writestr("repo-snapshot/" + key, value)
                archive.writestr("repo-snapshot/nested/ignored.json", "{}")
            return httpx.Response(200, content=buffer.getvalue())
        if self.timeout:
            raise httpx.ReadTimeout("simulated timeout", request=request)
        if self.fail_status:
            return httpx.Response(
                self.fail_status, json={"message": "private upstream details"}
            )
        key = request.url.path.split("/contents/")[1]
        payload = json.loads(request.content)
        assert payload["branch"] == "recipes/live"
        existing = self.files.get(key)
        if payload.get("sha") != (blob_sha(existing) if existing else None):
            return httpx.Response(409)
        self.active_writes += 1
        self.max_active_writes = max(self.max_active_writes, self.active_writes)
        try:
            if self.write_gate:
                self.write_entered.set()
                await self.write_gate.wait()
            if request.method == "DELETE":
                self.files.pop(key)
                return httpx.Response(200, json={"content": None})
            data = base64.b64decode(payload["content"])
            self.files[key] = data
            return httpx.Response(
                200 if existing else 201, json={"content": {"sha": blob_sha(data)}}
            )
        finally:
            self.active_writes -= 1


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.github = FakeGitHub()
        self.http = httpx.AsyncClient(
            transport=httpx.MockTransport(self.github),
            headers={"Authorization": "Bearer test-token"},
        )
        self.store = GitHubStore(self.http, "owner/recipes", "recipes/live")
        self.store_patch = patch("db.get_store", return_value=self.store)
        self.store_patch.start()
        self.api_client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        self.auth(None)

    def auth(self, user):
        api.dependency_overrides[get_current_user] = lambda: user

    async def asyncTearDown(self):
        api.dependency_overrides.clear()
        self.store_patch.stop()
        await self.api_client.aclose()
        await self.http.aclose()

    async def test_snapshot_cache_and_expiry(self):
        data, sha = await self.store.get(KEY)
        self.assertEqual(data, ORIGINAL)
        self.assertEqual(sha, blob_sha(ORIGINAL))
        self.assertEqual(await self.store.list(), [KEY, "valid_categories.json"])
        self.assertEqual(len(self.github.calls), 2)  # API redirect + archive
        self.assertIn("recipes%2Flive", self.github.calls[0].url.raw_path.decode())
        self.github.files[KEY] = b"changed"
        self.store.expires_at = 0
        self.assertEqual((await self.store.get(KEY))[0], b"changed")
        self.assertEqual(len(self.github.calls), 4)

    async def test_model_round_trip_and_delete(self):
        recipe = await Recipe.load("sample")
        self.assertEqual(recipe.version, blob_sha(ORIGINAL))
        recipe.title = "Torta nuova"
        await recipe.save()
        persisted = json.loads(self.github.files[KEY])
        self.assertEqual(persisted["title"], "Torta nuova")
        self.assertNotIn("version", persisted)
        self.assertNotIn("id", persisted)
        self.assertEqual(recipe.version, blob_sha(self.github.files[KEY]))
        self.assertEqual((await Recipe.all())[0].title, "Torta nuova")
        await Recipe.delete("sample", recipe.version)
        self.assertNotIn(KEY, self.github.files)
        self.assertEqual(await Recipe.all(), [])

    async def test_create_has_no_sha_and_duplicate_is_rejected(self):
        self.assertEqual(
            await self.store.mutate("recipe:new.json", b"{}", None), blob_sha(b"{}")
        )
        self.assertNotIn("sha", json.loads(self.github.calls[-1].content))
        with self.assertRaises(HTTPException) as error:
            await self.store.mutate("recipe:new.json", b"different", None)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.github.files["recipe:new.json"], b"{}")

    async def test_stale_editor_cannot_overwrite_or_delete(self):
        original = await Recipe.load("sample")
        stale = await Recipe.load("sample")
        original.title = "First editor"
        await original.save()
        stale.title = "Second editor"
        with self.assertRaises(HTTPException) as error:
            await stale.save()
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPException) as error:
            await Recipe.delete("sample", stale.version)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(json.loads(self.github.files[KEY])["title"], "First editor")

    async def test_external_conflict_does_not_change_cache_and_invalidates_it(self):
        _, version = await self.store.get(KEY)
        self.github.files[KEY] = b"external edit"
        with self.assertRaises(HTTPException) as error:
            await self.store.mutate(KEY, b"overwrite", version)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.github.files[KEY], b"external edit")
        self.assertEqual((await self.store.get(KEY))[0], b"external edit")

    async def test_upstream_failures_never_confirm_save(self):
        for status in (401, 403, 404, 429, 500):
            with self.subTest(status=status):
                _, version = await self.store.get(KEY)
                self.github.fail_status = status
                with self.assertRaises(HTTPException) as error:
                    await self.store.mutate(KEY, b"unsaved", version)
                self.assertEqual(error.exception.status_code, 503)
                self.assertNotIn("private upstream details", error.exception.detail)
                self.assertEqual(self.store.files[KEY][0], ORIGINAL)
                self.assertEqual(self.store.expires_at, 0)
                self.github.fail_status = None
        _, version = await self.store.get(KEY)
        self.github.timeout = True
        with self.assertRaises(HTTPException) as error:
            await self.store.mutate(KEY, b"unsaved", version)
        self.assertEqual(error.exception.status_code, 503)
        self.assertEqual(self.github.files[KEY], ORIGINAL)

    async def test_writes_are_serialized_and_wait_for_github(self):
        self.github.write_gate = asyncio.Event()
        first = asyncio.create_task(self.store.mutate("recipe:first.json", b"{}", None))
        await self.github.write_entered.wait()
        second = asyncio.create_task(
            self.store.mutate("recipe:second.json", b"{}", None)
        )
        await asyncio.sleep(0)
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.github.write_gate.set()
        await asyncio.gather(first, second)
        self.assertEqual(self.github.max_active_writes, 1)
        self.assertIn("recipe:second.json", self.github.files)

    async def test_invalid_snapshot_returns_error_without_serving_expired_data(self):
        await self.store.get(KEY)
        self.store.expires_at = 0

        async def broken_archive(request):
            return httpx.Response(200, content=b"not a zip archive")

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(broken_archive)
        ) as client:
            self.store.client = client
            with self.assertRaises(HTTPException) as error:
                await self.store.get(KEY)
            self.assertEqual(error.exception.status_code, 503)
            self.assertEqual(self.store.expires_at, 0)

    async def test_shutdown_closes_shared_http_client(self):
        with patch("app.get_store", return_value=self.store) as factory:
            async with app.router.lifespan_context(app):
                self.assertFalse(self.http.is_closed)
            self.assertTrue(self.http.is_closed)
            factory.cache_clear.assert_called_once()

    async def test_missing_and_invalid_keys(self):
        for key in ("../secret.json", "nested/file.json", "file.txt"):
            with self.assertRaises(HTTPException) as error:
                await self.store.get(key)
            self.assertEqual(error.exception.status_code, 400)
        with self.assertRaises(HTTPException) as error:
            await self.store.get("missing.json")
        self.assertEqual(error.exception.status_code, 404)

    async def test_api_auth_versions_and_id_match(self):
        recipe = (await self.api_client.get("/api/v1/recipe/sample")).json()
        url = "/api/v1/recipe/sample"
        self.assertEqual((await self.api_client.put(url, json=recipe)).status_code, 401)
        self.auth({"is_superuser": False})
        self.assertEqual((await self.api_client.put(url, json=recipe)).status_code, 403)
        self.auth({"is_superuser": True})
        self.assertEqual(
            (await self.api_client.put(url + "-other", json=recipe)).status_code, 400
        )
        self.assertEqual((await self.api_client.delete(url)).status_code, 422)
        recipe["title"] = "Saved via API"
        saved = await self.api_client.put(url, json=recipe)
        self.assertEqual(saved.status_code, 200)
        self.assertEqual((await self.api_client.put(url, json=recipe)).status_code, 409)
        self.assertEqual(
            (
                await self.api_client.delete(url, params={"version": recipe["version"]})
            ).status_code,
            409,
        )
        self.assertEqual(
            (
                await self.api_client.delete(
                    url, params={"version": saved.json()["version"]}
                )
            ).status_code,
            200,
        )
        self.assertEqual((await self.api_client.get(url)).status_code, 404)

    async def test_api_failure_preserves_remote_recipe(self):
        recipe = (await self.api_client.get("/api/v1/recipe/sample")).json()
        self.auth({"is_superuser": True})
        self.github.fail_status = 500
        result = await self.api_client.put(
            "/api/v1/recipe/sample", json={**recipe, "title": "unsaved"}
        )
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self.github.files[KEY], ORIGINAL)

    async def test_notes_section_only_appears_with_content(self):
        for notes in (None, "", "  \n\t ", "**Conservare in frigo**"):
            with self.subTest(notes=notes):
                recipe = {**json.loads(ORIGINAL), "notes": notes}
                self.github.files[KEY] = json.dumps(recipe).encode()
                self.store.expires_at = 0
                response = await self.api_client.get("/recipe/sample")
                self.assertEqual(response.status_code, 200)
                if notes and notes.strip():
                    self.assertIn(">Note</p>", response.text)
                    self.assertIn("<strong>Conservare in frigo</strong>", response.text)
                else:
                    self.assertNotIn(">Note</p>", response.text)

    async def test_ui_renders_versions_and_preserves_forms_on_failure(self):
        for url, expected in (
            ("/recipe/sample/edit", '"version": "' + blob_sha(ORIGINAL)),
            ("/recipe/sample", "?version="),
            ("/create-recipe", "crypto.randomUUID()"),
        ):
            response = await self.api_client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn(expected, response.text)
        self.assertEqual((await self.api_client.get("/")).status_code, 200)
        self.assertEqual(
            (await self.api_client.get("/static/favicon.svg")).status_code, 200
        )
        for filename in ("recipe_edit.html", "create_recipe.html"):
            source = (
                Path(__file__).resolve().parents[1] / "src/templates" / filename
            ).read_text()
            self.assertNotIn("/error?status=", source)
            self.assertIn('prop("disabled", false)', source)


if __name__ == "__main__":
    unittest.main()

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
from ui import ui

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
        self.files = {
            KEY: ORIGINAL,
            "valid_categories.json": b'["Dolci"]',
            "superusers.json": b'["writer@example.test"]',
        }
        self.histories = {}
        self.revisions = {}
        self.commits = {}
        self.counter = 0
        self.record(
            KEY,
            ORIGINAL,
            {"name": "Servizio ricette", "email": "service@example.test"},
            "2026-11-01T11:00:00Z",
        )
        self.calls = []
        self.fail_status = None
        self.timeout = False
        self.active_writes = 0
        self.max_active_writes = 0
        self.write_gate = None
        self.write_entered = asyncio.Event()

    def record(self, key, data, author=None, date="2026-11-02T12:00:00Z"):
        self.counter += 1
        sha = f"{self.counter:040x}"
        identity = {
            **(author or {"name": "Servizio ricette", "email": "service@example.test"}),
            "date": date,
        }
        commit = {"sha": sha, "commit": {"author": identity, "committer": identity}}
        self.histories.setdefault(key, []).insert(0, commit)
        self.revisions[key, sha] = data
        self.commits[sha] = commit
        if data is None:
            self.files.pop(key, None)
        else:
            self.files[key] = data
        return sha

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
        if request.url.path.endswith("/commits"):
            key = request.url.params["path"]
            items = self.histories.get(key, [])
            ref = request.url.params["sha"]
            if ref != "recipes/live":
                items = items[
                    next(
                        (i for i, item in enumerate(items) if item["sha"] == ref),
                        len(items),
                    ) :
                ]
            page = int(request.url.params["page"])
            size = int(request.url.params["per_page"])
            headers = {}
            if len(items) > page * size:
                headers["Link"] = (
                    f'<https://api.github.com/repos/owner/recipes/commits?page={page + 1}>; rel="next"'
                )
            return httpx.Response(
                200, json=items[(page - 1) * size : page * size], headers=headers
            )
        if "/commits/" in request.url.path:
            item = self.commits.get(request.url.path.rsplit("/", 1)[1])
            return httpx.Response(200, json=item) if item else httpx.Response(404)
        key = request.url.path.split("/contents/")[1]
        if request.method == "GET":
            data = self.revisions.get((key, request.url.params["ref"]))
            if data is None:
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "content": base64.b64encode(data).decode(),
                    "sha": blob_sha(data),
                },
            )
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
                self.record(key, None, payload.get("author"))
                return httpx.Response(200, json={"content": None})
            data = base64.b64decode(payload["content"])
            self.record(key, data, payload.get("author"))
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
        if user is not None:
            user = {
                "name": "Writer",
                "email": "writer@example.test"
                if user.get("is_superuser")
                else "reader@example.test",
                **user,
            }
        api.dependency_overrides[get_current_user] = lambda: user
        ui.dependency_overrides[get_current_user] = lambda: user

    async def asyncTearDown(self):
        api.dependency_overrides.clear()
        ui.dependency_overrides.clear()
        self.store_patch.stop()
        await self.api_client.aclose()
        await self.http.aclose()

    async def test_snapshot_cache_and_expiry(self):
        data, sha = await self.store.get(KEY)
        self.assertEqual(data, ORIGINAL)
        self.assertEqual(sha, blob_sha(ORIGINAL))
        self.assertEqual(
            await self.store.list(), [KEY, "superusers.json", "valid_categories.json"]
        )
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

    async def test_public_date_is_small_and_does_not_expose_history(self):
        response = await self.api_client.get("/recipe/sample")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Ultima modifica", response.text)
        self.assertLess(response.text.index("Cuocere"), response.text.index("<time"))
        self.assertLess(
            response.text.index("</time>"),
            response.text.index('<div class="page-actions recipe-actions">'),
        )
        self.assertIn("01/11/2026", response.text)
        self.assertNotIn("Servizio ricette", response.text)
        self.assertNotIn("service@example.test", response.text)
        self.assertNotIn("Cronologia</a>", response.text)
        self.assertIn("font-serif text-sm", response.text)
        count = len(self.github.calls)
        await self.api_client.get("/recipe/sample")
        self.assertEqual(len(self.github.calls), count)

    async def test_history_and_versions_require_current_writer_whitelist(self):
        sha = self.github.histories[KEY][0]["sha"]
        for user, expected in (
            (None, 401),
            ({"is_superuser": False}, 403),
            ({"is_superuser": True, "email": "removed@example.test"}, 403),
        ):
            self.auth(user)
            for path in ("/recipe/sample/history", f"/recipe/sample/history/{sha}"):
                with self.subTest(user=user, path=path):
                    self.assertEqual(
                        (await self.api_client.get(path)).status_code, expected
                    )
        self.auth({"is_superuser": True})
        response = await self.api_client.get("/recipe/sample")
        self.assertIn("Cronologia</a>", response.text)
        self.assertEqual(
            (await self.api_client.get("/recipe/sample/history")).status_code, 200
        )
        # Revocation takes effect without requiring the user to log out.
        self.github.files["superusers.json"] = b"[]"
        self.store.expires_at = 0
        self.assertEqual(
            (await self.api_client.get("/recipe/sample/history")).status_code, 403
        )
        self.assertEqual(
            (await self.api_client.get(f"/recipe/sample/history/{sha}")).status_code,
            403,
        )

    async def test_history_paginates_all_versions_with_stable_anchor(self):
        for number in range(25):
            self.github.record(
                KEY,
                json.dumps(
                    {**json.loads(ORIGINAL), "title": f"Torta {number}"}
                ).encode(),
            )
        self.auth({"is_superuser": True})
        first = await self.store.history(KEY)
        self.assertEqual(len(first["entries"]), 20)
        self.assertTrue(first["has_more"])
        self.assertEqual(first["ref"], first["entries"][0]["sha"])
        first_shas = {entry["sha"] for entry in first["entries"]}
        self.github.record(KEY, ORIGINAL)  # A new edit must not shift anchored pages.
        second = await self.store.history(KEY, page=2, ref=first["ref"])
        self.assertEqual(len(second["entries"]), 6)
        self.assertFalse(second["has_more"])
        self.assertFalse(
            first_shas.intersection(entry["sha"] for entry in second["entries"])
        )
        response = await self.api_client.get("/recipe/sample/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Meno recenti", response.text)
        self.assertIn("ref=" + first["ref"], response.text)
        next_page = await self.api_client.get(
            "/recipe/sample/history", params={"page": 2, "ref": first["ref"]}
        )
        self.assertIn("Più recenti", next_page.text)
        self.assertNotIn("Meno recenti", next_page.text)
        query = next(
            request.url.params
            for request in self.github.calls
            if request.url.path.endswith("/commits")
            and request.url.params["page"] == "2"
        )
        self.assertEqual(query["path"], KEY)
        self.assertEqual(query["sha"], first["ref"])

    async def test_historical_links_show_original_content_read_only(self):
        original_sha = self.github.histories[KEY][0]["sha"]
        self.github.record(
            KEY,
            json.dumps({**json.loads(ORIGINAL), "title": "Nuova torta"}).encode(),
            {"name": "Mamma", "email": "writer@example.test"},
        )
        self.auth({"is_superuser": True})
        listing = await self.api_client.get("/recipe/sample/history")
        self.assertIn("Mamma", listing.text)
        self.assertIn("Servizio ricette", listing.text)
        self.assertIn("02/11/2026", listing.text)
        self.assertIn("01/11/2026", listing.text)
        self.assertIn("/recipe/sample/history/" + original_sha, listing.text)
        self.assertNotIn("writer@example.test", listing.text)
        snapshot = await self.api_client.get("/recipe/sample/history/" + original_sha)
        self.assertEqual(snapshot.status_code, 200)
        self.assertIn("Torta", snapshot.text)
        self.assertNotIn("Nuova torta", snapshot.text)
        self.assertIn("sola lettura", snapshot.text)
        self.assertNotIn("delete_recipe()", snapshot.text)
        self.assertNotIn("go_to_edit()", snapshot.text)
        self.assertEqual(snapshot.headers["Cache-Control"], "private, no-store")
        self.assertEqual(listing.headers["Cache-Control"], "private, no-store")
        self.assertFalse(any(request.method != "GET" for request in self.github.calls))

    async def test_deletions_and_missing_revisions_are_distinguished(self):
        original_sha = self.github.histories[KEY][0]["sha"]
        deleted_sha = self.github.record(KEY, None)
        self.auth({"is_superuser": True})
        listing = await self.api_client.get("/recipe/sample/history")
        self.assertEqual(listing.status_code, 200)
        deleted = await self.api_client.get("/recipe/sample/history/" + deleted_sha)
        self.assertEqual(deleted.status_code, 200)
        self.assertIn("eliminata in questa versione", deleted.text)
        self.assertEqual(
            (
                await self.api_client.get("/recipe/sample/history/" + original_sha)
            ).status_code,
            200,
        )
        self.assertEqual(
            (
                await self.api_client.get("/recipe/sample/history/" + "f" * 40)
            ).status_code,
            404,
        )
        self.assertEqual(
            (await self.api_client.get("/recipe/sample/history/not-a-sha")).status_code,
            422,
        )
        self.assertEqual(
            (await self.api_client.get("/recipe/sample/history?page=0")).status_code,
            422,
        )

    async def test_api_records_editor_and_refreshes_last_edited(self):
        self.auth({"is_superuser": True, "name": "Mamma"})
        initial = await self.api_client.get("/recipe/sample")
        self.assertIn("01/11/2026", initial.text)
        payload = (await self.api_client.get("/api/v1/recipe/sample")).json()
        result = await self.api_client.put(
            "/api/v1/recipe/sample", json={**payload, "title": "Nuovo titolo"}
        )
        self.assertEqual(result.status_code, 200)
        request = next(
            request
            for request in reversed(self.github.calls)
            if request.method == "PUT"
        )
        self.assertEqual(
            json.loads(request.content)["author"],
            {"name": "Mamma", "email": "writer@example.test"},
        )
        self.assertIn("02/11/2026", (await self.api_client.get("/recipe/sample")).text)
        response = await self.api_client.get("/recipe/sample/history")
        self.assertIn("Mamma", response.text)
        self.assertNotIn("writer@example.test", response.text)
        version = result.json()["version"]
        self.assertEqual(
            (
                await self.api_client.delete(
                    "/api/v1/recipe/sample", params={"version": version}
                )
            ).status_code,
            200,
        )
        request = next(
            request
            for request in reversed(self.github.calls)
            if request.method == "DELETE"
        )
        self.assertEqual(json.loads(request.content)["author"]["name"], "Mamma")

    async def test_history_outage_does_not_break_public_recipe(self):
        await self.store.get(KEY)
        self.github.fail_status = 500
        response = await self.api_client.get("/recipe/sample")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<time", response.text)
        self.assertIn("Ingredienti", response.text)
        self.github.fail_status = None
        self.auth({"is_superuser": True})
        await self.store.get("superusers.json")
        self.github.fail_status = 500
        response = await self.api_client.get("/recipe/sample/history")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private upstream details", response.text)

    async def test_history_is_filtered_to_one_recipe(self):
        other_sha = self.github.record(
            "recipe:other.json",
            ORIGINAL,
            {"name": "Other recipe author", "email": "other@example.test"},
        )
        self.auth({"is_superuser": True})
        listing = await self.api_client.get("/recipe/sample/history")
        self.assertNotIn("Other recipe author", listing.text)
        self.assertNotIn(other_sha, listing.text)

    async def test_diff_compares_with_previous_and_current_versions(self):
        first_sha = self.github.histories[KEY][0]["sha"]
        middle_sha = self.github.record(
            KEY,
            json.dumps(
                {**json.loads(ORIGINAL), "ingredients": "200 g farina"}
            ).encode(),
            {"name": "Marco", "email": "marco@example.test"},
        )
        latest_sha = self.github.record(
            KEY,
            json.dumps(
                {**json.loads(ORIGINAL), "ingredients": "300 g farina"}
            ).encode(),
            {"name": "Mamma", "email": "writer@example.test"},
        )
        self.auth({"is_superuser": True})
        previous = await self.api_client.get(
            f"/recipe/sample/history/{middle_sha}/diff?compare=previous"
        )
        self.assertEqual(previous.status_code, 200)
        self.assertIn("<del>Farina</del>", previous.text)
        self.assertIn("<ins>200 g farina</ins>", previous.text)
        self.assertNotIn("300 g farina", previous.text)
        self.assertIn("Marco", previous.text)
        current = await self.api_client.get(
            f"/recipe/sample/history/{middle_sha}/diff?compare=current"
        )
        self.assertEqual(current.status_code, 200)
        self.assertIn("<del>200 g farina</del>", current.text)
        self.assertIn("<ins>300 g farina</ins>", current.text)
        self.assertIn("Mamma", current.text)
        self.assertEqual(current.headers["Cache-Control"], "private, no-store")
        listing = await self.api_client.get("/recipe/sample/history")
        self.assertNotIn(f"/history/{latest_sha}/diff?compare=current", listing.text)
        self.assertIn(f"/history/{middle_sha}/diff?compare=current", listing.text)
        self.assertIn(f"/history/{first_sha}/diff?compare=current", listing.text)
        self.assertIn(f"/history/{latest_sha}/diff?compare=previous", listing.text)
        self.assertIn(f"/history/{middle_sha}/diff?compare=previous", listing.text)
        self.assertNotIn(f"/history/{first_sha}/diff?compare=previous", listing.text)
        self.assertFalse(any(request.method != "GET" for request in self.github.calls))

    async def test_diff_shows_unchanged_fields_once_and_short_history_links(self):
        old_sha = self.github.histories[KEY][0]["sha"]
        self.github.record(
            KEY,
            json.dumps(
                {**json.loads(ORIGINAL), "notes": "Conservare in frigo"}
            ).encode(),
        )
        self.auth({"is_superuser": True})
        response = await self.api_client.get(
            f"/recipe/sample/history/{old_sha}/diff?compare=current"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text.count("Farina"), 1)
        self.assertEqual(response.text.count("Cuocere"), 1)
        self.assertEqual(response.text.count("Dolci"), 1)
        for label in ("Titolo", "Categoria", "Ingredienti", "Ricetta", "Note"):
            self.assertIn(f">{label}</h2>", response.text)
        self.assertIn("<ins>Conservare in frigo</ins>", response.text)
        self.assertNotIn("Testo rimosso", response.text)
        self.assertNotIn("Testo aggiunto", response.text)
        listing = await self.api_client.get("/recipe/sample/history")
        self.assertEqual(listing.text.count(">apri</a>"), 2)
        self.assertEqual(listing.text.count(">prec</a>"), 1)
        self.assertEqual(listing.text.count(">curr</a>"), 1)

    async def test_diff_handles_creation_deletion_and_identical_content(self):
        first_sha = self.github.histories[KEY][0]["sha"]
        self.auth({"is_superuser": True})
        created = await self.api_client.get(
            f"/recipe/sample/history/{first_sha}/diff?compare=previous"
        )
        self.assertEqual(created.status_code, 200)
        self.assertIn("Prima della creazione", created.text)
        self.assertIn("<ins>Farina</ins>", created.text)
        deleted_sha = self.github.record(KEY, None)
        self.store.history_cache.clear()
        deleted = await self.api_client.get(
            f"/recipe/sample/history/{deleted_sha}/diff?compare=previous"
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertIn("<del>Farina</del>", deleted.text)
        self.assertNotIn("<ins>Farina</ins>", deleted.text)
        same = await self.api_client.get(
            f"/recipe/sample/history/{deleted_sha}/diff?compare=current"
        )
        self.assertEqual(same.status_code, 200)
        self.assertIn("Nessuna differenza", same.text)

    async def test_diff_access_validation_and_html_escaping(self):
        first_sha = self.github.histories[KEY][0]["sha"]
        for user, expected in (
            (None, 401),
            ({"is_superuser": False}, 403),
            ({"is_superuser": True, "email": "revoked@example.test"}, 403),
        ):
            self.auth(user)
            response = await self.api_client.get(
                f"/recipe/sample/history/{first_sha}/diff"
            )
            self.assertEqual(response.status_code, expected)
        self.auth({"is_superuser": True})
        self.assertEqual(
            (
                await self.api_client.get(
                    f"/recipe/sample/history/{first_sha}/diff?compare=invalid"
                )
            ).status_code,
            422,
        )
        self.assertEqual(
            (
                await self.api_client.get(
                    "/recipe/sample/history/" + "f" * 40 + "/diff"
                )
            ).status_code,
            404,
        )
        other_sha = self.github.record("recipe:other.json", ORIGINAL)
        self.assertEqual(
            (
                await self.api_client.get(f"/recipe/sample/history/{other_sha}/diff")
            ).status_code,
            404,
        )
        html_sha = self.github.record(
            KEY,
            json.dumps(
                {**json.loads(ORIGINAL), "notes": '<script>alert("test")</script>'}
            ).encode(),
        )
        response = await self.api_client.get(f"/recipe/sample/history/{html_sha}/diff")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("<script>", response.text)
        self.assertIn("&lt;script&gt;", response.text)

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

"""HTTP/auth/CSRF/multipart tests with a fake catalog; no production connections."""
from contextlib import contextmanager
from html.parser import HTMLParser
from io import BytesIO
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock
import uuid
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# These modules normally load deployment settings; never read dotenv in offline tests.
with patch("dotenv.load_dotenv", return_value=False):
    from admin import minecraft_cosmetics as admin

from check_minecraft_cosmetics import png, geometry
from bot.services.minecraft_cosmetics import asset, json_bytes, MAX_UPLOAD, MAX_ID


class FakeRepository:
    added = []
    deleted = set()
    def __init__(self, connection): pass
    def assets(self): return list(self.added)
    def deleted_assets(self): return set(self.deleted)
    def servers(self): return []
    def revision(self): return 1
    def add(self, **kwargs):
        kind = kwargs.pop("kind"); kwargs.pop("created_by")
        entry = asset(kind, 5 if kind == "skin" else 1, "test_asset", **kwargs)
        self.added.append(entry)
        return entry
    def delete_asset(self, kind, asset_id, deleted_by, *, builtin_ids=()):
        if kind not in ("skin", "poster"):
            raise ValueError("削除できるのはスキンとポスターだけです。")
        if type(asset_id) is not int or not 1 <= asset_id <= MAX_ID:
            raise ValueError("素材IDが不正です。")
        if asset_id not in builtin_ids and not any(entry["kind"] == kind and entry["id"] == asset_id for entry in self.added):
            raise ValueError("削除できる素材がありません。")
        self.deleted.add((kind, asset_id))
        self.added[:] = [entry for entry in self.added if not (entry["kind"] == kind and entry["id"] == asset_id)]
        return True


class Connection:
    def commit(self): pass


@contextmanager
def fake_connection():
    yield Connection()


def require_login(request):
    user = request.session.get("discord_user")
    if not user: raise HTTPException(401)
    return user


class FakePermission:
    def __init__(self, connection): pass
    def has_global_admin(self, user_id): return user_id == "admin"


app = FastAPI()
app.add_middleware(SessionMiddleware, secret_key="offline-test-session-only")


@app.get("/test-login/{user}")
def login(request: Request, user: str):
    request.session["discord_user"] = {"user_id": user}
    request.session["cosmetics_csrf"] = "test-csrf"
    return {"ok": True}


admin.register_minecraft_cosmetics_routes(Jinja2Templates(directory=str(ROOT / "admin/templates")))
app.include_router(admin.router)
# layout.html uses the named static route.
from starlette.staticfiles import StaticFiles
app.mount("/static", StaticFiles(directory=str(ROOT / "admin/static")), name="static")


class AdminChecks(unittest.TestCase):
    def setUp(self):
        FakeRepository.added = []; FakeRepository.deleted = set()
        self.patches = [patch.object(admin, "get_connection", fake_connection),
                        patch.object(admin, "MinecraftCosmeticsRepository", FakeRepository), patch.object(admin, "require_login", require_login)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def sign_in(self, user="admin"):
        self.client.get("/test-login/" + user)

    def test_anonymous_requires_login_but_any_logged_in_user_can_access(self):
        self.assertEqual(self.client.get("/minecraft/cosmetics").status_code, 401)
        self.assertEqual(self.client.post("/minecraft/cosmetics/assets/poster/1/delete", data={"csrf": "test-csrf"}).status_code, 401)
        self.sign_in("viewer")
        self.assertEqual(self.client.get("/minecraft/cosmetics").status_code, 200)
        self.assertNotEqual(self.client.get("/minecraft/cosmetics/application").status_code, 403)

    def test_csrf_required_for_every_mutation(self):
        self.sign_in()
        for path in ("/assets", "/export", "/assets/skin/1/delete", "/assets/poster/1/delete", "/apply"):
            for token in ("", "wrong"):
                response = self.client.post("/minecraft/cosmetics" + path, data={"csrf": token})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(FakeRepository.added)
        self.assertFalse(FakeRepository.deleted)

    def test_registration_skin_and_preview(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets", data={"csrf": "test-csrf", "kind": "skin", "name": "追加スキン", "model": "slim"},
                                    files={"texture": ("../../anything.png", png(), "image/png")}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(FakeRepository.added[0]["model"], "slim")
        preview = self.client.get("/minecraft/cosmetics/preview/skin/5")
        self.assertEqual(preview.status_code, 200); self.assertEqual(preview.headers["content-type"], "image/png")

    def test_accessory_addition_is_rejected_but_existing_assets_remain(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets", data={"csrf": "test-csrf", "kind": "accessory", "name": "帽子", "slot": "hat"},
            files={"texture": ("t.png", png()), "geometry": ("a.json", json_bytes(geometry())), "icon": ("i.png", png((16, 16)))}, follow_redirects=False)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(FakeRepository.added)
        FakeRepository.added.append(asset("accessory", 1, "hat", "Existing hat", png(),
                                          geometry=json_bytes(geometry()), icon=png(), slot="hat"))
        page = self.client.get("/minecraft/cosmetics")
        self.assertIn("Existing hat", page.text)
        self.assertNotIn('name="kind" value="accessory"', page.text)
        self.assertEqual(self.client.get("/minecraft/cosmetics/preview/accessory/1").status_code, 200)
        self.assertEqual(self.client.post("/minecraft/cosmetics/export", data={"csrf": "test-csrf"}).status_code, 200)

    def test_poster_upload_preview_and_dimensions(self):
        from io import BytesIO
        from PIL import Image
        self.sign_in()
        fields = {"csrf": "test-csrf", "kind": "poster", "name": "New poster", "width": "3", "height": "2"}
        response = self.client.post("/minecraft/cosmetics/assets", data=fields,
                                    files={"texture": ("photo.png", png((300, 300)))}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        record = FakeRepository.added[0]
        self.assertEqual((record["width"], record["height"]), (3, 2))
        preview = self.client.get("/minecraft/cosmetics/preview/poster/1")
        self.assertEqual(Image.open(BytesIO(preview.content)).size, (192, 128))
        self.assertIn("3×2ブロック", self.client.get("/minecraft/cosmetics").text)
        for field in ("width", "height"):
            for value in ("", "0", "11", "1.5", "3.0", "True", "-1", "1e1", "３"):
                with self.subTest(field=field, value=value):
                    response = self.client.post("/minecraft/cosmetics/assets", data=fields | {field: value},
                                                files={"texture": ("photo.png", png())})
                    self.assertEqual(response.status_code, 400)
        self.assertEqual(len(FakeRepository.added), 1)

    def test_poster_delete_hides_preview_catalog_and_export_apply_assets(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets",
            data={"csrf": "test-csrf", "kind": "poster", "name": "Delete this poster", "width": "3", "height": "2"},
            files={"texture": ("photo.png", png())}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        FakeRepository.added.append(asset("poster", 2, "poster_2", "Keep this poster", png(), width=1, height=1))
        repo = FakeRepository(None)
        before = admin.catalog_digest(admin.records(repo))
        self.assertEqual(self.client.get("/minecraft/cosmetics/preview/poster/1").status_code, 200)
        response = self.client.post("/minecraft/cosmetics/assets/poster/1/delete",
                                    data={"csrf": "test-csrf"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(repo.deleted_assets(), {("poster", 1)})
        self.assertEqual([entry["id"] for entry in repo.assets()], [2])
        page = self.client.get("/minecraft/cosmetics")
        self.assertNotIn("Delete this poster", page.text)
        self.assertIn("Keep this poster", page.text)
        self.assertEqual(self.client.get("/minecraft/cosmetics/preview/poster/1").status_code, 404)
        self.assertEqual(self.client.get("/minecraft/cosmetics/preview/poster/2").status_code, 200)
        self.assertNotEqual(before, admin.catalog_digest(admin.records(repo)))
        exported = self.client.post("/minecraft/cosmetics/export", data={"csrf": "test-csrf"})
        self.assertEqual(exported.status_code, 200)
        with patch.object(admin, "cosmetics_control", AsyncMock(return_value={"status": "idle"})) as api:
            response = self.client.post("/minecraft/cosmetics/apply",
                data={"csrf": "test-csrf", "operation_id": str(uuid.uuid4())}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            applied = api.call_args.args[1]
        for data in (exported.content, applied):
            with ZipFile(BytesIO(data)) as archive:
                names = archive.namelist()
                self.assertFalse(any("poster_managed_1" in name for name in names))
                self.assertTrue(any("poster_managed_2" in name for name in names))
                catalog = json.loads(archive.read("cosmetics/catalog.lock.json"))
                self.assertEqual([p["baseId"] for p in catalog["posters"]], ["ichiyon:poster_managed_2"])
                for name in names:
                    if name.endswith((".json", ".js", ".lang")):
                        self.assertNotIn(b"poster_managed_1", archive.read(name), name)
                self.assertIn("behavior_packs/ichiyon_avatar_bp/blocks/poster_raio_r0c0.json", names)

    def test_delete_buttons_confirm_names_and_accessory_rejection(self):
        class Forms(HTMLParser):
            def __init__(self):
                super().__init__()
                self.forms = {}
            def handle_starttag(self, tag, attrs):
                attributes = dict(attrs)
                if tag == "form":
                    self.forms[attributes.get("action")] = attributes
        self.sign_in()
        name = "Poster '\" <>&"
        FakeRepository.added.extend([
            asset("poster", 1, "poster_1", name, png(), width=1, height=1),
            asset("accessory", 1, "hat", "Keep hat", png(), geometry=json_bytes(geometry()), icon=png(), slot="hat"),
        ])
        page = self.client.get("/minecraft/cosmetics")
        forms = Forms()
        forms.feed(page.text)
        form = forms.forms["/minecraft/cosmetics/assets/poster/1/delete"]
        self.assertEqual(form["onsubmit"], "return confirm(this.dataset.confirm);")
        self.assertEqual(form["data-confirm"], name + " を削除します。このIDは再利用されず、ゲーム反映後は一覧から消えます。よろしいですか？")
        self.assertIn("ポスターを削除", page.text)
        self.assertIn("スキンを削除", page.text)
        self.assertIn("/minecraft/cosmetics/assets/skin/1/delete", forms.forms)
        self.assertNotIn("/minecraft/cosmetics/assets/accessory/1/delete", forms.forms)
        for kind in ("accessory", "unknown"):
            response = self.client.post(f"/minecraft/cosmetics/assets/{kind}/1/delete", data={"csrf": "test-csrf"})
            self.assertEqual(response.status_code, 400)
            self.assertIn("スキンとポスターだけ", response.text)
        self.assertEqual(len(FakeRepository.added), 2)
        self.assertFalse(FakeRepository.deleted)

    def test_missing_poster_cannot_claim_skin_builtin_id(self):
        self.sign_in()
        for identifier in (1, 4, 99, 0):
            response = self.client.post(f"/minecraft/cosmetics/assets/poster/{identifier}/delete",
                data={"csrf": "test-csrf", "builtin_ids": str(identifier)})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(FakeRepository.deleted)

    def test_builtin_poster_uses_exact_kind_tombstone(self):
        self.sign_in()
        builtin = asset("poster", 1, "builtin_poster", "Builtin poster", png(), width=1, height=1)
        builtins = admin.builtin_assets(admin.ROOT) + [builtin]
        with patch.object(admin, "builtin_assets", return_value=builtins):
            before = admin.catalog_digest(admin.records(FakeRepository(None)))
            self.assertEqual(self.client.get("/minecraft/cosmetics/preview/poster/1").status_code, 200)
            response = self.client.post("/minecraft/cosmetics/assets/poster/1/delete",
                                        data={"csrf": "test-csrf"}, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(FakeRepository.deleted, {("poster", 1)})
            self.assertEqual(self.client.get("/minecraft/cosmetics/preview/poster/1").status_code, 404)
            self.assertEqual(self.client.get("/minecraft/cosmetics/preview/skin/1").status_code, 200)
            remaining = admin.records(FakeRepository(None))
            self.assertNotIn(builtin, remaining)
            self.assertNotEqual(before, admin.catalog_digest(remaining))
            self.assertNotIn("Builtin poster", self.client.get("/minecraft/cosmetics").text)

    def test_invalid_upload_returns_readable_error(self):
        self.sign_in()
        for files, data in (({"texture": ("x.png", b"not png")}, {}), ({}, {"texture": "string"}), ({"texture": ("x.png", png((32, 32)))}, {})):
            response = self.client.post("/minecraft/cosmetics/assets", data={"csrf": "test-csrf", "kind": "skin", "name": "bad", **data}, files=files)
            self.assertEqual(response.status_code, 400); self.assertIn('role="alert"', response.text)
        self.assertFalse(FakeRepository.added)

    def test_large_chunked_body_bounded(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets", content=iter([b"x" * MAX_UPLOAD] * 4), headers={"Content-Type": "multipart/form-data; boundary=test"})
        self.assertEqual(response.status_code, 413); self.assertFalse(FakeRepository.added)

    def test_name_escaped_in_catalog(self):
        self.sign_in(); FakeRepository.added.append(asset("skin", 5, "test_asset", "<script>alert(1)</script>", png()))
        response = self.client.get("/minecraft/cosmetics")
        self.assertEqual(response.status_code, 200); self.assertNotIn("<script>alert", response.text); self.assertIn("&lt;script&gt;", response.text)

    def test_export_contains_complete_packs_and_is_not_a_deploy(self):
        self.sign_in(); response = self.client.post("/minecraft/cosmetics/export", data={"csrf": "test-csrf"})
        self.assertEqual(response.status_code, 200); self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertTrue(response.content.startswith(b"PK"))

    def test_apply_generates_archive_and_retry_observes_same_operation(self):
        import uuid
        self.sign_in(); identifier = str(uuid.uuid4())
        fields = {'csrf':'test-csrf', 'operation_id':identifier}
        with patch.object(admin, 'cosmetics_control', AsyncMock(return_value={'status':'idle'})) as api:
            response = self.client.post('/minecraft/cosmetics/apply', data=fields, follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            self.assertEqual(api.call_args.args[0], identifier)
            self.assertTrue(api.call_args.args[1].startswith(b'PK'))
        with patch.object(admin, 'cosmetics_control', AsyncMock(return_value={'operation_id':identifier})) as api:
            self.assertEqual(self.client.post('/minecraft/cosmetics/apply', data=fields, follow_redirects=False).status_code, 303)
            self.assertEqual(api.call_count, 1)

    def test_apply_remote_failure_is_displayed_without_transport_details(self):
        import uuid
        self.sign_in()
        with patch.object(admin, 'cosmetics_control', AsyncMock(side_effect=admin.MinecraftControlError('接続できません。'))):
            response = self.client.post('/minecraft/cosmetics/apply', data={'csrf':'test-csrf', 'operation_id':str(uuid.uuid4())})
            self.assertEqual(response.status_code, 400); self.assertIn('接続できません。', response.text)

    def test_web_placement_route_and_form_are_removed(self):
        self.sign_in()
        page = self.client.get("/minecraft/cosmetics")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("マネキンを配置", page.text)
        self.assertNotIn('/minecraft/cosmetics/place', page.text)
        self.assertEqual(self.client.post("/minecraft/cosmetics/place", data={"csrf": "test-csrf"}).status_code, 404)

    def test_skin_delete_requires_admin_csrf_and_hides_builtin(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets/skin/1/delete", data={"csrf": "test-csrf"}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertIn(("skin", 1), FakeRepository.deleted)
        page = self.client.get("/minecraft/cosmetics")
        self.assertNotIn("キアナ", page.text)
        self.assertIn("スキンを削除", page.text)


if __name__ == "__main__": unittest.main()

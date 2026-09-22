"""HTTP/auth/CSRF/multipart tests with a fake catalog; no production connections."""
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch, AsyncMock

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
from bot.services.minecraft_cosmetics import asset, json_bytes, MAX_UPLOAD


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
    def delete_skin(self, asset_id, deleted_by):
        self.deleted.add(("skin", asset_id))
        self.added = [entry for entry in self.added if not (entry["kind"] == "skin" and entry["id"] == asset_id)]
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
        self.patches = [patch.object(admin, "get_connection", fake_connection), patch.object(admin, "PermissionRepository", FakePermission),
                        patch.object(admin, "MinecraftCosmeticsRepository", FakeRepository), patch.object(admin, "require_login", require_login)]
        for item in self.patches: item.start(); self.addCleanup(item.stop)
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def sign_in(self, user="admin"):
        self.client.get("/test-login/" + user)

    def test_anonymous_and_non_admin_cannot_read_or_mutate(self):
        for user, expected in ((None, 401), ("viewer", 403)):
            if user: self.sign_in(user)
            for method, path in (("get", ""), ("get", "/preview/skin/1"), ("get", "/application"), ("post", "/apply"), ("post", "/assets"), ("post", "/export"), ("post", "/assets/skin/1/delete")):
                with self.subTest(user=user, path=path): self.assertEqual(getattr(self.client, method)("/minecraft/cosmetics" + path).status_code, expected)

    def test_csrf_required_for_every_mutation(self):
        self.sign_in()
        for path in ("/assets", "/export", "/assets/skin/1/delete", "/apply"):
            for token in ("", "wrong"):
                response = self.client.post("/minecraft/cosmetics" + path, data={"csrf": token})
                self.assertEqual(response.status_code, 403)
        self.assertFalse(FakeRepository.added)

    def test_registration_skin_and_preview(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets", data={"csrf": "test-csrf", "kind": "skin", "name": "追加スキン", "model": "slim"},
                                    files={"texture": ("../../anything.png", png(), "image/png")}, follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertEqual(FakeRepository.added[0]["model"], "slim")
        preview = self.client.get("/minecraft/cosmetics/preview/skin/5")
        self.assertEqual(preview.status_code, 200); self.assertEqual(preview.headers["content-type"], "image/png")

    def test_registration_accessory(self):
        self.sign_in()
        response = self.client.post("/minecraft/cosmetics/assets", data={"csrf": "test-csrf", "kind": "accessory", "name": "帽子", "slot": "hat"},
            files={"texture": ("t.png", png()), "geometry": ("a.json", json_bytes(geometry())), "icon": ("i.png", png((16, 16)))}, follow_redirects=False)
        self.assertEqual(response.status_code, 303); self.assertEqual(FakeRepository.added[0]["slot"], "hat")

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

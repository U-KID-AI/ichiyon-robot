"""Real PostgreSQL tests, fixed localhost disposable test DB only. Used by CI."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import uuid

import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
with patch("dotenv.load_dotenv", return_value=False):
    from bot.repositories.minecraft_cosmetics import MinecraftCosmeticsRepository
    from admin import minecraft_internal
from check_minecraft_cosmetics import png, geometry
from bot.services.minecraft_cosmetics import json_bytes
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient


def connection():
    # Never uses DATABASE_URL, config, .env or any production credential.
    return psycopg.connect(host="127.0.0.1", port=5432, dbname="cosmetics_test", user="cosmetics_test", password="cosmetics_test", connect_timeout=5)


class DatabaseChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = "cosmetics_" + uuid.uuid4().hex
        with connection() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(cls.schema)))
            migration = (ROOT / "migrations/065_add_minecraft_cosmetics.sql").read_text(encoding="utf-8")
            conn.execute(migration)
            conn.execute(migration)  # Migration is additive/idempotent.

    @classmethod
    def tearDownClass(cls):
        with connection() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema)))

    @contextmanager
    def connect(self):
        with connection() as conn:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            yield conn

    def setUp(self):
        with self.connect() as conn:
            conn.execute("TRUNCATE minecraft_cosmetic_assets,minecraft_cosmetic_servers,minecraft_cosmetic_placements")

    def test_registration_is_durable_and_ids_do_not_race(self):
        def add(i):
            with self.connect() as conn:
                record = MinecraftCosmeticsRepository(conn).add(kind="skin", name=f"Skin {i}", texture=png(), created_by="tester", model="slim")
                return record["id"]
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(add, range(4)))
        self.assertEqual(sorted(ids), [5, 6, 7, 8])
        with self.connect() as conn:
            records = MinecraftCosmeticsRepository(conn).assets()
            self.assertEqual(len(records), 4); self.assertTrue(all(r["model"] == "slim" for r in records))

    def test_accessory_blob_roundtrip(self):
        with self.connect() as conn:
            record = MinecraftCosmeticsRepository(conn).add(kind="accessory", name="帽子", texture=png(), geometry=json_bytes(geometry()), icon=png((16, 16)), slot="hat", created_by="tester")
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).assets(), [record])

    def test_invalid_asset_rolls_back_without_consuming_id(self):
        with self.assertRaises(ValueError):
            with self.connect() as conn:
                MinecraftCosmeticsRepository(conn).add(kind="skin", name="bad", texture=b"invalid", created_by="tester")
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).add(kind="skin", name="valid", texture=png(), created_by="tester")["id"], 5)

    def test_placement_requires_recent_matching_server(self):
        with self.connect() as conn:
            repo = MinecraftCosmeticsRepository(conn)
            with self.assertRaises(ValueError): repo.place("ichiyon", "123", "a" * 64, 1, "Steve", "tester")
            repo.heartbeat("ichiyon", "123", "b" * 64)
            with self.assertRaises(ValueError): repo.place("ichiyon", "123", "a" * 64, 1, "Steve", "tester")
            repo.heartbeat("ichiyon", "123", "a" * 64)
            self.assertTrue(repo.place("ichiyon", "123", "a" * 64, 1, "Steve", "tester"))
            conn.execute("UPDATE minecraft_cosmetic_servers SET last_seen=NOW()-INTERVAL '2 minutes'")
            with self.assertRaises(ValueError): repo.place("ichiyon", "123", "a" * 64, 1, "Steve", "tester")

    def queue(self):
        with self.connect() as conn:
            repo = MinecraftCosmeticsRepository(conn); repo.heartbeat("ichiyon", "123", "a" * 64)
            return repo.place("ichiyon", "123", "a" * 64, 1, "Steve", "tester")

    def test_concurrent_poll_claims_once_and_respects_scope(self):
        request_id = self.queue()
        with self.connect() as conn: self.assertIsNone(MinecraftCosmeticsRepository(conn).claim("other", "123"))
        def claim(_):
            with self.connect() as conn: return MinecraftCosmeticsRepository(conn).claim("ichiyon", "123")
        with ThreadPoolExecutor(max_workers=4) as pool: claimed = [r for r in pool.map(claim, range(4)) if r]
        self.assertEqual(len(claimed), 1); self.assertEqual(claimed[0]["request_id"], request_id)
        with self.connect() as conn:
            repo = MinecraftCosmeticsRepository(conn)
            self.assertIsNotNone(repo.result(request_id, "succeeded", "ok"))
            self.assertIsNone(repo.result(request_id, "failed", "late"))
            self.assertEqual(repo.recent()[0]["status"], "succeeded")

    def test_expired_claim_is_not_retried(self):
        request_id = self.queue()
        with self.connect() as conn:
            repo = MinecraftCosmeticsRepository(conn); repo.claim("ichiyon", "123")
            conn.execute("UPDATE minecraft_cosmetic_placements SET expires_at=NOW()-INTERVAL '1 second'")
            repo.expire(); self.assertIsNone(repo.claim("ichiyon", "123"))
            self.assertIsNone(repo.result(request_id, "succeeded", "late")); self.assertEqual(repo.recent()[0]["reason"], "timeout")

    def test_export_revision_increases_across_connections(self):
        with self.connect() as conn: first = MinecraftCosmeticsRepository(conn).revision()
        with self.connect() as conn: second = MinecraftCosmeticsRepository(conn).revision()
        self.assertGreater(second, first)

    def test_authenticated_bridge_http_contract(self):
        class Legacy:
            def __init__(self, *args, **kwargs): pass
            def fail_expired(self): pass
            def claim_next_pending(self, **kwargs): return None
            def mark_result(self, **kwargs): return None
        def secret(value):
            if value != "test-only": raise HTTPException(401)
        app = FastAPI(); app.include_router(minecraft_internal.router)
        headers = {"X-Minecraft-Bridge-Secret": "test-only"}
        with patch.object(minecraft_internal, "get_connection", self.connect), patch.object(minecraft_internal, "MinecraftBridgeRepository", Legacy), patch.object(minecraft_internal, "require_minecraft_bridge_secret", secret), TestClient(app) as client:
            url = "/internal/minecraft/commands/next?bot_id=ichiyon&guild_id=123&cosmetics_digest=" + "a" * 64
            self.assertEqual(client.get(url).status_code, 401)
            self.assertEqual(client.get(url, headers=headers).json(), {"command": None})
            request_id = self.queue()
            result = client.get(url, headers=headers).json()["command"]
            self.assertEqual(result["type"], "cosmetic_avatar_spawn"); self.assertEqual(result["request_id"], request_id)
            response = client.post(f"/internal/minecraft/commands/{request_id}/result", headers=headers, json={"status": "succeeded", "reason": "ok"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(client.get(url, headers=headers).json(), {"command": None})


if __name__ == "__main__": unittest.main()

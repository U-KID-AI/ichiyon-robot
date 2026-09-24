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
            for name in ("065_add_minecraft_cosmetics.sql", "066_delete_minecraft_cosmetic_skins.sql",
                         "069_add_minecraft_managed_posters.sql"):
                migration = (ROOT / "migrations" / name).read_text(encoding="utf-8")
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
            conn.execute("TRUNCATE minecraft_cosmetic_assets,minecraft_cosmetic_servers,minecraft_cosmetic_deleted_assets")

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

    def test_deleted_skin_ids_are_hidden_and_never_reused(self):
        with self.connect() as conn:
            repo = MinecraftCosmeticsRepository(conn)
            first = repo.add(kind="skin", name="Skin 1", texture=png(), created_by="tester", model="classic")
            self.assertEqual(first["id"], 5)
            repo.delete_skin(5, "tester")
            repo.delete_skin(1, "tester")
            self.assertEqual(repo.assets(), [])
            self.assertEqual(repo.deleted_assets(), {("skin", 1), ("skin", 5)})
            second = repo.add(kind="skin", name="Skin 2", texture=png(), created_by="tester", model="slim")
            self.assertEqual(second["id"], 6)

    def test_accessory_blob_roundtrip(self):
        with self.connect() as conn:
            record = MinecraftCosmeticsRepository(conn).add(kind="accessory", name="帽子", texture=png(), geometry=json_bytes(geometry()), icon=png((16, 16)), slot="hat", created_by="tester")
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).assets(), [record])

    def test_posters_are_durable_and_ids_are_serialized(self):
        def add(i):
            with self.connect() as conn:
                return MinecraftCosmeticsRepository(conn).add(kind="poster", name=f"Poster {i}",
                    texture=png(), width=i + 1, height=10, created_by="tester")
        with ThreadPoolExecutor(max_workers=4) as pool:
            records = list(pool.map(add, range(4)))
        self.assertEqual(sorted(record["id"] for record in records), [1, 2, 3, 4])
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).assets(), sorted(records, key=lambda r: r["id"]))
            conn.execute("INSERT INTO minecraft_cosmetic_deleted_assets (kind,asset_id,deleted_by) VALUES ('poster',20,'tester')")
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).add(kind="poster", name="Next", texture=png(),
                             width=1, height=1, created_by="tester")["id"], 21)

    def test_poster_database_shape_constraints(self):
        for width, height in ((None, 2), (2, None), (0, 2), (2, 11)):
            with self.subTest(width=width, height=height), self.assertRaises(psycopg.errors.CheckViolation):
                with self.connect() as conn:
                    conn.execute("""INSERT INTO minecraft_cosmetic_assets
                        (kind,asset_id,asset_key,name,texture,created_by,width,height)
                        VALUES ('poster',1,'poster_1','Poster',%s,'tester',%s,%s)""", (png(), width, height))

    def test_upgrade_preserves_legacy_rows_and_tombstones(self):
        schema = self.schema + "_upgrade"
        with connection() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            for name in ("065_add_minecraft_cosmetics.sql", "066_delete_minecraft_cosmetic_skins.sql"):
                conn.execute((ROOT / "migrations" / name).read_text(encoding="utf-8"))
            conn.execute("""INSERT INTO minecraft_cosmetic_assets
                (kind,asset_id,asset_key,name,texture,geometry,icon,slot,created_by)
                VALUES ('accessory',1,'hat','Hat',%s,%s,%s,'hat','tester')""",
                (png(), json_bytes(geometry()), png()))
            conn.execute("""INSERT INTO minecraft_cosmetic_assets
                (kind,asset_id,asset_key,name,texture,model,created_by,deleted_at)
                VALUES ('skin',5,'skin_5','Deleted',%s,'classic','tester',NOW())""", (png(),))
            conn.execute("INSERT INTO minecraft_cosmetic_deleted_assets (kind,asset_id,deleted_by) VALUES ('skin',5,'tester')")
            before = conn.execute("SELECT kind,asset_id,asset_key,texture,geometry,icon,deleted_at FROM minecraft_cosmetic_assets ORDER BY kind").fetchall()
            for _ in range(2):
                conn.execute((ROOT / "migrations/069_add_minecraft_managed_posters.sql").read_text(encoding="utf-8"))
            self.assertEqual(before, conn.execute("SELECT kind,asset_id,asset_key,texture,geometry,icon,deleted_at FROM minecraft_cosmetic_assets ORDER BY kind").fetchall())
            repo = MinecraftCosmeticsRepository(conn)
            self.assertEqual(repo.deleted_assets(), {("skin", 5)})
            self.assertEqual([record["kind"] for record in repo.assets()], ["accessory"])
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))

    def test_invalid_asset_rolls_back_without_consuming_id(self):
        with self.assertRaises(ValueError):
            with self.connect() as conn:
                MinecraftCosmeticsRepository(conn).add(kind="skin", name="bad", texture=b"invalid", created_by="tester")
        with self.connect() as conn:
            self.assertEqual(MinecraftCosmeticsRepository(conn).add(kind="skin", name="valid", texture=png(), created_by="tester")["id"], 5)

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
            self.assertEqual(client.get(url, headers=headers).json(), {"command": None})
            with self.connect() as conn:
                servers = MinecraftCosmeticsRepository(conn).servers()
                self.assertEqual(servers[0]["catalog_digest"], "a" * 64)


if __name__ == "__main__": unittest.main()

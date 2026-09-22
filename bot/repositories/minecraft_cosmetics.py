"""Catalog blobs survive immutable app releases in the existing database."""
import re
import uuid
from bot.repositories.base import fetch_all, fetch_one
from bot.services.minecraft_cosmetics import asset, MAX_ID


class MinecraftCosmeticsRepository:
    def __init__(self, connection):
        self.connection = connection

    def assets(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM minecraft_cosmetic_assets ORDER BY kind, asset_id")
            rows = fetch_all(cursor)
        return [asset(row["kind"], row["asset_id"], row["asset_key"], row["name"], bytes(row["texture"]),
                      **({"model": row["model"]} if row["kind"] == "skin" else {"slot": row["slot"], "geometry": bytes(row["geometry"]), "icon": bytes(row["icon"])})) for row in rows]

    def add(self, *, kind, name, texture, created_by, model="classic", slot="hat", geometry=None, icon=None):
        if kind not in ("skin", "accessory"):
            raise ValueError("素材の種類が不正です。")
        with self.connection.cursor() as cursor:
            # Serialize assignment across workers, never recycle IDs or remap saved worlds.
            cursor.execute("LOCK TABLE minecraft_cosmetic_assets IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("SELECT COALESCE(MAX(asset_id), %s) + 1 FROM minecraft_cosmetic_assets WHERE kind = %s", (4 if kind == "skin" else 0, kind))
            asset_id = cursor.fetchone()[0]
            if asset_id > MAX_ID:
                raise ValueError("この種類の素材の登録上限に達しました。")
            record = asset(kind, asset_id, f"{kind}_{asset_id}", name, texture, model=model, slot=slot, geometry=geometry, icon=icon)
            cursor.execute("""INSERT INTO minecraft_cosmetic_assets
                (kind, asset_id, asset_key, name, model, slot, texture, geometry, icon, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (kind, asset_id, record["key"], record["name"], record.get("model"), record.get("slot"), record["texture"], record.get("geometry"), record.get("icon"), created_by))
        return record

    def revision(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT nextval('minecraft_cosmetic_export_revision')")
            return cursor.fetchone()[0]

    def heartbeat(self, bot_id, guild_id, digest):
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("invalid catalog digest")
        with self.connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minecraft_cosmetic_servers (bot_id,guild_id,catalog_digest)
                VALUES (%s,%s,%s) ON CONFLICT (bot_id,guild_id) DO UPDATE
                SET catalog_digest=EXCLUDED.catalog_digest,last_seen=NOW()""", (bot_id, guild_id, digest))

    def servers(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT *, last_seen > NOW() - INTERVAL '60 seconds' AS online FROM minecraft_cosmetic_servers ORDER BY bot_id,guild_id")
            return fetch_all(cursor)

    def place(self, bot_id, guild_id, digest, skin_id, player, user_id):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,16}", player) or type(skin_id) is not int or not 1 <= skin_id <= MAX_ID:
            raise ValueError("プレイヤー名またはスキンIDが不正です。")
        request_id = str(uuid.uuid4())
        with self.connection.cursor() as cursor:
            cursor.execute("""INSERT INTO minecraft_cosmetic_placements
                (request_id,bot_id,guild_id,catalog_digest,skin_id,minecraft_player,created_by)
                SELECT %s,bot_id,guild_id,%s,%s,%s,%s FROM minecraft_cosmetic_servers
                WHERE bot_id=%s AND guild_id=%s AND catalog_digest=%s
                  AND last_seen > NOW() - INTERVAL '60 seconds' RETURNING request_id""",
                (request_id, digest, skin_id, player, user_id, bot_id, guild_id, digest))
            if cursor.fetchone() is None:
                raise ValueError("サーバーが接続していないか、最新の素材パックが読み込まれていません。")
        return request_id

    def expire(self):
        with self.connection.cursor() as cursor:
            cursor.execute("UPDATE minecraft_cosmetic_placements SET status='failed',reason='timeout',completed_at=NOW() WHERE status IN ('pending','claimed') AND expires_at <= NOW()")

    def claim(self, bot_id, guild_id):
        with self.connection.cursor() as cursor:
            cursor.execute("""WITH candidate AS (
                SELECT request_id FROM minecraft_cosmetic_placements WHERE bot_id=%s AND guild_id=%s
                AND status='pending' AND expires_at>NOW() ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED)
                UPDATE minecraft_cosmetic_placements AS p SET status='claimed'
                FROM candidate WHERE p.request_id=candidate.request_id RETURNING p.*""", (bot_id, guild_id))
            row = fetch_one(cursor)
        if row:
            return {"request_id": str(row["request_id"]), "type": "cosmetic_avatar_spawn", "minecraft_player": row["minecraft_player"], "skin_id": row["skin_id"], "catalog_digest": row["catalog_digest"]}
        return None

    def result(self, request_id, status, reason):
        if status not in ("succeeded", "failed"):
            raise ValueError("invalid placement status")
        with self.connection.cursor() as cursor:
            cursor.execute("""UPDATE minecraft_cosmetic_placements SET status=%s,reason=%s,completed_at=NOW()
                WHERE request_id=%s AND status='claimed' AND expires_at>NOW() RETURNING request_id""", (status, reason[:120], request_id))
            return fetch_one(cursor)

    def recent(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT request_id, minecraft_player, skin_id, status, reason, created_at FROM minecraft_cosmetic_placements ORDER BY created_at DESC LIMIT 20")
            return fetch_all(cursor)

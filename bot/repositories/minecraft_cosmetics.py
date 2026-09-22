"""Catalog blobs survive immutable app releases in the existing database."""
from bot.repositories.base import fetch_all, fetch_one
from bot.services.minecraft_cosmetics import asset, MAX_ID


class MinecraftCosmeticsRepository:
    def __init__(self, connection):
        self.connection = connection

    def assets(self):
        with self.connection.cursor() as cursor:
            cursor.execute("""
                SELECT * FROM minecraft_cosmetic_assets
                WHERE deleted_at IS NULL
                  AND NOT EXISTS (
                    SELECT 1 FROM minecraft_cosmetic_deleted_assets AS d
                    WHERE d.kind = minecraft_cosmetic_assets.kind
                      AND d.asset_id = minecraft_cosmetic_assets.asset_id
                  )
                ORDER BY kind, asset_id
            """)
            rows = fetch_all(cursor)
        return [asset(row["kind"], row["asset_id"], row["asset_key"], row["name"], bytes(row["texture"]),
                      **({"model": row["model"]} if row["kind"] == "skin" else {"slot": row["slot"], "geometry": bytes(row["geometry"]), "icon": bytes(row["icon"])})) for row in rows]

    def deleted_assets(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT kind, asset_id FROM minecraft_cosmetic_deleted_assets ORDER BY kind, asset_id")
            return {(row["kind"], row["asset_id"]) for row in fetch_all(cursor)}

    def add(self, *, kind, name, texture, created_by, model="classic", slot="hat", geometry=None, icon=None):
        if kind not in ("skin", "accessory"):
            raise ValueError("素材の種類が不正です。")
        with self.connection.cursor() as cursor:
            # Serialize assignment across workers, never recycle IDs or remap saved worlds.
            cursor.execute("LOCK TABLE minecraft_cosmetic_assets IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("""
                SELECT GREATEST(
                    COALESCE((SELECT MAX(asset_id) FROM minecraft_cosmetic_assets WHERE kind = %s), %s),
                    COALESCE((SELECT MAX(asset_id) FROM minecraft_cosmetic_deleted_assets WHERE kind = %s), %s)
                ) + 1
            """, (kind, 4 if kind == "skin" else 0, kind, 4 if kind == "skin" else 0))
            asset_id = cursor.fetchone()[0]
            if asset_id > MAX_ID:
                raise ValueError("この種類の素材の登録上限に達しました。")
            record = asset(kind, asset_id, f"{kind}_{asset_id}", name, texture, model=model, slot=slot, geometry=geometry, icon=icon)
            cursor.execute("""INSERT INTO minecraft_cosmetic_assets
                (kind, asset_id, asset_key, name, model, slot, texture, geometry, icon, created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (kind, asset_id, record["key"], record["name"], record.get("model"), record.get("slot"), record["texture"], record.get("geometry"), record.get("icon"), created_by))
        return record

    def delete_skin(self, asset_id, deleted_by):
        if type(asset_id) is not int or not 1 <= asset_id <= MAX_ID:
            raise ValueError("スキンIDが不正です。")
        with self.connection.cursor() as cursor:
            cursor.execute("LOCK TABLE minecraft_cosmetic_assets IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("""
                SELECT name FROM minecraft_cosmetic_assets
                WHERE kind='skin' AND asset_id=%s AND deleted_at IS NULL
            """, (asset_id,))
            row = fetch_one(cursor)
            if row is None and asset_id > 4:
                raise ValueError("削除できるスキンがありません。")
            cursor.execute("""
                INSERT INTO minecraft_cosmetic_deleted_assets (kind, asset_id, deleted_by)
                VALUES ('skin', %s, %s)
                ON CONFLICT (kind, asset_id) DO NOTHING
            """, (asset_id, deleted_by))
            cursor.execute("""
                UPDATE minecraft_cosmetic_assets
                SET deleted_at = COALESCE(deleted_at, NOW())
                WHERE kind='skin' AND asset_id=%s
            """, (asset_id,))
        return True

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

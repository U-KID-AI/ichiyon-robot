"""Catalog blobs survive immutable app releases in the existing database."""
import re
import uuid

from bot.repositories.base import fetch_all, fetch_one
from bot.services.minecraft_cosmetics import asset, MAX_ID, validate_category


class MinecraftCosmeticsRepository:
    def __init__(self, connection):
        self.connection = connection

    def categories(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT category_id, name FROM minecraft_skin_categories ORDER BY category_id = 'uncategorized' DESC, name, category_id")
            return fetch_all(cursor)

    def skin_categories(self):
        with self.connection.cursor() as cursor:
            cursor.execute("""SELECT s.skin_id, c.category_id, c.name
                FROM minecraft_skin_category_assignments s
                JOIN minecraft_skin_categories c USING (category_id)""")
            return {row["skin_id"]: {"category_id": row["category_id"], "category": row["name"]}
                    for row in fetch_all(cursor)}

    def _category(self, cursor, category_id):
        if not isinstance(category_id, str):
            raise ValueError("カテゴリを選んでください。")
        cursor.execute("SELECT category_id, name FROM minecraft_skin_categories WHERE category_id=%s FOR SHARE", (category_id,))
        row = fetch_one(cursor)
        if row is None:
            raise ValueError("指定されたカテゴリがありません。")
        return {"category_id": row["category_id"], "category": row["name"]}

    def save_category(self, name, *, category_id=None):
        creating = category_id is None
        category_id = "cat_" + uuid.uuid4().hex if creating else category_id
        name = validate_category(category_id, name)
        with self.connection.cursor() as cursor:
            # Serialize uniqueness checks so concurrent forms get a readable error.
            cursor.execute("LOCK TABLE minecraft_skin_categories IN SHARE ROW EXCLUSIVE MODE")
            if not creating:
                self._category(cursor, category_id)
            cursor.execute("SELECT category_id FROM minecraft_skin_categories WHERE name=%s AND category_id<>%s", (name, category_id))
            if cursor.fetchone():
                raise ValueError("同じ名前のカテゴリが既にあります。")
            if creating:
                cursor.execute("INSERT INTO minecraft_skin_categories (category_id,name) VALUES (%s,%s)", (category_id, name))
            else:
                cursor.execute("UPDATE minecraft_skin_categories SET name=%s WHERE category_id=%s", (name, category_id))
        return category_id

    def set_skin_category(self, skin_id, category_id, updated_by):
        if type(skin_id) is not int or not 1 <= skin_id <= MAX_ID:
            raise ValueError("スキンIDが不正です。")
        with self.connection.cursor() as cursor:
            # Same lock as add/delete: an edit cannot revive a concurrently deleted skin.
            cursor.execute("LOCK TABLE minecraft_cosmetic_assets IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("SELECT 1 FROM minecraft_cosmetic_deleted_assets WHERE kind='skin' AND asset_id=%s", (skin_id,))
            if cursor.fetchone():
                raise ValueError("変更できるスキンがありません。")
            if skin_id > 4:
                cursor.execute("SELECT 1 FROM minecraft_cosmetic_assets WHERE kind='skin' AND asset_id=%s AND deleted_at IS NULL", (skin_id,))
                if not cursor.fetchone():
                    raise ValueError("変更できるスキンがありません。")
            self._category(cursor, category_id)
            cursor.execute("""INSERT INTO minecraft_skin_category_assignments (skin_id,category_id,updated_by)
                VALUES (%s,%s,%s) ON CONFLICT (skin_id) DO UPDATE
                SET category_id=EXCLUDED.category_id, updated_by=EXCLUDED.updated_by, updated_at=NOW()""",
                (skin_id, category_id, updated_by))

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
        categories = self.skin_categories()
        return [asset(row["kind"], row["asset_id"], row["asset_key"], row["name"], bytes(row["texture"]),
                      **({"model": row["model"], **categories.get(row["asset_id"], {})} if row["kind"] == "skin" else
                         {"width": row["width"], "height": row["height"]} if row["kind"] == "poster" else
                         {"slot": row["slot"], "geometry": bytes(row["geometry"]), "icon": bytes(row["icon"])})) for row in rows]

    def deleted_assets(self):
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT kind, asset_id FROM minecraft_cosmetic_deleted_assets ORDER BY kind, asset_id")
            return {(row["kind"], row["asset_id"]) for row in fetch_all(cursor)}

    def add(self, *, kind, name, texture, created_by, model="classic", slot="hat", geometry=None, icon=None,
            width=None, height=None, category_id="uncategorized"):
        if kind not in ("skin", "accessory", "poster"):
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
            category = self._category(cursor, category_id) if kind == "skin" else {}
            record = asset(kind, asset_id, f"{kind}_{asset_id}", name, texture, model=model, slot=slot,
                           geometry=geometry, icon=icon, width=width, height=height, **category)
            cursor.execute("""INSERT INTO minecraft_cosmetic_assets
                (kind, asset_id, asset_key, name, model, slot, texture, geometry, icon, created_by, width, height)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (kind, asset_id, record["key"], record["name"], record.get("model"), record.get("slot"), record["texture"],
                 record.get("geometry"), record.get("icon"), created_by, record.get("width"), record.get("height")))
            if kind == "skin":
                cursor.execute("INSERT INTO minecraft_skin_category_assignments (skin_id,category_id,updated_by) VALUES (%s,%s,%s)",
                               (asset_id, category_id, created_by))
        return record

    def delete_skin(self, asset_id, deleted_by):
        return self.delete_asset("skin", asset_id, deleted_by, builtin_ids=range(1, 5))

    def delete_asset(self, kind, asset_id, deleted_by, *, builtin_ids=()):
        """Soft-delete one kind/ID; builtin IDs must come from the trusted catalog."""
        if kind not in ("skin", "poster"):
            raise ValueError("削除できるのはスキンとポスターだけです。")
        label = "スキン" if kind == "skin" else "ポスター"
        if type(asset_id) is not int or not 1 <= asset_id <= MAX_ID:
            raise ValueError(f"{label}IDが不正です。")
        with self.connection.cursor() as cursor:
            cursor.execute("LOCK TABLE minecraft_cosmetic_assets IN SHARE ROW EXCLUSIVE MODE")
            cursor.execute("""
                SELECT name FROM minecraft_cosmetic_assets
                WHERE kind=%s AND asset_id=%s AND deleted_at IS NULL
            """, (kind, asset_id))
            row = fetch_one(cursor)
            if row is None and asset_id not in builtin_ids:
                raise ValueError(f"削除できる{label}がありません。")
            cursor.execute("""
                INSERT INTO minecraft_cosmetic_deleted_assets (kind, asset_id, deleted_by)
                VALUES (%s, %s, %s)
                ON CONFLICT (kind, asset_id) DO NOTHING
            """, (kind, asset_id, deleted_by))
            cursor.execute("""
                UPDATE minecraft_cosmetic_assets
                SET deleted_at = COALESCE(deleted_at, NOW())
                WHERE kind=%s AND asset_id=%s
            """, (kind, asset_id))
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

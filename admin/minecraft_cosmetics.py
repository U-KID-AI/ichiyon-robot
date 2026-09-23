from pathlib import Path
import secrets
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse, Response
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile

from admin.auth import require_login
from bot.db import get_connection
from bot.repositories.minecraft_cosmetics import MinecraftCosmeticsRepository
from bot.services.minecraft_cosmetics import MAX_UPLOAD, public_asset
from bot.services.minecraft_cosmetics_pack import builtin_assets, catalog_digest, pack_zip
from bot.services.minecraft_control import cosmetics_control, MinecraftControlError

router = APIRouter(prefix="/minecraft/cosmetics", tags=["minecraft-cosmetics"])
ROOT = Path(__file__).resolve().parent.parent / "minecraft"


async def bounded_form(request):
    """Authenticate first, then bound even chunked multipart bodies before parsing."""
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_UPLOAD * 3 + 65536:
            raise HTTPException(413, "アップロードの合計サイズが大きすぎます。")
        data.extend(chunk)
    async def receive():
        return {"type": "http.request", "body": bytes(data), "more_body": False}
    parsed = Request(request.scope, receive)
    async with parsed.form(max_files=3, max_fields=10) as form:
        token = form.get("csrf")
        expected = request.session.get("cosmetics_csrf")
        if not isinstance(token, str) or not token.isascii() or not expected or not secrets.compare_digest(token, expected):
            raise HTTPException(403, "画面を再読み込みして、もう一度操作してください。")
        result = {}
        for key, value in form.items():
            if isinstance(value, UploadFile):
                result[key] = await value.read(MAX_UPLOAD + 1)
                if len(result[key]) > MAX_UPLOAD:
                    raise ValueError("各ファイルは1MB以下にしてください。")
            else:
                result[key] = value
        return result


def records(repository):
    deleted = repository.deleted_assets()
    builtins = [entry for entry in builtin_assets(ROOT) if (entry["kind"], entry["id"]) not in deleted]
    return builtins + repository.assets()


def register_minecraft_cosmetics_routes(templates):
    def page(request, *, error=None, code=200):
        with get_connection() as connection:
            repository = MinecraftCosmeticsRepository(connection)
            assets = records(repository)
            connection.commit()
        csrf = request.session.setdefault("cosmetics_csrf", secrets.token_urlsafe(32))
        return templates.TemplateResponse(request, "minecraft_cosmetics.html", {
            "assets": [public_asset(a) for a in assets], "digest": catalog_digest(assets),
            "csrf": csrf, "error": error, "operation_id": str(uuid.uuid4()),
            "current_bot_instance": {"display_name": "Minecraft", "bot_id": "共有素材"},
        }, status_code=code, headers={"Cache-Control": "no-store"})

    @router.get("")
    async def catalog_page(request: Request):
        require_login(request)
        return page(request)

    @router.post("/assets")
    async def upload(request: Request):
        user = require_login(request)
        try:
            form = await bounded_form(request)
            with get_connection() as connection:
                repository = MinecraftCosmeticsRepository(connection)
                repository.add(kind=form.get("kind"), name=form.get("name"), texture=form.get("texture", b""),
                               model=form.get("model", "classic"), slot=form.get("slot", "hat"),
                               geometry=form.get("geometry"), icon=form.get("icon"), created_by=str(user["user_id"]))
                connection.commit()
        except ValueError as exc:
            return page(request, error=str(exc), code=400)
        return RedirectResponse("/minecraft/cosmetics", status_code=303)

    @router.post("/assets/{kind}/{asset_id}/delete")
    async def delete_asset(request: Request, kind: str, asset_id: int):
        user = require_login(request)
        try:
            await bounded_form(request)
            if kind != "skin":
                raise ValueError("削除できるのはスキンだけです。")
            with get_connection() as connection:
                MinecraftCosmeticsRepository(connection).delete_skin(asset_id, str(user["user_id"]))
                connection.commit()
        except ValueError as exc:
            return page(request, error=str(exc), code=400)
        return RedirectResponse("/minecraft/cosmetics", status_code=303)

    @router.get("/preview/{kind}/{asset_id}")
    async def preview(request: Request, kind: str, asset_id: int):
        require_login(request)
        with get_connection() as connection:
            entry = next((a for a in records(MinecraftCosmeticsRepository(connection)) if a["kind"] == kind and a["id"] == asset_id), None)
        if not entry:
            raise HTTPException(404, "素材がありません。")
        return Response(entry["texture"], media_type="image/png", headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "no-store"})

    @router.post("/export")
    async def export(request: Request):
        require_login(request)
        await bounded_form(request)
        with get_connection() as connection:
            repository = MinecraftCosmeticsRepository(connection)
            assets = records(repository)
            revision = repository.revision()
            connection.commit()
        data = await run_in_threadpool(pack_zip, ROOT, assets, revision)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="ichiyon-cosmetics-{revision}.zip"',
            "Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
        })

    @router.get('/application')
    async def application(request: Request):
        require_login(request)
        try:
            result = await cosmetics_control()
            with get_connection() as connection:
                repository = MinecraftCosmeticsRepository(connection)
                digest = catalog_digest(records(repository))
                servers = repository.servers()
            result['current'] = result.get('installed') and result.get('active_digest') == digest
            result['game_connected'] = any(s['online'] and s['catalog_digest'] == digest for s in servers)
            return result
        except MinecraftControlError as exc:
            raise HTTPException(503, str(exc)) from None

    @router.post('/apply')
    async def apply(request: Request):
        require_login(request)
        try:
            form = await bounded_form(request)
            operation_id = str(uuid.UUID(str(form.get('operation_id', ''))))
            # A browser retry uses the same ID and only observes the original job.
            previous = await cosmetics_control()
            if previous.get('operation_id') != operation_id:
                with get_connection() as connection:
                    repository = MinecraftCosmeticsRepository(connection)
                    assets = records(repository)
                    revision = repository.revision()
                    connection.commit()
                archive = await run_in_threadpool(pack_zip, ROOT, assets, revision)
                await cosmetics_control(operation_id, archive)
        except (ValueError, MinecraftControlError) as exc:
            return page(request, error=str(exc), code=400)
        return RedirectResponse('/minecraft/cosmetics', status_code=303)

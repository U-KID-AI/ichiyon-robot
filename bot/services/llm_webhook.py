"""Rachel-only transport. One attempt; never fall back to the Bot identity."""
import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx

from bot.services.llm_limits import GeminiError

# HTTPX's INFO request log includes credential-bearing webhook URLs.
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)


def credentials(settings):
    data = json.loads(Path(settings.webhook_path).read_text(encoding='utf-8'))
    if (data.get('id') != settings.webhook_id or data.get('channel_id') != settings.channel_id
            or data.get('name') != 'レイチェル竈門にぼ' or not data.get('avatar')
            or not isinstance(data.get('token'), str) or not data['token']):
        raise ValueError('Rachel webhook identity incomplete')
    return data


async def send(settings, message, text):
    if not settings.enabled or not settings.allows(message):
        raise GeminiError('webhook_disabled')
    data = await asyncio.to_thread(credentials, settings)
    client = await asyncio.to_thread(httpx.AsyncClient, timeout=15, follow_redirects=False)
    try:
        async with client:
            async with client.stream('POST', 'https://discord.com/api/v10/webhooks/' + data['id'] + '/' + data['token'],
                    params={'wait': 'true'}, json={'content': text[:1900], 'allowed_mentions': {'parse': [], 'replied_user': False}}) as response:
                raw = bytearray()
                async for chunk in response.aiter_bytes():
                    raw.extend(chunk)
                    if len(raw) > 131072:
                        raise GeminiError('webhook_delivery_unknown')
                if response.status_code != 200:
                    raise GeminiError('webhook_http_' + str(response.status_code))
                sent = json.loads(raw)
                if (str(sent.get('webhook_id')) != data['id'] or str(sent.get('channel_id')) != settings.channel_id
                        or not str(sent.get('id', '')).isdigit()):
                    raise GeminiError('webhook_delivery_unknown')
                return SimpleNamespace(id=str(sent['id']))
    except GeminiError:
        raise
    except Exception:
        # HTTP errors may include the webhook token in their URL.
        raise GeminiError('webhook_delivery_unknown') from None

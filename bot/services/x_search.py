from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx

from bot import config


RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"


@dataclass
class XMedia:
    media_key: str
    url: str
    type: str
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class XPost:
    post_id: str
    text: str
    created_at: str
    media: List[XMedia]

    @property
    def url(self) -> str:
        return "https://x.com/i/web/status/{0}".format(self.post_id)


class XSearchDisabled(Exception):
    pass


class XSearchError(Exception):
    pass


def clamp_x_max_results(value: int) -> int:
    return max(10, min(100, value))


def build_media_map(payload: Dict[str, Any]) -> Dict[str, XMedia]:
    media_map = {}
    includes = payload.get("includes") or {}
    for item in includes.get("media") or []:
        media_key = item.get("media_key")
        url = item.get("url") or item.get("preview_image_url")
        if not media_key or not url:
            continue
        media_map[media_key] = XMedia(
            media_key=str(media_key),
            url=str(url),
            type=str(item.get("type") or ""),
            width=item.get("width"),
            height=item.get("height"),
        )
    return media_map


def parse_search_response(payload: Dict[str, Any]) -> List[XPost]:
    media_map = build_media_map(payload)
    posts = []
    for item in payload.get("data") or []:
        attachments = item.get("attachments") or {}
        media = []
        for key in attachments.get("media_keys") or []:
            found = media_map.get(key)
            if found is not None:
                media.append(found)
        posts.append(
            XPost(
                post_id=str(item.get("id") or ""),
                text=str(item.get("text") or ""),
                created_at=str(item.get("created_at") or ""),
                media=media,
            )
        )
    posts.sort(key=lambda post: 0 if post.media else 1)
    return posts


async def search_recent_posts(query: str, max_results: int, timeout_seconds: int) -> List[XPost]:
    if not config.X_SEARCH_ENABLED:
        raise XSearchDisabled()
    bearer_token = config.X_BEARER_TOKEN.strip()
    if not bearer_token:
        raise XSearchDisabled()

    request_results = clamp_x_max_results(max_results)
    params = {
        "query": query,
        "max_results": request_results,
        "tweet.fields": "created_at,attachments,text",
        "expansions": "attachments.media_keys",
        "media.fields": "url,preview_image_url,type,width,height",
        "sort_order": "recency",
    }
    headers = {"Authorization": "Bearer {0}".format(bearer_token)}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(RECENT_SEARCH_URL, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise XSearchError("timeout") from exc
    except httpx.HTTPError as exc:
        raise XSearchError("request failed") from exc

    if response.status_code in (401, 403, 429):
        raise XSearchError("api status {0}".format(response.status_code))
    if response.status_code >= 400:
        raise XSearchError("api status {0}".format(response.status_code))

    try:
        payload = response.json()
    except ValueError as exc:
        raise XSearchError("invalid json") from exc
    return parse_search_response(payload)

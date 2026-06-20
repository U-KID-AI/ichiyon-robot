from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import httpx

from bot import config


RECENT_SEARCH_URL = "https://api.x.com/2/tweets/search/recent"
FULL_ARCHIVE_SEARCH_URL = "https://api.x.com/2/tweets/search/all"


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
    def __init__(self, message: str, status_code: Optional[int] = None, endpoint_type: str = "recent") -> None:
        super().__init__(message)
        self.status_code = status_code
        self.endpoint_type = endpoint_type


def clamp_x_max_results(value: int) -> int:
    return max(10, min(100, value))


def clamp_lookback_days(value: int) -> int:
    return max(1, min(30, value))


def normalize_search_mode(value: str) -> str:
    normalized = (value or "recent").strip().lower()
    if normalized == "full_archive":
        return "full_archive"
    return "recent"


def build_search_time_range(lookback_days: int, now: Optional[datetime] = None) -> Dict[str, str]:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    start = current - timedelta(days=clamp_lookback_days(lookback_days))
    return {
        "start_time": start.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "end_time": current.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }


def get_search_endpoint(search_mode: str) -> str:
    endpoint_type = normalize_search_mode(search_mode)
    if endpoint_type == "full_archive":
        return FULL_ARCHIVE_SEARCH_URL
    return RECENT_SEARCH_URL


def build_search_params(query: str, max_results: int, search_mode: str, lookback_days: int) -> Dict[str, Any]:
    endpoint_type = normalize_search_mode(search_mode)
    params = {
        "query": query,
        "max_results": clamp_x_max_results(max_results),
        "tweet.fields": "created_at,attachments,text",
        "expansions": "attachments.media_keys",
        "media.fields": "url,preview_image_url,type,width,height",
        "sort_order": "recency",
    }
    if endpoint_type == "full_archive":
        params.update(build_search_time_range(lookback_days))
    return params


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


async def search_posts(
    query: str,
    max_results: int,
    timeout_seconds: int,
    search_mode: str = "recent",
    lookback_days: int = 14,
) -> List[XPost]:
    if not config.X_SEARCH_ENABLED:
        raise XSearchDisabled()
    bearer_token = config.X_BEARER_TOKEN.strip()
    if not bearer_token:
        raise XSearchDisabled()

    endpoint_type = normalize_search_mode(search_mode)
    url = get_search_endpoint(endpoint_type)
    params = build_search_params(query, max_results, endpoint_type, lookback_days)
    headers = {"Authorization": "Bearer {0}".format(bearer_token)}
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            response = await client.get(url, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise XSearchError("timeout", endpoint_type=endpoint_type) from exc
    except httpx.HTTPError as exc:
        raise XSearchError("request failed", endpoint_type=endpoint_type) from exc

    if response.status_code >= 400:
        print("[WARN] X search failed: endpoint={0} status={1}".format(endpoint_type, response.status_code))
        raise XSearchError(
            "api status {0}".format(response.status_code),
            status_code=response.status_code,
            endpoint_type=endpoint_type,
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise XSearchError("invalid json", endpoint_type=endpoint_type) from exc
    return parse_search_response(payload)


async def search_recent_posts(query: str, max_results: int, timeout_seconds: int) -> List[XPost]:
    return await search_posts(query, max_results, timeout_seconds, "recent", 14)

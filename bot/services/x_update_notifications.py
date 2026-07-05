import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import discord

from bot import config
from bot.db import get_connection
from bot.repositories import FeatureFlagRepository, XUpdateWatchRepository
from bot.services import x_search


FEATURE_X_UPDATES = "x_updates"
DEFAULT_POST_TEMPLATE = "{account_name} が更新しました\n{post_url}"
DEFAULT_CHECK_INTERVAL_SECONDS = 900
DEFAULT_MAX_POSTS_PER_CHECK = 5
DEFAULT_X_UPDATE_FETCH_LIMIT = 10
SAFE_QUERY_TERM_RE = re.compile(r"^[0-9A-Za-z_\-\u3040-\u30ff\u3400-\u9fff]+$")


def normalize_username(value: str) -> str:
    return str(value or "").strip().lstrip("@")


def parse_keyword_list(value: Any) -> List[str]:
    raw_text = str(value or "")
    if not raw_text.strip():
        return []
    words = []
    seen = set()
    for item in re.split(r"[\n,，、]+", raw_text):
        word = item.strip()
        if not word:
            continue
        normalized = word.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        words.append(word)
    return words


def is_safe_x_query_term(value: str) -> bool:
    if not value or len(value) > 40:
        return False
    if "http://" in value.lower() or "https://" in value.lower():
        return False
    if "@" in value or any(ord(char) < 32 for char in value):
        return False
    return SAFE_QUERY_TERM_RE.match(value) is not None


def build_keyword_query_terms(words: List[str]) -> List[str]:
    return [word for word in words if is_safe_x_query_term(word)]


def build_x_update_search_query(username: str, include_keywords: List[str], exclude_keywords: List[str]) -> str:
    query_parts = ["from:{0}".format(normalize_username(username))]
    safe_include = build_keyword_query_terms(include_keywords)
    safe_exclude = build_keyword_query_terms(exclude_keywords)
    if safe_include:
        if len(safe_include) == 1:
            query_parts.append(safe_include[0])
        else:
            query_parts.append("({0})".format(" OR ".join(safe_include)))
    for word in safe_exclude:
        query_parts.append("-{0}".format(word))
    return " ".join(query_parts)


def post_matches_keyword_filters(post: x_search.XPost, include_keywords: List[str], exclude_keywords: List[str]) -> bool:
    text = (post.text or "").casefold()
    include_values = [word.casefold() for word in include_keywords]
    exclude_values = [word.casefold() for word in exclude_keywords]
    if exclude_values and any(word in text for word in exclude_values):
        return False
    if include_values and not any(word in text for word in include_values):
        return False
    return True


def get_post_reference_types(post: x_search.XPost) -> List[str]:
    return list(post.referenced_types or [])


def should_post_update(post: x_search.XPost, watch: Dict[str, Any]) -> bool:
    include_keywords = parse_keyword_list(watch.get("include_keywords"))
    exclude_keywords = parse_keyword_list(watch.get("exclude_keywords"))
    if not post_matches_keyword_filters(post, include_keywords, exclude_keywords):
        return False
    reference_types = set(get_post_reference_types(post))
    if "retweeted" in reference_types and not bool(watch.get("include_reposts")):
        return False
    if "replied_to" in reference_types and not bool(watch.get("include_replies")):
        return False
    if "quoted" in reference_types and not bool(watch.get("include_quotes")):
        return False
    return True


def newest_post_id(posts: List[x_search.XPost]) -> Optional[str]:
    if not posts:
        return None
    sorted_posts = sorted(posts, key=lambda post: int(post.post_id) if post.post_id.isdigit() else 0)
    return sorted_posts[-1].post_id


def render_post_template(watch: Dict[str, Any], post: x_search.XPost) -> str:
    template = str(watch.get("post_template") or DEFAULT_POST_TEMPLATE)
    account_name = str(watch.get("display_name") or watch.get("x_username") or "")
    values = {
        "account_name": account_name,
        "username": str(watch.get("x_username") or ""),
        "post_text": post.text or "",
        "post_url": post.url,
        "created_at": post.created_at or "",
    }
    rendered = template
    for key, value in values.items():
        rendered = rendered.replace("{" + key + "}", value)
    return rendered


async def get_watch_channel(bot, watch: Dict[str, Any]) -> Optional[discord.abc.Messageable]:
    raw_channel_id = str(watch.get("channel_id") or "").strip()
    if not raw_channel_id:
        print("[WARN] x_update channel_id is not set: id={0}".format(watch.get("id")))
        return None
    try:
        channel_id = int(raw_channel_id)
    except ValueError:
        print("[WARN] x_update channel_id is invalid: id={0}".format(watch.get("id")))
        return None
    channel = bot.get_channel(channel_id)
    if channel is None:
        try:
            channel = await bot.fetch_channel(channel_id)
        except discord.DiscordException as exc:
            print("[WARN] x_update channel was not found: id={0} error={1}".format(watch.get("id"), exc))
            return None
    if not hasattr(channel, "send"):
        print("[WARN] x_update channel cannot send messages: id={0}".format(watch.get("id")))
        return None
    return channel


async def ensure_x_user(repository: XUpdateWatchRepository, watch: Dict[str, Any]) -> Optional[str]:
    x_user_id = str(watch.get("x_user_id") or "").strip()
    if x_user_id:
        return x_user_id
    username = normalize_username(str(watch.get("x_username") or ""))
    if not username:
        raise x_search.XSearchError("username is empty", endpoint_type="user_lookup")
    user = await x_search.lookup_user_by_username(username)
    repository.update_user_identity(int(watch["id"]), user.user_id, user.name, user.username)
    watch["x_user_id"] = user.user_id
    watch["display_name"] = watch.get("display_name") or user.name
    watch["x_username"] = user.username
    return user.user_id


async def process_x_update_watch(bot, repository: XUpdateWatchRepository, watch: Dict[str, Any]) -> int:
    watch_id = int(watch["id"])
    x_user_id = await ensure_x_user(repository, watch)
    if not x_user_id:
        return 0

    last_seen_post_id = str(watch.get("last_seen_post_id") or "").strip() or None
    include_keywords = parse_keyword_list(watch.get("include_keywords"))
    exclude_keywords = parse_keyword_list(watch.get("exclude_keywords"))
    safe_query_terms = build_keyword_query_terms(include_keywords) + build_keyword_query_terms(exclude_keywords)
    if safe_query_terms:
        query = build_x_update_search_query(str(watch.get("x_username") or ""), include_keywords, exclude_keywords)
        posts = await x_search.search_recent_posts(
            query,
            DEFAULT_X_UPDATE_FETCH_LIMIT,
            10,
            since_id=last_seen_post_id,
        )
    else:
        posts = await x_search.get_user_posts(
            x_user_id,
            last_seen_post_id,
            DEFAULT_X_UPDATE_FETCH_LIMIT,
        )
    latest_seen = newest_post_id(posts)

    if not last_seen_post_id:
        repository.mark_checked_success(watch_id, latest_seen, None)
        return 0

    postable_posts = [post for post in posts if should_post_update(post, watch)]
    postable_posts = postable_posts[:DEFAULT_MAX_POSTS_PER_CHECK]
    if not postable_posts:
        repository.mark_checked_success(watch_id, latest_seen, None)
        return 0

    channel = await get_watch_channel(bot, watch)
    if channel is None:
        repository.mark_checked_success(watch_id, latest_seen, None)
        return 0

    sent_count = 0
    last_posted_id = None
    for post in postable_posts:
        history = repository.record_history(watch_id, post.post_id, post.url, post.text, str(watch.get("channel_id") or ""))
        if history is None:
            continue
        message_text = render_post_template(watch, post)
        try:
            sent = await channel.send(message_text)
        except discord.DiscordException as exc:
            print("[WARN] x_update send failed: id={0} error={1}".format(watch_id, exc))
            continue
        message_id = str(getattr(sent, "id", "")) if sent is not None else None
        repository.mark_history_posted(int(history["id"]), message_id)
        sent_count += 1
        last_posted_id = post.post_id

    repository.mark_checked_success(watch_id, latest_seen, last_posted_id)
    return sent_count


async def run_x_update_notifications_once(bot, now: Optional[datetime] = None) -> int:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    sent_count = 0
    with get_connection() as connection:
        repository = XUpdateWatchRepository(connection)
        flag_repository = FeatureFlagRepository(connection)
        for watch in repository.list_due_enabled_watches(config.BOT_INSTANCE_ID, current):
            watch_id = int(watch["id"])
            try:
                guild_id = str(watch.get("guild_id") or "")
                if not flag_repository.is_enabled(guild_id, FEATURE_X_UPDATES, default=True):
                    continue
                sent_count += await process_x_update_watch(bot, repository, watch)
                connection.commit()
            except x_search.XSearchDisabled:
                repository.mark_checked_error(watch_id, "X search disabled")
                connection.commit()
            except Exception as exc:
                try:
                    repository.mark_checked_error(watch_id, "{0}: {1}".format(type(exc).__name__, exc))
                    connection.commit()
                except Exception:
                    try:
                        connection.rollback()
                    except Exception:
                        pass
                print("[WARN] x_update skipped watch id={0}: {1}".format(watch_id, type(exc).__name__))
    return sent_count

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any, Dict, List

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from bot import config  # noqa: E402
from bot.services.deck_search import fetch_image_bytes  # noqa: E402
from bot.services.qr_detector import detect_qr_codes, opencv_available  # noqa: E402
from bot.services.x_search import parse_search_response  # noqa: E402


TWEET_LOOKUP_URL = "https://api.x.com/2/tweets"


def build_lookup_params(tweet_id: str) -> Dict[str, Any]:
    return {
        "ids": tweet_id,
        "tweet.fields": "created_at,referenced_tweets",
        "expansions": "attachments.media_keys,referenced_tweets.id,referenced_tweets.id.attachments.media_keys",
        "media.fields": "url,preview_image_url,type,width,height",
    }


async def lookup_tweet(tweet_id: str, timeout_seconds: int) -> Dict[str, Any]:
    bearer_token = config.X_BEARER_TOKEN.strip()
    if not bearer_token:
        raise RuntimeError("X_BEARER_TOKEN is not set")
    headers = {"Authorization": "Bearer {0}".format(bearer_token)}
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        response = await client.get(TWEET_LOOKUP_URL, params=build_lookup_params(tweet_id), headers=headers)
    print("tweet lookup status={0}".format(response.status_code))
    if response.status_code >= 400:
        raise RuntimeError("tweet lookup failed status={0}".format(response.status_code))
    return response.json()


def print_payload_media(payload: Dict[str, Any]) -> None:
    data = payload.get("data") or []
    includes = payload.get("includes") or {}
    media = includes.get("media") or []
    tweets = includes.get("tweets") or []
    print("data_count={0} included_tweets={1} included_media={2}".format(len(data), len(tweets), len(media)))
    for tweet in data:
        attachments = tweet.get("attachments") or {}
        print("tweet_id={0} attachments.media_keys={1}".format(tweet.get("id"), attachments.get("media_keys") or []))
        print("tweet_id={0} referenced_tweets={1}".format(tweet.get("id"), tweet.get("referenced_tweets") or []))
    for tweet in tweets:
        attachments = tweet.get("attachments") or {}
        print("referenced_tweet_id={0} attachments.media_keys={1}".format(tweet.get("id"), attachments.get("media_keys") or []))
    for item in media:
        print(
            "media_key={0} type={1} url={2} preview_image_url={3}".format(
                item.get("media_key"),
                item.get("type"),
                item.get("url") or "",
                item.get("preview_image_url") or "",
            )
        )


async def debug_media(tweet_id: str, timeout_seconds: int) -> int:
    payload = await lookup_tweet(tweet_id, timeout_seconds)
    print_payload_media(payload)
    posts = parse_search_response(payload)
    if not posts:
        print("no tweet data parsed")
        return 1

    debug_dir = Path("/tmp") / "deck_debug_{0}".format(tweet_id)
    debug_dir.mkdir(parents=True, exist_ok=True)
    print("debug_dir={0}".format(debug_dir))
    print("opencv_available={0}".format(opencv_available()))

    downloaded = 0
    qr_detected = 0
    for post in posts:
        print("parsed_post_id={0} media_count={1}".format(post.post_id, len(post.media)))
        for index, media in enumerate(post.media, start=1):
            print("candidate media={0} type={1} image_url={2}".format(media.media_key, media.type, media.url))
            image_bytes = await fetch_image_bytes(media.url, timeout_seconds)
            if image_bytes is None:
                print("download=failed media={0}".format(media.media_key))
                continue
            downloaded += 1
            suffix = ".jpg"
            save_path = debug_dir / "{0}_{1}_{2}{3}".format(post.post_id, index, media.media_key, suffix)
            save_path.write_bytes(image_bytes)
            print("download=ok bytes={0} saved={1}".format(len(image_bytes), save_path))
            try:
                detections = detect_qr_codes(image_bytes)
            except RuntimeError as exc:
                print("qr=unavailable error={0}".format(exc))
                continue
            except Exception as exc:
                print("qr=error type={0}".format(exc.__class__.__name__))
                continue
            if detections:
                qr_detected += len(detections)
                print("qr=detected count={0} scores={1}".format(len(detections), [item.score for item in detections]))
            else:
                print("qr=not_detected")

    print("summary downloaded={0} qr_detected={1}".format(downloaded, qr_detected))
    return 0 if downloaded > 0 else 1


def main(argv: List[str]) -> int:
    parser = argparse.ArgumentParser(description="Debug deck search tweet media and QR detection.")
    parser.add_argument("--tweet-id", required=True)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    args = parser.parse_args(argv)
    return asyncio.run(debug_media(args.tweet_id, args.timeout_seconds))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

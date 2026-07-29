import asyncio
import random
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


DEFAULT_USER_AGENT = "ichiyon-robot/1.0 (+https://github.com/U-KID-AI/ichiyon-robot)"


@dataclass(frozen=True)
class ExternalHttpPolicy:
    connect_timeout: float = 5.0
    read_timeout: float = 10.0
    write_timeout: float = 10.0
    pool_timeout: float = 5.0
    retries: int = 1
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 5.0
    trust_env: bool = False
    follow_redirects: bool = True
    user_agent: str = DEFAULT_USER_AGENT


class ExternalHttpError(RuntimeError):
    def __init__(self, message: str, *, status_code: Optional[int] = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


DEFAULT_PUBLIC_API_POLICY = ExternalHttpPolicy()


def timeout_from_policy(policy: ExternalHttpPolicy) -> httpx.Timeout:
    return httpx.Timeout(
        connect=policy.connect_timeout,
        read=policy.read_timeout,
        write=policy.write_timeout,
        pool=policy.pool_timeout,
    )


def is_retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code <= 599


def retry_after_seconds(response: httpx.Response, policy: ExternalHttpPolicy) -> float:
    raw = response.headers.get("retry-after", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(policy.backoff_max_seconds, value))


def redacted_url_for_log(url: str) -> str:
    parsed = httpx.URL(url)
    path = parsed.path or "/"
    return "{0}://{1}{2}".format(parsed.scheme, parsed.host, path)


async def sleep_before_retry(attempt: int, response: Optional[httpx.Response], policy: ExternalHttpPolicy) -> None:
    if response is not None:
        retry_after = retry_after_seconds(response, policy)
        if retry_after > 0:
            await asyncio.sleep(retry_after)
            return
    delay = min(policy.backoff_max_seconds, policy.backoff_base_seconds * (2 ** max(0, attempt - 1)))
    jitter = random.uniform(0.0, min(0.25, delay / 4))
    await asyncio.sleep(delay + jitter)


async def fetch_json(
    url: str,
    *,
    policy: ExternalHttpPolicy = DEFAULT_PUBLIC_API_POLICY,
    headers: Optional[Dict[str, str]] = None,
) -> Any:
    request_headers = {"User-Agent": policy.user_agent}
    if headers:
        request_headers.update(headers)
    last_error: Optional[BaseException] = None
    max_attempts = max(1, policy.retries + 1)
    async with httpx.AsyncClient(
        timeout=timeout_from_policy(policy),
        follow_redirects=policy.follow_redirects,
        trust_env=policy.trust_env,
        headers=request_headers,
    ) as client:
        for attempt in range(1, max_attempts + 1):
            response: Optional[httpx.Response] = None
            try:
                response = await client.get(url)
                if response.status_code < 400:
                    return response.json()
                retryable = is_retryable_status(response.status_code)
                if retryable and attempt < max_attempts:
                    await sleep_before_retry(attempt, response, policy)
                    continue
                raise ExternalHttpError(
                    "external request failed with status {0}".format(response.status_code),
                    status_code=response.status_code,
                    retryable=retryable,
                )
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                if attempt < max_attempts:
                    await sleep_before_retry(attempt, response, policy)
                    continue
                raise ExternalHttpError("external request failed", retryable=True) from exc
            except ValueError as exc:
                raise ExternalHttpError("external response is not valid json", retryable=False) from exc
    raise ExternalHttpError("external request failed", retryable=True) from last_error

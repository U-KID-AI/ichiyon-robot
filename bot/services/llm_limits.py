"""Persistent project/model quota windows. Never store provider error text or keys."""
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

import psycopg

from bot.db import require_database_url

QUOTA_REPLY = "ゴソゴソ\nアイスがないよ"
PACIFIC = ZoneInfo("America/Los_Angeles")
LOCK = 1568969564769747006


class GeminiError(Exception):
    def __init__(self, status):
        self.status = status
        super().__init__(str(status))


class QuotaError(GeminiError):
    def __init__(self, reason, until=None, observed=None, status="local_quota"):
        super().__init__(status)
        self.reason = reason
        self.until = until
        self.observed = observed or {}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: str = ""
    until: datetime | None = None


def day_bounds(now):
    local = now.astimezone(PACIFIC)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)


def decide(now, recent, daily_count, settings, estimated_tokens=0, observed=None):
    """recent is the bounded last-minute list of (timestamp, input tokens)."""
    limits = {name: getattr(settings, name) for name in
              ("requests_per_minute", "requests_per_day", "tokens_per_minute")}
    for name, value in (observed or {}).items():
        if name in limits and type(value) is int and value >= 0:
            limits[name] = min(limits[name], math.floor(value * .95))
    if daily_count >= limits["requests_per_day"]:
        return Decision(False, "day", day_bounds(now)[1])
    deadlines = []
    recent = sorted((at, tokens) for at, tokens in recent if at > now - timedelta(seconds=60))
    rpm = limits["requests_per_minute"]
    if len(recent) >= rpm:
        deadlines.append((recent[len(recent) - rpm][0] + timedelta(seconds=60)) if rpm else now + timedelta(seconds=60))
    total = sum(tokens for _, tokens in recent)
    tpm = limits["tokens_per_minute"]
    required = max(1, estimated_tokens)
    if total + required > tpm:
        for at, tokens in recent:
            total -= tokens
            if total + required <= tpm:
                deadlines.append(at + timedelta(seconds=60))
                break
        else:
            deadlines.append(now + timedelta(seconds=60))
    if deadlines:
        return Decision(False, "minute", max(deadlines))
    return Decision(True)


async def connection():
    return await psycopg.AsyncConnection.connect(require_database_url(), connect_timeout=5,
                                                options="-c statement_timeout=5000")


async def check(settings, estimated_tokens=0, message_id=None):
    """Check, and optionally atomically reserve one HTTP attempt."""
    async with await connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        now = (await (await conn.execute("SELECT now()")).fetchone())[0]
        row = await (await conn.execute(
            "SELECT blocked_until,reason,observed_limits FROM llm_quota_blocks WHERE model=%s",
            (settings.model,))).fetchone()
        if row and row[0] > now:
            return Decision(False, row[1], row[0])
        daily = (await (await conn.execute(
            "SELECT count(*) FROM llm_api_attempts WHERE model=%s AND attempted_at>=%s",
            (settings.model, day_bounds(now)[0]))).fetchone())[0]
        recent = await (await conn.execute(
            "SELECT attempted_at,input_tokens FROM llm_api_attempts WHERE model=%s AND attempted_at>%s ORDER BY attempted_at",
            (settings.model, now - timedelta(seconds=60)))).fetchall()
        result = decide(now, recent, daily, settings, estimated_tokens, row[2] if row else None)
        if result.allowed and message_id is not None:
            await conn.execute("INSERT INTO llm_api_attempts(message_id,model,input_tokens) VALUES (%s,%s,%s)",
                               (message_id, settings.model, estimated_tokens))
            await conn.execute("UPDATE llm_requests SET attempts=attempts+1,last_attempt_at=now() WHERE message_id=%s", (message_id,))
            # Only request metadata expires; private source material is never touched.
            await conn.execute("""DELETE FROM llm_requests WHERE message_id IN
                (SELECT message_id FROM llm_requests WHERE created_at < now()-interval '7 days'
                 AND (last_attempt_at IS NULL OR last_attempt_at < now()-interval '2 days') LIMIT 1000)""")
        return result


def parse_quota_error(status, data, headers=None, now=None):
    error = data.get("error", {}) if isinstance(data, dict) else {}
    if not isinstance(error, dict):
        error = {}
    if status != 429 and error.get("status") != "RESOURCE_EXHAUSTED":
        return None
    now = now or datetime.now(timezone.utc)
    delay = 0.0
    descriptions = []
    observed = {}
    details = error.get("details", [])
    for item in details if isinstance(details, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("@type", "")).endswith("RetryInfo"):
            match = re.fullmatch(r"(\d+(?:\.\d+)?)s", str(item.get("retryDelay", "")))
            if match:
                delay = max(delay, min(float(match[1]), 172800))
        if str(item.get("@type", "")).endswith("QuotaFailure"):
            violations = item.get("violations", [])
            for violation in violations if isinstance(violations, list) else []:
                if not isinstance(violation, dict):
                    continue
                name = str(violation.get("quotaId", "")) + " " + str(violation.get("quotaMetric", ""))
                name = name.lower()
                descriptions.append(name)
                field = None
                if "request" in name:
                    if re.search(r"per.?day|daily", name):
                        field = "requests_per_day"
                    elif re.search(r"per.?minute", name):
                        field = "requests_per_minute"
                elif "token" in name and re.search(r"per.?minute", name):
                    field = "tokens_per_minute"
                value = str(violation.get("quotaValue", ""))
                if field and value.isdigit():
                    observed[field] = int(value)
    retry_after = (headers or {}).get("retry-after", "")
    try:
        retry = float(retry_after)
        if math.isfinite(retry):
            delay = max(delay, min(max(0, retry), 172800))
    except (TypeError, ValueError):
        try:
            delay = max(delay, min((parsedate_to_datetime(retry_after) - now).total_seconds(), 172800))
        except (TypeError, ValueError, OverflowError):
            pass
    text = " ".join(descriptions) or str(error.get("message", "")).lower()
    daily = bool(re.search(r"per.?day|daily", text))
    reason = "provider_day" if daily else ("provider_minute" if re.search(r"per.?minute", text) else "provider_unknown")
    until = now + timedelta(seconds=max(1, delay or 60))
    if daily:
        until = max(until, day_bounds(now)[1])
    return QuotaError(reason, until, observed, status=429)


async def remember(settings, error):
    async with await connection() as conn:
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK,))
        now = (await (await conn.execute("SELECT now()")).fetchone())[0]
        previous = await (await conn.execute("SELECT strikes FROM llm_quota_blocks WHERE model=%s", (settings.model,))).fetchone()
        strikes = min(7, (previous[0] if previous else 0) + 1)
        until = error.until
        if error.reason == "provider_unknown":
            until = max(until, now + timedelta(seconds=min(3600, 60 * 2 ** (strikes - 1))))
        await conn.execute("""INSERT INTO llm_quota_blocks(model,blocked_until,reason,strikes,observed_limits)
            VALUES (%s,%s,%s,%s,%s::jsonb) ON CONFLICT(model) DO UPDATE SET
            blocked_until=greatest(llm_quota_blocks.blocked_until,excluded.blocked_until),
            reason=excluded.reason,strikes=excluded.strikes,
            observed_limits=llm_quota_blocks.observed_limits || excluded.observed_limits""",
                           (settings.model, until, error.reason, strikes, json.dumps(error.observed)))


async def record_usage(message_id, usage):
    tokens = usage.get("promptTokenCount")
    if type(tokens) is not int or tokens < 0:
        return
    async with await connection() as conn:
        await conn.execute("""UPDATE llm_api_attempts SET input_tokens=%s WHERE id=
            (SELECT id FROM llm_api_attempts WHERE message_id=%s ORDER BY id DESC LIMIT 1)""", (tokens, message_id))
        await conn.execute("""UPDATE llm_quota_blocks SET strikes=0 WHERE blocked_until<=now()
            AND model=(SELECT model FROM llm_requests WHERE message_id=%s)""", (message_id,))

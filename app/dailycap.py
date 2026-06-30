"""Daily cap (guardrail #3): a single global request ceiling backed by Redis.

This is a different bound from the per-client rate limit. The rate limit asks
whether one client is calling too often, so it protects fairness between callers.
The daily cap asks whether the whole service has spent enough for one day, so it
protects total provider spend (here, a free token budget) across everyone
combined. There's one counter, shared by all callers.

There's no Lua script here, unlike the rate limiter. Spending a daily ticket is a
single INCR, and INCR is already atomic on the Redis server, so two workers
incrementing at the same instant can't lose a count. The token bucket needed Lua
only because its spend was three steps (read, refill, write) that could interleave.
A lone INCR has nothing to interleave with.

The key embeds today's UTC date (dailycap:2026-06-30), so at UTC midnight requests
naturally start landing on a fresh key that begins at 0. Note that "day" here means
UTC day, not the caller's local day.

We set EXPIRE only on the first increment. INCR creates the key on the first call
of the day; if we re-set EXPIRE on every call, the window would keep sliding forward
and the key would never die. Setting it once, when the count comes back as 1, pins
the key's lifetime to the first request of the day.
"""
from datetime import datetime, timedelta, timezone
from math import ceil

from upstash_redis import Redis

# How many model-hitting requests the whole service allows per UTC day. Lower it to
# demo the cap tripping, raise it for real use. It guards the free token budget
# across all callers combined, not per client.
DAILY_LIMIT = 200

# Give the key a little more than a day so it comfortably outlives its own UTC day
# even with clock skew, then lets Redis clean it up. The next day already uses a
# different key, so a lingering old one is harmless either way.
KEY_TTL_SECONDS = 60 * 60 * 25  # 25 hours

redis = Redis.from_env()  # same UPSTASH_REDIS_REST_URL / _TOKEN as the rate limiter


def _seconds_until_utc_midnight() -> int:
    """Seconds from now until the next UTC midnight, when today's key rolls over and
    the count starts fresh. This is the only honest "come back at" value for the
    daily cap: unlike the rate limiter, which hands a ticket back in seconds, the
    budget only refills when the calendar day turns over.
    """
    now = datetime.now(timezone.utc)
    next_midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return ceil((next_midnight - now).total_seconds())


def check_daily_cap() -> dict:
    """Count one request against today's global budget. Returns {allowed, count, retry_after}.

    Builds today's UTC-dated key, INCRs it, and on the first request of the day sets
    an expiry so the key cleans itself up. `allowed` is False once the day's count
    goes past DAILY_LIMIT; `retry_after` is seconds until the cap resets.
    """
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    key = f"dailycap:{today}"

    count = redis.incr(key)
    if count == 1:
        # the first request of the day just created the key, so pin its lifetime now
        redis.expire(key, KEY_TTL_SECONDS)

    return {
        "allowed": count <= DAILY_LIMIT,
        "count": count,
        "retry_after": _seconds_until_utc_midnight(),
    }

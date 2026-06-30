"""Rate limiting (guardrail #2): a per-client token bucket backed by Redis.

This bounds how *often* one client can call, which is a different concern from
max_tokens (how *long* one answer can be). A "token" here is a permission ticket,
one per request, and has nothing to do with LLM tokens.

Each client gets a bucket holding up to CAPACITY tickets that refills at a steady
rate. It tolerates a short burst, up to capacity, then meters the client to the
sustained rate. That's friendlier to real bursty clients than a fixed window, which
resets on a clock and can be gamed at the boundary, or a plain sliding window, which
punishes any quick flurry.

The state lives in Redis rather than an in-memory counter, because an in-memory
counter breaks the moment the app runs more than one worker process: each worker
keeps its own count and the real limit multiplies by the worker count. Redis gives
every worker one shared bucket.

The Lua script is what makes spending a ticket safe. Spending is three steps (read
the tickets left, refill and decide, write the new count) and across workers those
steps can interleave: two workers both read "1 left", both allow, both write "0",
and two requests slip through on one ticket. A Lua script runs atomically on the
Redis server, so the whole read-refill-write happens as one indivisible step and two
workers can't spend the same ticket.
"""
import time

from upstash_redis import Redis

# Bucket policy. CAPACITY is the burst size; REFILL_RATE is the sustained rate.
# 10 requests/minute sustained, with room to burst 10 at once.
CAPACITY = 10
REQUESTS_PER_MINUTE = 10
REFILL_RATE = REQUESTS_PER_MINUTE / 60.0  # tickets added per second

redis = Redis.from_env()  # reads UPSTASH_REDIS_REST_URL / _TOKEN from the environment

# The bucket lives in a Redis hash per client: {tokens, ts}. This script does the
# whole refill-then-spend atomically and returns whether the request is allowed,
# plus how many seconds until the next ticket (for the Retry-After header).
#
# KEYS[1] = the client's bucket key
# ARGV    = capacity, refill_rate (tokens/sec), now (unix seconds)
TOKEN_BUCKET = """
local key = KEYS[1]
local capacity = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])
local now = tonumber(ARGV[3])

-- load this client's bucket; if it's never been seen, start full
local bucket = redis.call('HMGET', key, 'tokens', 'ts')
local tokens = tonumber(bucket[1])
local ts = tonumber(bucket[2])
if tokens == nil then
    tokens = capacity
    ts = now
end

-- refill: add the tickets earned since last time, but never overflow capacity
local elapsed = now - ts
if elapsed < 0 then elapsed = 0 end          -- guard against any clock skew
tokens = math.min(capacity, tokens + elapsed * refill_rate)

-- decide and spend
local allowed = 0
if tokens >= 1 then
    tokens = tokens - 1
    allowed = 1
end

-- save the new state, and let an idle bucket clean itself up once it would be
-- full again anyway (capacity / refill_rate seconds = time to refill from empty)
redis.call('HSET', key, 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', key, math.ceil((capacity / refill_rate) * 1000))

-- if blocked, how long until one ticket trickles back
local retry_after = 0
if allowed == 0 then
    retry_after = (1 - tokens) / refill_rate
end

-- Redis truncates float returns to ints, so hand floats back as strings
return {allowed, tostring(retry_after)}
"""


def check_rate_limit(client_id: str) -> dict:
    """Try to spend one ticket for `client_id`. Returns {allowed, retry_after}.

    `retry_after` is seconds until the next ticket (0 when allowed). The Lua script
    makes the whole check atomic, so concurrent workers can't oversell the bucket.
    """
    now = time.time()
    allowed, retry_after = redis.eval(
        TOKEN_BUCKET,
        keys=[f"ratelimit:{client_id}"],
        args=[str(CAPACITY), str(REFILL_RATE), str(now)],
    )
    return {"allowed": allowed == 1, "retry_after": float(retry_after)}

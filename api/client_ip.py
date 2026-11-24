"""The client address to rate-limit and lock out on.

uvicorn runs with --forwarded-allow-ips='*', which makes request.client.host the
*first* X-Forwarded-For entry: a value the caller chooses. Render's proxy appends the
real address as the *last* entry, so that is the one to trust.
"""


def client_ip(request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        last = forwarded.split(",")[-1].strip()
        if last:
            return last
    return request.client.host if request.client else "unknown"

"""
Mutual TLS (mTLS) configuration reference for production deployment.

NOT wired into api/webhook_server.py by default — mTLS needs real
certificates (a CA cert, a server cert/key, and the portal's client cert)
that don't exist in this environment, so this is documentation plus a ready
-to-use config function, not something you can exercise here.

In practice, mTLS between the web portal and this backend is usually
terminated one of two places:

1. **At a reverse proxy / load balancer** (nginx, AWS ALB, Envoy) sitting in
   front of this FastAPI app — the proxy verifies the portal's client
   certificate and forwards plain HTTPS (or HTTP on a private network) to
   uvicorn. This is the more common production pattern and requires no
   Python-side changes at all.

2. **Directly in uvicorn**, if you're not using a proxy — uvicorn (built on
   Python's `ssl` module) can require and verify client certificates itself.
   Use `uvicorn_mtls_kwargs()` below to get the right arguments.

Usage (once you have real certs):

    from api.mtls_config import uvicorn_mtls_kwargs
    import uvicorn

    uvicorn.run(
        "api.webhook_server:app",
        host="0.0.0.0", port=8443,
        **uvicorn_mtls_kwargs(
            certfile="/etc/certs/server.crt",
            keyfile="/etc/certs/server.key",
            ca_certfile="/etc/certs/portal-ca.crt",
        ),
    )
"""

import ssl


def uvicorn_mtls_kwargs(certfile: str, keyfile: str, ca_certfile: str) -> dict:
    """Returns the uvicorn.run() kwargs needed to require and verify a
    client certificate signed by the portal's CA on every connection."""
    return {
        "ssl_certfile": certfile,
        "ssl_keyfile": keyfile,
        "ssl_ca_certs": ca_certfile,
        "ssl_cert_reqs": ssl.CERT_REQUIRED,
    }

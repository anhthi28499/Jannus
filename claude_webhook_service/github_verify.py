import hashlib
import hmac


def verify_github_signature(body: bytes, secret: str, signature_header: str | None) -> bool:
    """Validate GitHub `X-Hub-Signature-256` (sha256=...)."""
    if not secret:
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    digest = signature_header.removeprefix("sha256=")
    mac = hmac.new(secret.encode("utf-8"), msg=body, digestmod=hashlib.sha256)
    expected = mac.hexdigest()
    return hmac.compare_digest(expected, digest)

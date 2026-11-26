"""Recusa autenticação iniciada em outro site, preservando clientes não navegador."""

from urllib.parse import urlsplit

from fastapi import HTTPException, Request


def authority(value):
    parsed = urlsplit(value)
    if parsed.scheme not in ('https', 'http') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError()
    if parsed.path not in ('', '/') or parsed.query or parsed.fragment:
        raise ValueError()
    return parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)


async def require_same_origin(request: Request):
    if request.headers.get('sec-fetch-site', '').lower() == 'cross-site':
        raise HTTPException(403, 'Cross-site authentication forbidden')
    origin = request.headers.get('origin')
    if origin is not None:
        try:
            expected = authority(str(request.base_url))
            if authority(origin) != expected:
                raise ValueError()
        except ValueError:
            raise HTTPException(403, 'Cross-site authentication forbidden') from None

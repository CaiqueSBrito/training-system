"""Limite ASGI antes de FastAPI carregar/parsear o JSON de POST /chat."""
from starlette.responses import JSONResponse
from starlette._utils import get_route_path


class ChatBodyLimit:
    MAX_BYTES = 256 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or get_route_path(scope) != '/chat' or scope.get('method') != 'POST':
            return await self.app(scope, receive, send)
        async def reject():
            await JSONResponse({'detail': 'Corpo excede 256 KiB'}, status_code=413)(scope, receive, send)
        for key, value in scope.get('headers', []):
            if key.lower() == b'content-length':
                try:
                    if int(value) > self.MAX_BYTES:
                        return await reject()
                except ValueError:
                    pass  # Mesmo sem tamanho confiável, conta os bytes recebidos.
        buffer, size = bytearray(), 0
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            size += len(message.get('body', b''))
            if size > self.MAX_BYTES:
                return await reject()
            buffer.extend(message.get('body', b''))
            if not message.get('more_body', False):
                break
        body, first = bytes(buffer), True
        async def limited_receive():
            nonlocal first
            if first:
                first = False
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        await self.app(scope, limited_receive, send)

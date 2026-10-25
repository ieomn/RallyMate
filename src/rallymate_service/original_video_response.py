"""Original-video-only compatibility for unsatisfiable byte-range headers."""
from __future__ import annotations

from starlette.responses import FileResponse
from starlette.types import Message, Receive, Scope, Send


class OriginalVideoResponse(FileResponse):
    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def send_with_range_unit(message: Message) -> None:
            if message["type"] == "http.response.start" and message["status"] == 416:
                # Starlette 0.52.1 emits */<size>, with the size from its actual
                # file stat. Preserve that size and add the missing bytes unit.
                # Already-correct headers and all other responses pass through.
                message = {
                    **message,
                    "headers": [
                        (name, b"bytes " + value)
                        if name.lower() == b"content-range" and value.startswith(b"*/") and value[2:].isdigit()
                        else (name, value)
                        for name, value in message["headers"]
                    ],
                }
            await send(message)

        await super().__call__(scope, receive, send_with_range_unit)

"""WebSocket fan-out (manual §3.17, §3.22).

The processor runs in a thread; the WebSocket handlers run on the asyncio loop. `publish` is
thread-safe: it hands the message to the loop. Each client has a BOUNDED queue; when it is full the
OLDEST message is dropped, so one slow browser can never grow server memory without limit.
"""

import asyncio
from typing import Any


class Client:
    def __init__(self, maxsize: int):
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0


class Hub:
    def __init__(self, maxsize: int = 200):
        self.maxsize = maxsize
        self.clients: set[Client] = set()
        self.loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        self.loop = loop

    def connect(self) -> Client:
        c = Client(self.maxsize)
        self.clients.add(c)
        return c

    def disconnect(self, c: Client) -> None:
        self.clients.discard(c)

    def publish(self, msg: dict[str, Any]) -> None:
        """Safe to call from any thread."""
        if self.loop is None or not self.clients:
            return
        try:
            self.loop.call_soon_threadsafe(self._fanout, msg)
        except RuntimeError:  # loop already closed during shutdown
            pass

    def _fanout(self, msg: dict[str, Any]) -> None:
        for c in list(self.clients):
            if c.queue.full():
                c.queue.get_nowait()  # drop the oldest: the newest state matters most
                c.dropped += 1
            c.queue.put_nowait(msg)

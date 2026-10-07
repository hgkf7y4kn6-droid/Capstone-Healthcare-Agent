"""
Live status sync to the web portal, part 3: WebSockets.

For the BackgroundTasks fallback mode (no Celery), the task runs in the
same process as the FastAPI app, so broadcasting to connected browser
clients is a simple in-memory fan-out.

For real Celery deployments, the worker runs in a separate process and
can't reach this process's in-memory connections directly — a production
setup would bridge this via Redis pub/sub (the worker publishes, this
process subscribes and forwards to its WebSocket clients). That bridge
isn't wired up here since it needs a live Redis instance to test against;
the webhook and SQS sync (integrations/) are the reliable cross-process
channels — treat WebSocket updates as a same-process nice-to-have on top
of those, not the primary delivery mechanism.
"""

import asyncio
from collections import defaultdict


class ConnectionManager:
    def __init__(self):
        self._connections: dict[int, list] = defaultdict(list)
        self._loop: asyncio.AbstractEventLoop | None = None

    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._loop = loop

    async def connect(self, patient_id: int, websocket):
        await websocket.accept()
        self._connections[patient_id].append(websocket)

    def disconnect(self, patient_id: int, websocket):
        if websocket in self._connections.get(patient_id, []):
            self._connections[patient_id].remove(websocket)

    async def broadcast(self, patient_id: int, message: dict):
        dead = []
        for ws in self._connections.get(patient_id, []):
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(patient_id, ws)


manager = ConnectionManager()


def broadcast_status_sync(patient_id: int, message: dict):
    """Called from synchronous task code (tasks/refill_tasks.py). Schedules
    the async broadcast onto the running event loop if one was registered;
    a no-op if the app hasn't set one up (e.g. running outside FastAPI)."""
    if manager._loop is None:
        return
    try:
        asyncio.run_coroutine_threadsafe(manager.broadcast(patient_id, message), manager._loop)
    except RuntimeError:
        pass

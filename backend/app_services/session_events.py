"""In-process WebSocket notifications for client-session changes."""

from fastapi import WebSocket


class SessionEventHub:
    def __init__(self) -> None:
        self._connections: dict[int, tuple[int, set[WebSocket]]] = {}

    async def connect(self, user_id: int, session_id: int, websocket: WebSocket) -> None:
        if session_id not in self._connections:
            self._connections[session_id] = (user_id, set())
        self._connections[session_id][1].add(websocket)

    def disconnect(self, session_id: int, websocket: WebSocket) -> None:
        entry = self._connections.get(session_id)
        if entry is None:
            return
        entry[1].discard(websocket)
        if not entry[1]:
            self._connections.pop(session_id, None)

    async def notify_user(self, user_id: int, reason: str) -> None:
        targets = [
            (session_id, websocket)
            for session_id, (connected_user_id, sockets) in self._connections.items()
            if connected_user_id == user_id
            for websocket in tuple(sockets)
        ]
        for session_id, websocket in targets:
            try:
                await websocket.send_json({"type": "session_revoked", "reason": reason})
                await websocket.close(code=4403, reason="Client session revoked")
            except Exception:
                pass
            self.disconnect(session_id, websocket)

    async def push_user_event(self, user_id: int, payload: dict) -> None:
        targets = [
            websocket
            for connected_user_id, sockets in self._connections.values()
            if connected_user_id == user_id
            for websocket in tuple(sockets)
        ]
        for websocket in targets:
            try:
                await websocket.send_json(payload)
            except Exception:
                pass


session_event_hub = SessionEventHub()

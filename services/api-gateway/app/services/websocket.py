"""WebSocket connection manager for real-time updates."""

import json
from typing import List
from fastapi import WebSocket


class WebSocketManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        dead = []
        for connection in self.active_connections:
            try:
                await connection.send_text(json.dumps(message, default=str))
            except Exception:
                dead.append(connection)
        for conn in dead:
            self.disconnect(conn)

    async def broadcast_event(self, event_type: str, job_id: str, data: dict = None):
        await self.broadcast({
            "type": event_type,
            "job_id": job_id,
            "data": data or {},
        })


ws_manager = WebSocketManager()

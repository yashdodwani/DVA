import asyncio
import json
from typing import Set
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from config_loader import config

app = FastAPI(title="DVA ADAS Co-Pilot")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Shared state — populated by main.py before the server starts
# ---------------------------------------------------------------------------
_actuation_log = None   # ActuationLog instance
_can_bus = None         # CanBusSimulator instance
_decider = None         # Decider instance (for current_ear)

_ws_clients: Set[WebSocket] = set()


def init(actuation_log, can_bus, decider):
    global _actuation_log, _can_bus, _decider
    _actuation_log = actuation_log
    _can_bus       = can_bus
    _decider       = decider


# ---------------------------------------------------------------------------
# Broadcaster — called by all modules whenever they emit an event
# ---------------------------------------------------------------------------
async def broadcast(event_dict: dict):
    """Send an event to all connected WebSocket clients and store in ring buffer."""
    if _actuation_log is not None:
        _actuation_log.record_raw(event_dict)

    if not _ws_clients:
        return

    payload = json.dumps(event_dict)
    dead: Set[WebSocket] = set()
    for ws in list(_ws_clients):
        try:
            await ws.send_text(payload)
        except Exception:
            dead.add(ws)
    _ws_clients -= dead


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------
@app.websocket("/ws/events")
async def ws_events(websocket: WebSocket):
    await websocket.accept()
    _ws_clients.add(websocket)
    try:
        while True:
            # Keep connection alive; client drives nothing inbound
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)


# ---------------------------------------------------------------------------
# REST endpoints
# ---------------------------------------------------------------------------
@app.get("/api/state")
async def get_state():
    ear = round(_decider.current_ear, 4) if _decider else 0.0
    episode = _decider._episode_id if _decider else None
    last_10 = _actuation_log.get_last_n(10) if _actuation_log else []
    can_snap = _can_bus.get_latest_reading().model_dump() if _can_bus else {}
    return {
        "current_ear": ear,
        "active_episode": episode,
        "last_10_events": last_10,
        "latest_can_reading": can_snap,
        "system_status": "MONITORING",
    }


@app.get("/api/history")
async def get_history():
    if _actuation_log is None:
        return []
    return _actuation_log.get_history()

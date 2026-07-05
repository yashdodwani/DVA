import asyncio
import json
import threading
from pathlib import Path
from typing import Optional, Set
import cv2
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from config_loader import config

_DASHBOARD_PATH = Path(__file__).parent / "dashboard.html"

# Latest BGR frame written by the vision thread; read by /video_feed
_latest_frame: Optional[bytes] = None  # JPEG bytes
_frame_lock = threading.Lock()


def set_latest_frame(bgr_frame) -> None:
    """Called from the vision thread with each raw BGR frame."""
    global _latest_frame
    ok, buf = cv2.imencode(".jpg", bgr_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    if ok:
        with _frame_lock:
            _latest_frame = buf.tobytes()

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
    _ws_clients.difference_update(dead)


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


# ---------------------------------------------------------------------------
# Demo control endpoints (for dashboard scenario buttons)
# ---------------------------------------------------------------------------
@app.post("/api/scenario/{scenario}")
async def set_scenario(scenario: str):
    if _can_bus is None:
        return {"error": "CAN bus not initialized"}
    s = scenario.upper()
    if s == "A":
        _can_bus.set_override(speed=65.0, steering=0.5, brake="OFF")
        label = "Scenario A — genuine drowsiness (EXECUTE expected)"
    elif s == "B":
        _can_bus.set_override(speed=65.0, steering=0.5, brake="OFF")
        label = "Scenario B — head-turn false trigger (SUPPRESSED expected)"
    elif s == "C":
        _can_bus.set_override(speed=55.0, steering=0.0, brake="HARD_BRAKING")
        label = "Scenario C — hard-braking false trigger (SUPPRESSED expected)"
    else:
        return {"error": f"Unknown scenario '{scenario}'"}
    return {"status": "ok", "scenario": s, "label": label}


@app.post("/api/reset")
async def reset_scenario():
    if _can_bus:
        _can_bus.clear_override()
    return {"status": "ok", "label": "Normal simulation resumed"}


# ---------------------------------------------------------------------------
# MJPEG video feed — streams the backend webcam so the browser doesn't need
# its own camera access (avoids the Linux V4L2 exclusive-lock issue).
# ---------------------------------------------------------------------------
async def _mjpeg_generator():
    boundary = b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
    while True:
        with _frame_lock:
            frame = _latest_frame
        if frame:
            yield boundary + frame + b"\r\n"
        await asyncio.sleep(1 / 30)  # ~30 fps cap


@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(
        _mjpeg_generator(),
        media_type="multipart/x-mixed-replace; boundary=frame",
    )


# ---------------------------------------------------------------------------
# Dashboard — served directly so no separate file server is needed.
# ---------------------------------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    host = request.url.hostname
    if host and host not in ("localhost", "127.0.0.1"):
        return RedirectResponse(
            url=f"http://localhost:{request.url.port or 8000}/",
            status_code=302,
        )
    return FileResponse(_DASHBOARD_PATH)

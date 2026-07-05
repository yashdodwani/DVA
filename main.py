"""
DVA ADAS Co-Pilot — Entry Point
================================
Usage:
    python main.py [OPTIONS]

Options:
    --scenario A   Force CAN bus to Scenario A (speed>20, OFF brakes, steering≈0)
    --scenario B   Force CAN bus to Scenario B (speed>20, OFF brakes, steering≈0)
                   (Verifier will block on head-pose; no CAN override needed for B)
    --scenario C   Force CAN bus to Scenario C (HARD_BRAKING → Verifier blocks)
    --reset        Clear any active scenario override and return to normal simulation
    --no-cam       Run without webcam (demo/test mode — logs events only)

Hotkeys (while running, focus the terminal):
    a  →  activate Scenario A override
    b  →  activate Scenario B override (head-pose; no CAN change needed)
    c  →  activate Scenario C override
    r  →  reset CAN bus to normal simulation
    q  →  quit
"""

import argparse
import asyncio
import sys
import time
import threading
from pathlib import Path
import cv2
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
import uvicorn

_MODEL_PATH = str(Path(__file__).parent / "face_landmarker.task")

from config_loader import config
from can_bus_simulator import CanBusSimulator
from decider import Decider
from verifier import Verifier
from actuation_log import ActuationLog
import server as srv

# ---------------------------------------------------------------------------
# Globals
# ---------------------------------------------------------------------------
_loop: asyncio.AbstractEventLoop = None


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------
def _apply_scenario(can_bus: CanBusSimulator, scenario: str):
    scenario = scenario.upper()
    if scenario == "A":
        # Genuine drowsiness — CAN bus should approve
        can_bus.set_override(speed=65.0, steering=0.5, brake="OFF")
        print("[SCENARIO A] CAN override: speed=65, steering=0.5, brake=OFF")
    elif scenario == "B":
        # Head-turn block — CAN bus is fine, block comes from head pose
        can_bus.set_override(speed=65.0, steering=0.5, brake="OFF")
        print("[SCENARIO B] CAN override: speed=65, steering=0.5, brake=OFF  "
              "(turn your head to trigger Verifier block)")
    elif scenario == "C":
        # Hard-braking block — CAN forces HARD_BRAKING
        can_bus.set_override(speed=55.0, steering=0.0, brake="HARD_BRAKING")
        print("[SCENARIO C] CAN override: speed=55, steering=0.0, brake=HARD_BRAKING")
    else:
        print(f"[WARN] Unknown scenario '{scenario}', ignoring.")


# ---------------------------------------------------------------------------
# Vision processing loop (runs in thread, posts coroutines to main loop)
# ---------------------------------------------------------------------------
def _vision_thread(can_bus: CanBusSimulator, decider: Decider,
                   verifier: Verifier, actuation_log: ActuationLog,
                   no_cam: bool):
    # Build FaceLandmarker using the Tasks API (mediapipe >= 0.10)
    base_options = mp_python.BaseOptions(model_asset_path=_MODEL_PATH)
    options = mp_vision.FaceLandmarkerOptions(
        base_options=base_options,
        running_mode=mp_vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    face_landmarker = mp_vision.FaceLandmarker.create_from_options(options)

    cap = None
    if not no_cam:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            print("[ERROR] Cannot open webcam — falling back to no-cam mode.")
            no_cam = True

    print("[VISION] Processing loop started.")
    frame_w, frame_h = 640, 480
    frame_ts_ms = 0  # monotonic timestamp in ms required by VIDEO mode

    try:
        while True:
            if no_cam:
                asyncio.run_coroutine_threadsafe(_no_face_tick(), _loop).result(timeout=1.0)
                time.sleep(1 / 15)
                continue

            ret, frame = cap.read()
            if not ret:
                continue

            frame_h, frame_w = frame.shape[:2]
            frame_ts_ms = int(time.monotonic() * 1000)

            # Push raw frame to the MJPEG feed before any processing
            srv.set_latest_frame(frame)

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = face_landmarker.detect_for_video(mp_image, frame_ts_ms)

            if not result.face_landmarks:
                # No face detected — skip gracefully
                continue

            landmarks = result.face_landmarks[0]  # list of NormalizedLandmark

            future = asyncio.run_coroutine_threadsafe(
                _process_frame(landmarks, frame_w, frame_h,
                               can_bus, decider, verifier, actuation_log),
                _loop,
            )
            try:
                future.result(timeout=0.5)
            except Exception as exc:
                print(f"[VISION] Frame processing error: {exc}")

    finally:
        face_landmarker.close()
        if cap:
            cap.release()
        print("[VISION] Loop stopped.")


async def _no_face_tick():
    """Idle tick when no camera is available."""
    pass


async def _process_frame(landmarks, frame_w: int, frame_h: int,
                         can_bus: CanBusSimulator, decider: Decider,
                         verifier: Verifier, actuation_log: ActuationLog):
    decider_evt = await decider.process_frame(landmarks)
    actuation_log.set_current_ear(decider.current_ear)

    if decider_evt is not None:
        actuation_log.set_active_episode(decider_evt.episode_id)
        verifier_evt = await verifier.verify(
            decider_evt, landmarks, frame_w, frame_h
        )
        await actuation_log.process(decider_evt, verifier_evt)
    else:
        actuation_log.set_active_episode(decider._episode_id)


# ---------------------------------------------------------------------------
# Keyboard hotkey loop (runs in its own thread, non-blocking)
# ---------------------------------------------------------------------------
def _hotkey_thread(can_bus: CanBusSimulator):
    """
    Read single keypresses from stdin.  Works in a standard terminal.
    Falls back gracefully if stdin is not a tty.
    """
    import sys, tty, termios

    if not sys.stdin.isatty():
        return  # Non-interactive — skip hotkey handling

    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    print("[HOTKEYS] a=ScenA  b=ScenB  c=ScenC  r=reset  q=quit")
    try:
        tty.setraw(fd)
        while True:
            ch = sys.stdin.read(1).lower()
            if ch == 'q':
                print("\n[HOTKEYS] Quit requested.")
                asyncio.run_coroutine_threadsafe(_shutdown(), _loop)
                break
            elif ch == 'a':
                _apply_scenario(can_bus, "A")
            elif ch == 'b':
                _apply_scenario(can_bus, "B")
            elif ch == 'c':
                _apply_scenario(can_bus, "C")
            elif ch == 'r':
                can_bus.clear_override()
                print("\n[HOTKEYS] CAN bus override cleared — back to normal simulation.")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


async def _shutdown():
    asyncio.get_event_loop().stop()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def parse_args():
    parser = argparse.ArgumentParser(description="DVA ADAS Co-Pilot")
    parser.add_argument("--scenario", choices=["A", "B", "C", "a", "b", "c"],
                        help="Force CAN bus to a demo scenario on startup.")
    parser.add_argument("--reset", action="store_true",
                        help="Clear CAN bus override on startup.")
    parser.add_argument("--no-cam", action="store_true",
                        help="Run without webcam (CAN bus + server only).")
    return parser.parse_args()


async def main_async(args):
    global _loop
    _loop = asyncio.get_running_loop()

    # --- Instantiate modules ---
    can_bus       = CanBusSimulator()
    actuation_log = ActuationLog(event_emitter=srv.broadcast)
    decider       = Decider(event_emitter=srv.broadcast)
    verifier      = Verifier(can_bus=can_bus, event_emitter=srv.broadcast)

    # Subscribe CAN bus events to the broadcaster
    async def _can_bus_handler(evt):
        await srv.broadcast(evt.model_dump())

    can_bus.subscribe(_can_bus_handler)

    # Wire the server
    srv.init(actuation_log, can_bus, decider)

    # --- Apply startup scenario / reset ---
    if args.reset:
        can_bus.clear_override()
    elif args.scenario:
        _apply_scenario(can_bus, args.scenario.upper())

    # --- Start CAN bus ---
    can_bus.start()

    # --- Start vision thread ---
    vision_t = threading.Thread(
        target=_vision_thread,
        args=(can_bus, decider, verifier, actuation_log, args.no_cam),
        daemon=True,
        name="vision-thread",
    )
    vision_t.start()

    # --- Start hotkey thread ---
    hotkey_t = threading.Thread(
        target=_hotkey_thread,
        args=(can_bus,),
        daemon=True,
        name="hotkey-thread",
    )
    hotkey_t.start()

    # --- Start FastAPI server ---
    host = config["server"]["host"]
    port = config["server"]["port"]
    print(f"[SERVER] Open dashboard at http://localhost:{port}  (binding {host}:{port})")
    uvi_config = uvicorn.Config(
        app=srv.app,
        host=host,
        port=port,
        log_level="warning",
    )
    uvi_server = uvicorn.Server(uvi_config)
    await uvi_server.serve()

    # Cleanup
    can_bus.stop()


def main():
    args = parse_args()
    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        print("\n[DVA] Shutting down.")


if __name__ == "__main__":
    main()

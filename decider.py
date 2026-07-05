import uuid
from collections import deque
from typing import Optional, Callable, Awaitable
import numpy as np
from models import DeciderEvent
from config_loader import config

# MediaPipe Face Mesh landmark indices for each eye (standard 6-point EAR set)
LEFT_EYE_IDX  = [33, 160, 158, 133, 153, 144]
RIGHT_EYE_IDX = [362, 385, 387, 263, 373, 380]


def _ear(landmarks, eye_indices) -> float:
    """Compute Eye Aspect Ratio from 6 landmark points."""
    pts = [np.array([landmarks[i].x, landmarks[i].y]) for i in eye_indices]
    # pts[0] = P1 (inner corner), pts[3] = P4 (outer corner)
    # pts[1] = P2 (top-left), pts[2] = P3 (top-right)
    # pts[5] = P6 (bottom-left), pts[4] = P5 (bottom-right)
    A = np.linalg.norm(pts[1] - pts[5])
    B = np.linalg.norm(pts[2] - pts[4])
    C = np.linalg.norm(pts[0] - pts[3])
    return (A + B) / (2.0 * C)


class Decider:
    def __init__(self, event_emitter: Callable[[dict], Awaitable[None]]):
        self._emit = event_emitter
        cfg = config["decider"]
        self.ear_threshold: float     = cfg["ear_threshold"]
        self.window: int              = cfg["window_frames"]
        self.escalation_window: int   = cfg["escalation_window_frames"]
        self.cooldown_frames: int     = cfg["cooldown_frames"]
        self.heartbeat_every: int     = cfg["heartbeat_every_n_frames"]

        # Rolling EAR buffer (True = below threshold, False = above)
        self._ear_buffer: deque = deque(maxlen=self.escalation_window)
        self._cooldown_counter: int = 0
        self._frame_count: int = 0
        self._current_ear: float = 1.0

        # Episode state
        self._episode_id: Optional[str] = None
        self._episode_proposed: bool = False   # True once first action proposed this episode
        self._episode_escalated: bool = False  # True once escalation proposed

    @property
    def current_ear(self) -> float:
        return self._current_ear

    async def process_frame(self, landmarks) -> Optional[DeciderEvent]:
        """
        Process one frame's landmarks. Returns a DeciderEvent if an action is
        proposed, otherwise None.  Also emits a heartbeat on the configured cadence.
        """
        self._frame_count += 1

        # Compute EAR
        left  = _ear(landmarks, LEFT_EYE_IDX)
        right = _ear(landmarks, RIGHT_EYE_IDX)
        avg_ear = round((left + right) / 2.0, 4)
        self._current_ear = avg_ear

        below = avg_ear < self.ear_threshold
        self._ear_buffer.append(below)

        # --- Cooldown management ---
        if not below:
            self._cooldown_counter += 1
            if self._episode_id and self._cooldown_counter >= self.cooldown_frames:
                # Episode over
                self._episode_id = None
                self._episode_proposed = False
                self._episode_escalated = False
        else:
            self._cooldown_counter = 0

        # --- Heartbeat (no episode needed) ---
        if self._frame_count % self.heartbeat_every == 0:
            await self._emit({
                "event": "ear_heartbeat",
                "ear_value": round(avg_ear, 4),
            })

        # --- Action proposal logic ---
        event = await self._maybe_propose(avg_ear)
        return event

    async def _maybe_propose(self, avg_ear: float) -> Optional[DeciderEvent]:
        n = len(self._ear_buffer)

        # Check short window (first trigger)
        if n >= self.window and all(list(self._ear_buffer)[-self.window:]):
            if not self._episode_proposed:
                # Start a new episode
                self._episode_id = str(uuid.uuid4())
                self._episode_proposed = True
                action = "INCREASE_FOLLOWING_DISTANCE"
                evt = DeciderEvent(
                    ear_value=round(avg_ear, 4),
                    proposed_action=action,
                    episode_id=self._episode_id,
                )
                await self._emit(evt.model_dump())
                return evt

        # Check long window (escalation)
        if n >= self.escalation_window and all(list(self._ear_buffer)[-self.escalation_window:]):
            if self._episode_proposed and not self._episode_escalated:
                self._episode_escalated = True
                action = "TIGHTEN_LANE_ASSIST"
                evt = DeciderEvent(
                    ear_value=round(avg_ear, 4),
                    proposed_action=action,
                    episode_id=self._episode_id,
                )
                await self._emit(evt.model_dump())
                return evt

        return None

from typing import Callable, Awaitable
import numpy as np
import cv2
from models import DeciderEvent, VerifierEvent
from config_loader import config

# ---------------------------------------------------------------------------
# Head-pose landmark indices (distinct from Decider's eye-only indices)
# These are the standard solvePnP "facial anchor" points.
# ---------------------------------------------------------------------------
HEAD_POSE_FACE_IDX = [1, 152, 263, 33, 287, 57]
# 1   = Nose tip
# 152 = Chin
# 263 = Left eye outer corner  (face-right)
# 33  = Right eye outer corner (face-left)
# 287 = Left mouth corner
# 57  = Right mouth corner

# Generic 3D face model coordinates (mm, origin at nose tip)
_FACE_3D = np.array([
    [0.0,    0.0,    0.0   ],   # Nose tip
    [0.0,   -330.0, -65.0 ],   # Chin
    [-225.0, 170.0, -135.0],   # Left eye outer corner
    [ 225.0, 170.0, -135.0],   # Right eye outer corner
    [-150.0,-150.0, -125.0],   # Left mouth corner
    [ 150.0,-150.0, -125.0],   # Right mouth corner
], dtype=np.float64)

_DIST_COEFFS = np.zeros((4, 1), dtype=np.float64)


def _solve_head_pose(landmarks, frame_w: int, frame_h: int):
    """Return (yaw_deg, pitch_deg) or (None, None) if solvePnP fails."""
    pts_2d = np.array([
        [landmarks[i].x * frame_w, landmarks[i].y * frame_h]
        for i in HEAD_POSE_FACE_IDX
    ], dtype=np.float64)

    focal = float(frame_w)
    cam_matrix = np.array([
        [focal, 0.0,   frame_w / 2.0],
        [0.0,   focal, frame_h / 2.0],
        [0.0,   0.0,   1.0          ],
    ], dtype=np.float64)

    ok, rvec, _ = cv2.solvePnP(
        _FACE_3D, pts_2d, cam_matrix, _DIST_COEFFS,
        flags=cv2.SOLVEPNP_ITERATIVE
    )
    if not ok:
        return None, None

    rmat, _ = cv2.Rodrigues(rvec)
    # Decompose rotation matrix to Euler angles
    sy = np.sqrt(rmat[0, 0] ** 2 + rmat[1, 0] ** 2)
    if sy > 1e-6:
        pitch = np.degrees(np.arctan2(-rmat[2, 0], sy))
        yaw   = np.degrees(np.arctan2(rmat[1, 0], rmat[0, 0]))
    else:
        pitch = np.degrees(np.arctan2(-rmat[2, 0], sy))
        yaw   = 0.0

    return round(yaw, 2), round(pitch, 2)


class Verifier:
    def __init__(self, can_bus, event_emitter: Callable[[dict], Awaitable[None]]):
        self._can = can_bus
        self._emit = event_emitter
        cfg = config["verifier"]
        self.yaw_threshold: float      = cfg["yaw_threshold_deg"]
        self.pitch_threshold: float    = cfg["pitch_threshold_deg"]
        self.min_speed: float          = cfg["min_speed_for_action_kmh"]
        self.steering_threshold: float = cfg["steering_angle_threshold_deg"]

    async def verify(
        self,
        decider_evt: DeciderEvent,
        landmarks,
        frame_w: int,
        frame_h: int,
    ) -> VerifierEvent:
        yaw, pitch = _solve_head_pose(landmarks, frame_w, frame_h)
        if yaw is None:
            yaw, pitch = 0.0, 0.0

        can_snap = self._can.get_latest_reading()
        action = decider_evt.proposed_action

        failures = []

        # --- Head-pose checks ---
        if abs(yaw) >= self.yaw_threshold:
            failures.append(
                f"yaw {yaw}° exceeds forward-facing threshold {self.yaw_threshold}°"
            )
        if abs(pitch) >= self.pitch_threshold:
            failures.append(
                f"pitch {pitch}° exceeds forward-facing threshold {self.pitch_threshold}°"
            )

        # --- CAN bus context checks ---
        if action == "INCREASE_FOLLOWING_DISTANCE":
            if can_snap.speed_kmh <= self.min_speed:
                failures.append(
                    f"speed {can_snap.speed_kmh} km/h is below minimum {self.min_speed} km/h"
                )
            if can_snap.brake_status == "HARD_BRAKING":
                failures.append("vehicle is in HARD_BRAKING — following-distance action deferred")

        elif action == "TIGHTEN_LANE_ASSIST":
            if abs(can_snap.steering_angle_deg) > self.steering_threshold:
                failures.append(
                    f"steering angle {can_snap.steering_angle_deg}° exceeds threshold "
                    f"{self.steering_threshold}° — likely intentional maneuver"
                )

        if failures:
            decision = "BLOCKED"
            reason = "; ".join(failures)
        else:
            decision = "APPROVED"
            reason = "head pose forward-facing and vehicle bus context consistent with proposed action"

        evt = VerifierEvent(
            episode_id=decider_evt.episode_id,
            yaw=yaw,
            pitch=pitch,
            can_snapshot=can_snap,
            decision=decision,
            reason=reason,
        )
        await self._emit(evt.model_dump())
        return evt

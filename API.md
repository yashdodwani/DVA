# DVA ADAS Co-Pilot — Dashboard API Reference

Base URL: `http://localhost:8000` (configurable via `config.yaml → server`)

CORS is enabled for all origins, so the dashboard can run on any localhost port.

---

## WebSocket

### `GET /ws/events`

Connect once; the server pushes every event as a JSON text message as soon as it occurs.

**How to connect (JS example):**
```js
const ws = new WebSocket("ws://localhost:8000/ws/events");
ws.onmessage = (msg) => {
  const event = JSON.parse(msg.data);
  console.log(event.event, event);
};
```

Four event types flow over this socket:

---

### `decider_event`
Emitted when the Decider detects a sustained drowsiness signal and proposes a corrective action.

```json
{
  "event": "decider_event",
  "timestamp": "2024-01-15T10:23:45.123456Z",
  "ear_value": 0.18,
  "proposed_action": "INCREASE_FOLLOWING_DISTANCE",
  "episode_id": "a1b2c3d4-..."
}
```

| Field             | Type   | Notes |
|-------------------|--------|-------|
| `ear_value`       | float  | Eye Aspect Ratio at time of proposal (2 d.p.) |
| `proposed_action` | string | `INCREASE_FOLLOWING_DISTANCE` or `TIGHTEN_LANE_ASSIST` |
| `episode_id`      | string | UUID linking all events in the same risk episode |

---

### `verifier_event`
Emitted immediately after each `decider_event`, with the Verifier's decision.

```json
{
  "event": "verifier_event",
  "timestamp": "2024-01-15T10:23:45.187654Z",
  "episode_id": "a1b2c3d4-...",
  "yaw": 4.2,
  "pitch": -2.1,
  "can_snapshot": {
    "speed_kmh": 62.0,
    "steering_angle_deg": 1.5,
    "brake_status": "OFF"
  },
  "decision": "APPROVED",
  "reason": "head pose forward-facing and vehicle bus context consistent with proposed action"
}
```

| Field          | Type   | Notes |
|----------------|--------|-------|
| `yaw`          | float  | Head yaw in degrees (positive = right) |
| `pitch`        | float  | Head pitch in degrees (positive = up) |
| `can_snapshot` | object | CAN bus reading at the moment of verification |
| `decision`     | string | `APPROVED` or `BLOCKED` |
| `reason`       | string | Human-readable explanation; lists which checks failed on BLOCKED |

`brake_status` is one of: `OFF` | `LIGHT_BRAKING` | `HARD_BRAKING`

---

### `actuation_event`
Emitted after the Verifier decides, recording the final outcome of the episode.

```json
{
  "event": "actuation_event",
  "timestamp": "2024-01-15T10:23:45.200000Z",
  "episode_id": "a1b2c3d4-...",
  "proposed_action": "INCREASE_FOLLOWING_DISTANCE",
  "verifier_decision": "APPROVED",
  "final_status": "EXECUTED",
  "alert_issued": false
}
```

| Field               | Type    | Notes |
|---------------------|---------|-------|
| `final_status`      | string  | `EXECUTED` (action ran) or `SUPPRESSED` (Verifier blocked it) |
| `alert_issued`      | boolean | `true` when suppressed — driver alert fallback was issued |

When `alert_issued` is `true`, a companion `alert_issued` event is also pushed:
```json
{
  "event": "alert_issued",
  "timestamp": "...",
  "episode_id": "...",
  "reason": "yaw 27.5° exceeds forward-facing threshold 15.0°"
}
```

---

### `can_bus_event`
Emitted every 200 ms (configurable) by the CAN Bus Simulator, **independently** of any drowsiness episode. Use this for a continuously live "vehicle telemetry" panel.

```json
{
  "event": "can_bus_event",
  "timestamp": "2024-01-15T10:23:45.000000Z",
  "source": "simulated_CAN",
  "speed_kmh": 64.2,
  "steering_angle_deg": -0.8,
  "brake_status": "OFF"
}
```

---

### `ear_heartbeat`
Emitted every 5 frames (~every 333 ms at 15 FPS) so the dashboard can display a live EAR trace even when no episode is active.

```json
{
  "event": "ear_heartbeat",
  "ear_value": 0.28
}
```

---

## REST Endpoints

### `GET /api/state`
Returns the current system snapshot. Use for initial page load or polling as a fallback.

**Response:**
```json
{
  "current_ear": 0.24,
  "active_episode": null,
  "last_10_events": [ ... ],
  "latest_can_reading": {
    "speed_kmh": 64.2,
    "steering_angle_deg": -0.8,
    "brake_status": "OFF"
  },
  "system_status": "MONITORING"
}
```

`active_episode` is `null` when no risk episode is in progress, or the episode UUID string when one is active.

---

### `GET /api/history`
Returns the full ring buffer (last 50 events across all types) for building a history/timeline view.

**Response:** JSON array of event objects (same shapes as above).

---

## Episode Lifecycle

```
decider_event  →  verifier_event  →  actuation_event
     ↑ all share the same episode_id ↑
```

A risk episode begins when the Decider's EAR drops below threshold for the configured window, and ends when EAR returns above threshold for the cooldown period. Within a single episode, a second (escalated) `decider_event` may be emitted with `proposed_action: "TIGHTEN_LANE_ASSIST"`.

---

## Demo Scenario Overrides (CLI)

The backend supports three demo scenarios, selectable via CLI flag or terminal hotkey:

| Scenario | CLI flag      | Hotkey | Expected outcome |
|----------|---------------|--------|------------------|
| A — Genuine drowsiness | `--scenario A` | `a` | `EXECUTED` |
| B — Head-turn false trigger | `--scenario B` | `b` | `SUPPRESSED` (yaw check) |
| C — Hard-braking false trigger | `--scenario C` | `c` | `SUPPRESSED` (CAN check) |
| Reset | `--reset` | `r` | Normal simulation |

Run: `python main.py --scenario A`

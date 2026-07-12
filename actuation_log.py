from collections import deque
from typing import Callable, Awaitable, Optional
from models import DeciderEvent, VerifierEvent, ActuationEvent
from config_loader import config


class ActuationLog:
    def __init__(self, event_emitter: Callable[[dict], Awaitable[None]]):
        self._emit = event_emitter
        size = config["actuation_log"]["ring_buffer_size"]
        self._buffer: deque = deque(maxlen=size)
        self._current_ear: float = 1.0
        self._active_episode: Optional[str] = None

    def set_current_ear(self, ear: float):
        self._current_ear = ear

    def set_active_episode(self, episode_id: Optional[str]):
        self._active_episode = episode_id

    async def process(
        self,
        decider_evt: DeciderEvent,
        verifier_evt: VerifierEvent,
    ) -> ActuationEvent:
        approved = verifier_evt.decision == "APPROVED"
        final_status = "EXECUTED" if approved else "SUPPRESSED"
        alert_issued = not approved

        evt = ActuationEvent(
            episode_id=decider_evt.episode_id,
            proposed_action=decider_evt.proposed_action,
            verifier_decision=verifier_evt.decision,
            final_status=final_status,
            alert_issued=alert_issued,
        )
        payload = evt.model_dump()
        self._buffer.append(payload)

        # If blocked, also append an ALERT event for the dashboard
        if alert_issued:
            alert_payload = {
                "event": "alert_issued",
                "timestamp": evt.timestamp,
                "episode_id": evt.episode_id,
                "reason": verifier_evt.reason,
            }
            self._buffer.append(alert_payload)
            await self._emit(alert_payload)

        await self._emit(payload)
        return evt

    def record_raw(self, event_dict: dict):
        """Store raw event dict in the ring buffer (decider/verifier/can)."""
        self._buffer.append(event_dict)

    def get_history(self) -> list:
        return list(self._buffer)

    def get_last_n(self, n: int = 10) -> list:
        items = list(self._buffer)
        return items[-n:]

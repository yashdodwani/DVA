from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
import uuid

def generate_timestamp():
    return datetime.utcnow().isoformat() + "Z"

class DeciderEvent(BaseModel):
    event: Literal["decider_event"] = "decider_event"
    timestamp: str = Field(default_factory=generate_timestamp)
    ear_value: float
    proposed_action: str
    episode_id: str

class CanSnapshot(BaseModel):
    speed_kmh: float
    steering_angle_deg: float
    brake_status: Literal["OFF", "LIGHT_BRAKING", "HARD_BRAKING"]

class VerifierEvent(BaseModel):
    event: Literal["verifier_event"] = "verifier_event"
    timestamp: str = Field(default_factory=generate_timestamp)
    episode_id: str
    yaw: float
    pitch: float
    can_snapshot: CanSnapshot
    decision: Literal["APPROVED", "BLOCKED"]
    reason: str

class CanBusEvent(BaseModel):
    event: Literal["can_bus_event"] = "can_bus_event"
    timestamp: str = Field(default_factory=generate_timestamp)
    source: Literal["simulated_CAN"] = "simulated_CAN"
    speed_kmh: float
    steering_angle_deg: float
    brake_status: Literal["OFF", "LIGHT_BRAKING", "HARD_BRAKING"]

class ActuationEvent(BaseModel):
    event: Literal["actuation_event"] = "actuation_event"
    timestamp: str = Field(default_factory=generate_timestamp)
    episode_id: str
    proposed_action: str
    verifier_decision: Literal["APPROVED", "BLOCKED"]
    final_status: Literal["EXECUTED", "SUPPRESSED"]
    alert_issued: bool

class SystemState(BaseModel):
    current_ear: float
    active_episode: Optional[str] = None
    last_10_events: List[dict]
    latest_can_reading: CanSnapshot
    system_status: str

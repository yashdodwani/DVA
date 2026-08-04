import asyncio
import random
from models import CanSnapshot, CanBusEvent
from config_loader import config
from typing import Optional, Callable, Awaitable


class CanBusSimulator:
    def __init__(self):
        self.interval_ms = config["can_bus"]["tick_interval_ms"]
        self.speed_range = config["can_bus"]["speed_range_kmh"]
        self.steering_range = config["can_bus"]["steering_normal_range_deg"]
        
        self.current_speed = random.uniform(*self.speed_range)
        self.current_steering = 0.0
        self.current_brake = "OFF"
        
        self.running = False
        self.task: Optional[asyncio.Task] = None
        self.subscribers = [] # list of callbacks: Callable[[CanBusEvent], Awaitable[None]]

        # Override state for demo scenarios
        self.override_active = False
        self.override_speed: Optional[float] = None
        self.override_steering: Optional[float] = None
        self.override_brake: Optional[str] = None

    def subscribe(self, callback: Callable[[CanBusEvent], Awaitable[None]]):
        self.subscribers.append(callback)

    def set_override(self, speed=None, steering=None, brake=None):
        self.override_active = True
        self.override_speed = speed
        self.override_steering = steering
        self.override_brake = brake

    def clear_override(self):
        self.override_active = False
        self.override_speed = None
        self.override_steering = None
        self.override_brake = None

    def get_latest_reading(self) -> CanSnapshot:
        return CanSnapshot(
            speed_kmh=round(self.override_speed if self.override_speed is not None else self.current_speed, 2),
            steering_angle_deg=round(self.override_steering if self.override_steering is not None else self.current_steering, 2),
            brake_status=self.override_brake if self.override_brake is not None else self.current_brake
        )

    def _update_state(self):
        if not self.override_active:
            # Random walk for speed
            speed_change = random.uniform(-1.0, 1.0)
            self.current_speed = max(self.speed_range[0], min(self.speed_range[1], self.current_speed + speed_change))
            
            # Random walk for steering, tending towards 0
            steering_change = random.uniform(-0.5, 0.5) - (self.current_steering * 0.1)
            self.current_steering = max(self.steering_range[0], min(self.steering_range[1], self.current_steering + steering_change))
            
            # Very small chance of random braking, but mostly rely on overrides for demo
            if random.random() < 0.01:
                self.current_brake = random.choice(["LIGHT_BRAKING", "HARD_BRAKING"])
            else:
                self.current_brake = "OFF"

    async def _loop(self):
        self.running = True
        while self.running:
            self._update_state()
            
            reading = self.get_latest_reading()
            event = CanBusEvent(
                speed_kmh=reading.speed_kmh,
                steering_angle_deg=reading.steering_angle_deg,
                brake_status=reading.brake_status
            )
            
            for sub in self.subscribers:
                asyncio.create_task(sub(event))
                
            await asyncio.sleep(self.interval_ms / 1000.0)

    def start(self):
        if not self.task or self.task.done():
            self.task = asyncio.create_task(self._loop())

    def stop(self):
        self.running = False
        if self.task:
            self.task.cancel()

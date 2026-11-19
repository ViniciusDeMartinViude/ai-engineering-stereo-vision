"""Time-based exponential smoother for dashboard telemetry.

Applies filtering only to the displayed reading, never to the raw timestamps.
"""
import math


class ExponentialSmoother:
    def __init__(self, time_constant_seconds=1.5):
        if time_constant_seconds <= 0:
            raise ValueError("time constant must be positive")
        self.tau = float(time_constant_seconds)
        self.value = None
        self.last_time = None

    def update(self, sample, now):
        if not math.isfinite(sample):
            return self.value
        if self.value is None:
            self.value = float(sample)
        else:
            dt = max(0.0, now - self.last_time)
            weight = -math.expm1(-dt / self.tau)
            self.value += weight * (float(sample) - self.value)
        self.last_time = now
        return self.value

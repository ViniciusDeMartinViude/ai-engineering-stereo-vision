"""Object speed per camera, from tracked detections and the frames' capture times.

Same measure as Ultralytics' SpeedEstimator: the displacement between the
first and last position of a track over a short history, divided by the time
between them. Two differences make it right for this client:

* Time comes from each frame's camera *capture* timestamp, not from a frame
  counter and an assumed frame rate. YOLO always takes the newest frame, so
  it skips frames whenever it is slower than the video, and frame counting
  would then be off by the skipped fraction.
* The history is a rolling time window, so the speed keeps updating while the
  object moves (Ultralytics locks each object's speed after its first frames).

Speeds are in image pixels per second; ``format_speed`` turns them into mm/s
when the camera has a scale (mm per pixel) measured with the Scale tool.
"""
from __future__ import annotations

import collections
import math

WINDOW_MS = 700.0      # history used for the displacement (rolling)
MIN_SPAN_MS = 150.0    # need at least this much time between first and last point
FORGET_MS = 2000.0     # drop tracks not seen for this long
STILL_PX_S = 4.0       # slower than this reads as "still" (detection jitter)


class SpeedEstimator:
    """Velocities of the tracked objects of one camera."""

    def __init__(self, window_ms=WINDOW_MS, min_span_ms=MIN_SPAN_MS):
        self.window_ms = window_ms
        self.min_span_ms = min_span_ms
        self.history = {}  # track_id -> deque[(capture_ms, x, y)]

    def reset(self):
        self.history.clear()

    def update(self, capture_ms, detections):
        """Add this frame's tracked detections; return {track_id: (vx, vy)} in px/s.

        ``capture_ms`` is when this camera captured the frame; None (timestamp
        unreadable) adds nothing and returns the current estimates.
        """
        if capture_ms is not None:
            for d in detections:
                if d.track_id < 0:
                    continue
                points = self.history.setdefault(d.track_id, collections.deque())
                if points and capture_ms <= points[-1][0]:
                    continue  # same or older frame: no new information
                x, y = d.center
                points.append((capture_ms, x, y))
                while len(points) > 2 and capture_ms - points[1][0] >= self.window_ms:
                    points.popleft()
            for track_id in [t for t, p in self.history.items() if capture_ms - p[-1][0] > FORGET_MS]:
                del self.history[track_id]
        return self.velocities()

    def velocities(self):
        result = {}
        for track_id, points in self.history.items():
            (t0, x0, y0), (t1, x1, y1) = points[0], points[-1]
            span = t1 - t0
            if span >= self.min_span_ms:
                result[track_id] = ((x1 - x0) / span * 1000.0, (y1 - y0) / span * 1000.0)
        return result


def speed_of(velocity):
    return math.hypot(*velocity)


def format_speed(velocity, mm_per_px=None):
    """'142 mm/s' with a scale, '168 px/s' without, 'still' when below jitter level."""
    pixels = speed_of(velocity)
    if pixels < STILL_PX_S:
        return 'still'
    if mm_per_px:
        return f'{pixels * mm_per_px:.0f} mm/s'
    return f'{pixels:.0f} px/s'


def load_speed_settings(settings, side):
    """(show speeds, mm per pixel or None) remembered for one camera."""
    show = settings.value(f'speed/show_{side}', False, type=bool)
    try:
        scale = float(settings.value(f'speed/mm_per_px_{side}', 0.0) or 0.0)
    except (TypeError, ValueError):
        scale = 0.0
    return bool(show), (scale if scale > 0 else None)


def save_speed_settings(settings, side, show, mm_per_px):
    settings.setValue(f'speed/show_{side}', bool(show))
    if mm_per_px:
        settings.setValue(f'speed/mm_per_px_{side}', float(mm_per_px))
    else:
        settings.remove(f'speed/mm_per_px_{side}')

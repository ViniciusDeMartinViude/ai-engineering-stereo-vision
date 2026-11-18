"""Optional real-time YOLO inference for both stereo views.

No model is loaded unless the client was started with --yolo-model. Frames are
never uploaded: Ultralytics operates locally on decoded numpy BGR images.
The queue is depth one (latest frame wins) to avoid inference backlog.
"""
import threading
import time
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Detection:
    side: str
    label: str
    confidence: float
    xyxy: tuple  # pixel coordinates within one source camera image


class YoloWorker:
    def __init__(self, weights, confidence=0.35, imgsz=640, device=None):
        self.weights = weights
        self.confidence = float(confidence)
        self.imgsz = int(imgsz)
        self.device = device
        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)
        self.pending = None
        self.stop_event = False
        self.thread = None
        self.detections = ()
        self.status = "Loading YOLO..."
        self.result_frame_id = -1
        self.result_mono = 0.0
        self.last_inference_ms = 0.0
        self.error = None
        self.processed = 0

    def start(self):
        self.thread = threading.Thread(target=self._run, daemon=True, name="stereo-yolo")
        self.thread.start()

    def submit(self, frame, frame_id):
        """Submit the latest *original* stereo frame; no copy unless idle."""
        with self.condition:
            if self.stop_event or self.error:
                return
            # Replacing pending work avoids an unbounded backlog.
            self.pending = (frame, frame_id)
            self.condition.notify()

    def snapshot(self):
        with self.lock:
            return (self.detections, self.status, self.result_frame_id,
                    self.result_mono, self.last_inference_ms, self.error)

    def stop(self):
        with self.condition:
            self.stop_event = True
            self.pending = None
            self.condition.notify_all()
        if self.thread:
            self.thread.join(timeout=2)

    def _run(self):
        try:
            from ultralytics import YOLO
            model = YOLO(self.weights)
            with self.lock:
                self.status = f"YOLO ready: {self.weights}"
            while True:
                with self.condition:
                    while self.pending is None and not self.stop_event:
                        self.condition.wait(timeout=0.5)
                    if self.stop_event:
                        break
                    frame, fid = self.pending
                    self.pending = None
                h, w = frame.shape[:2]
                video_h = h - 100  # metadata footer MUST never enter model
                half = w // 2
                if video_h < 1 or half < 1:
                    continue
                images = [np.ascontiguousarray(frame[:video_h, :half]),
                          np.ascontiguousarray(frame[:video_h, half:])]
                options = dict(source=images, conf=self.confidence,
                               imgsz=self.imgsz, verbose=False)
                if self.device and self.device.lower() != "auto":
                    options["device"] = self.device
                start = time.perf_counter()
                results = model.predict(**options)
                inference_ms = (time.perf_counter() - start) * 1000.0
                found = []
                for side, result in zip(("LEFT", "RIGHT"), results):
                    names = result.names
                    if result.boxes is None:
                        continue
                    for box in result.boxes:
                        cls = int(box.cls[0].item())
                        conf = float(box.conf[0].item())
                        coords = tuple(float(v) for v in box.xyxy[0].cpu().tolist())
                        name = names.get(cls, str(cls)) if isinstance(names, dict) else names[cls]
                        found.append(Detection(side, str(name), conf, coords))
                with self.lock:
                    self.detections = tuple(found)
                    self.result_frame_id = fid
                    self.result_mono = time.monotonic()
                    self.last_inference_ms = inference_ms
                    self.processed += 1
                    self.status = (f"YOLO {len(found)} objects "
                                   f"(L={sum(d.side == 'LEFT' for d in found)}, "
                                   f"R={sum(d.side == 'RIGHT' for d in found)}) "
                                   f"{inference_ms:.0f}ms")
        except Exception as exc:
            with self.lock:
                self.error = f"{type(exc).__name__}: {exc}"
                self.status = f"YOLO error: {self.error}"

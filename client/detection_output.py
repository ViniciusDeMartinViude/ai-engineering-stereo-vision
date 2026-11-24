"""Publish YOLO detections from both cameras as JSON over UDP, for other programs.

One datagram per processed stereo frame, sent as soon as YOLO finishes it.
A student's program (the "backend") receives them on a UDP port and uses
the detections and capture times to track objects and command a robot.

* UDP: the client never waits for the receiver, which can start, stop or
  crash at any time. A lost datagram is replaced by the next one ~100 ms later.
* Times are in the camera server's clock (UTC milliseconds), corrected with
  the client's LAN clock sync, so capture times from every client agree.
* When there is nothing to send (detection off, model loading, stream
  stalled), a small "status" message goes out once a second instead.

See docs/DETECTION_OUTPUT.md for the message format.
"""
from __future__ import annotations

import collections
import ipaddress
import json
import socket
import threading
import time

FORMAT_VERSION = 1
DEFAULT_PORT = 5600
DEFAULT_TARGET = f'127.0.0.1:{DEFAULT_PORT}'
MAX_OBJECTS_PER_CAMERA = 100   # keeps a datagram far below the 64 KB UDP limit
HEARTBEAT_S = 1.0
NO_LISTENER_HOLD_S = 2.0       # how long a "port unreachable" report counts
WINDOWS_CONN_RESET = 10054


def parse_target(text):
    """'host:port' or 'udp://host:port' -> (host, port). Raises ValueError with a clear message."""
    value = str(text or '').strip()
    if value.lower().startswith('udp://'):
        value = value[6:]
    host, sep, port_text = value.rpartition(':')
    if not sep or not host:
        raise ValueError(f'{text!r} is not HOST:PORT, e.g. 127.0.0.1:{DEFAULT_PORT}')
    try:
        port = int(port_text)
    except ValueError:
        raise ValueError(f'{port_text!r} in {text!r} is not a port number') from None
    if not 1 <= port <= 65535:
        raise ValueError(f'port {port} is outside 1-65535')
    if host.lower() == 'localhost':
        host = '127.0.0.1'
    try:
        address = ipaddress.IPv4Address(host)
    except ValueError:
        raise ValueError(f'{host!r} is not an IPv4 address (use e.g. 127.0.0.1 or 192.168.0.11)') from None
    if address.is_multicast or str(address) == '255.255.255.255':
        raise ValueError(f'{host} is a multicast/broadcast address; give one receiving PC')
    return str(address), port


def format_target(host, port):
    return f'{host}:{port}'


def _r(value, digits=1):
    return round(float(value), digits)


def camera_block(size, level_deg, roi, detections, velocities=None, mm_per_px=None):
    """One camera's part of the message. Coordinates are levelled-image pixels.

    ``velocities``: {track_id: (vx, vy)} in px/s from the speed estimator;
    ``mm_per_px``: the camera's scale from the Scale tool, or None.
    """
    velocities = velocities or {}
    ordered = sorted(detections, key=lambda d: d.confidence, reverse=True)
    objects = []
    for d in ordered[:MAX_OBJECTS_PER_CAMERA]:
        x1, y1, x2, y2 = d.xyxy
        cx, cy = d.center
        track_id = getattr(d, 'track_id', -1)
        velocity = velocities.get(track_id) if track_id >= 0 else None
        objects.append({'class_id': int(d.class_id), 'label': d.label, 'conf': _r(d.confidence, 3),
                        'box': [_r(x1), _r(y1), _r(x2), _r(y2)], 'center': [_r(cx), _r(cy)],
                        'track_id': track_id if track_id >= 0 else None,
                        'velocity_px_s': [_r(velocity[0]), _r(velocity[1])] if velocity else None,
                        'velocity_mm_s': ([_r(velocity[0] * mm_per_px), _r(velocity[1] * mm_per_px)]
                                          if velocity and mm_per_px else None)})
    block = {'size': [int(size[0]), int(size[1])], 'level_deg': float(level_deg or 0.0),
             'roi': list(roi) if roi is not None else None,
             'mm_per_px': float(mm_per_px) if mm_per_px else None, 'objects': objects}
    if len(ordered) > MAX_OBJECTS_PER_CAMERA:
        block['truncated'] = len(ordered)
    return block


def detection_message(source, frame_meta, detections, inference_ms, model, clock_offset_ms,
                      clock_synced, now_ns=None, velocities=None, scales=None):
    """The 'detections' message for one processed stereo frame.

    ``frame_meta`` is what the client recorded when it handed the frame to YOLO:
    stamp (or None when the timestamp strip failed to decode), recv_ns, sizes,
    level angles and ROIs, keyed 'LEFT' / 'RIGHT'. ``velocities`` and ``scales``
    are keyed the same way (see ``camera_block``).
    """
    velocities = velocities or {}
    scales = scales or {}
    now_ns = time.time_ns() if now_ns is None else now_ns
    stamp = frame_meta.get('stamp')
    sides = {'LEFT': [], 'RIGHT': []}
    for d in detections:
        sides.setdefault(d.side, []).append(d)
    return {
        'v': FORMAT_VERSION,
        'type': 'detections',
        'source': source,
        'seq': stamp.sequence if stamp is not None else None,
        'stamp_ok': stamp is not None,
        'capture_utc_ms': ({'left': float(stamp.left_utc_ms), 'right': _r(stamp.right_utc_ms, 3)}
                           if stamp is not None else None),
        'received_utc_ms': _r(frame_meta['recv_ns'] / 1e6 - clock_offset_ms),
        'detected_utc_ms': _r(now_ns / 1e6 - clock_offset_ms),
        'clock_synced': bool(clock_synced),
        'inference_ms': _r(inference_ms),
        'model': model,
        'cameras': {
            side.lower(): camera_block(frame_meta['sizes'][side], frame_meta['angles'].get(side, 0.0),
                                       frame_meta['rois'].get(side), sides[side],
                                       velocities.get(side), scales.get(side))
            for side in ('LEFT', 'RIGHT')
        },
    }


def status_message(source, detection, stream_live, model, clock_offset_ms, clock_synced, now_ns=None):
    """Heartbeat sent when no detections went out for a second: says why."""
    now_ns = time.time_ns() if now_ns is None else now_ns
    return {'v': FORMAT_VERSION, 'type': 'status', 'source': source,
            'sent_utc_ms': _r(now_ns / 1e6 - clock_offset_ms), 'clock_synced': bool(clock_synced),
            'detection': detection, 'stream_live': bool(stream_live), 'model': model}


class DetectionPublisher:
    """Thread-safe UDP JSON sender with counters for the app's Output pill."""

    def __init__(self, target=DEFAULT_TARGET, enabled=False, source=None):
        self.host, self.port = parse_target(target)
        self.enabled = bool(enabled)
        self.source = source or socket.gethostname()
        self._lock = threading.Lock()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setblocking(False)
        self.sent = 0
        self.sent_bytes = 0
        self.errors = 0
        self.last_error = None
        self.last_sent_mono = None
        self.last_detection_mono = None
        self.last_no_listener_mono = None
        self.last_message = None      # the JSON text, for the live view
        self.last_type = None
        self._times = collections.deque(maxlen=64)

    # ---- configuration -----------------------------------------------------
    @property
    def target(self):
        return format_target(self.host, self.port)

    def set_target(self, text):
        host, port = parse_target(text)
        with self._lock:
            if (host, port) != (self.host, self.port):
                self.host, self.port = host, port
                self.last_no_listener_mono = None
                self._times.clear()

    def set_enabled(self, enabled):
        with self._lock:
            self.enabled = bool(enabled)
            if not enabled:
                self._times.clear()
                self.last_no_listener_mono = None

    # ---- sending ---------------------------------------------------------------
    def publish(self, message):
        """Send one message if enabled. Never blocks and never raises."""
        if not self.enabled:
            return False
        text = json.dumps(message, separators=(',', ':'), ensure_ascii=False)
        data = text.encode('utf-8')
        now = time.monotonic()
        with self._lock:
            self._drain_reports(now)
            try:
                self._sock.sendto(data, (self.host, self.port))
            except OSError as exc:
                self.errors += 1
                self.last_error = f'{type(exc).__name__}: {exc}'
                return False
            self.sent += 1
            self.sent_bytes += len(data)
            self.last_sent_mono = now
            self._times.append(now)
            self.last_message = text
            self.last_type = message.get('type')
            if self.last_type == 'detections':
                self.last_detection_mono = now
        return True

    def heartbeat_due(self, now=None):
        """True when enabled and nothing was sent for HEARTBEAT_S."""
        now = time.monotonic() if now is None else now
        return self.enabled and (self.last_sent_mono is None or now - self.last_sent_mono >= HEARTBEAT_S)

    def _drain_reports(self, now):
        """Windows reports 'port unreachable' for an earlier datagram on the next receive."""
        while True:
            try:
                self._sock.recvfrom(2048)
            except BlockingIOError:
                return
            except ConnectionResetError:
                self.last_no_listener_mono = now
            except OSError:
                return

    # ---- presentation ------------------------------------------------------------
    def status(self, now=None):
        now = time.monotonic() if now is None else now
        with self._lock:
            self._drain_reports(now)
            recent = [t for t in self._times if now - t <= 2.0]
            rate = (len(recent) - 1) / (recent[-1] - recent[0]) if len(recent) >= 2 and recent[-1] > recent[0] else 0.0
            no_listener = (self.last_no_listener_mono is not None
                           and now - self.last_no_listener_mono <= NO_LISTENER_HOLD_S)
            return {
                'enabled': self.enabled, 'target': self.target, 'source': self.source,
                'sent': self.sent, 'bytes': self.sent_bytes, 'errors': self.errors,
                'last_error': self.last_error, 'rate': rate,
                'since_last': None if self.last_sent_mono is None else now - self.last_sent_mono,
                'since_detection': (None if self.last_detection_mono is None
                                    else now - self.last_detection_mono),
                'no_listener': no_listener, 'local': self.host.startswith('127.'),
                'last_message': self.last_message, 'last_type': self.last_type,
            }

    def close(self):
        with self._lock:
            self._sock.close()

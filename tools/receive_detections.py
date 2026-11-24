#!/usr/bin/env python3
"""Receive the detections the stereo client publishes, and estimate object speeds.

A starting point for a student "backend". Standard library only, Python 3.8+:
copy this one file anywhere.

  1. In the client, click the Output pill and switch publishing on (or start it
     with --publish 127.0.0.1:5600).
  2. Run:  python receive_detections.py
           python receive_detections.py --raw          (print every message as JSON)
           python receive_detections.py --host 0.0.0.0 (accept from other PCs too)

Each line shows, per camera, the objects found and a speed estimate in pixels
per second. Speeds use the camera *capture* times in the message, not when the
message arrived: arrival times include ~0.3 s of variable network and decoding
delay and would give noisy speeds.

The tracking here is deliberately simple (nearest object with the same label
in the previous frame). Improve it: filter speeds over several frames, use the
conveyor direction, convert pixels to millimetres after calibration, predict
where an object will be when the robot gets there.

Message format: docs/DETECTION_OUTPUT.md
"""
import argparse
import json
import math
import socket
import time

MAX_JUMP_PX = 150      # an object can't move further than this between two messages
MAX_GAP_MS = 1000      # don't compute a speed across a longer gap


class SimpleTracker:
    """Match each object to the nearest one with the same label in the previous frame."""

    def __init__(self):
        self.previous = []          # [(label, (x, y))]
        self.previous_time = None   # capture time, ms

    def update(self, objects, capture_ms):
        """Returns [(object, (vx, vy) in px/s or None)]."""
        results = []
        dt_ms = None if self.previous_time is None else capture_ms - self.previous_time
        usable = dt_ms is not None and 0 < dt_ms <= MAX_GAP_MS
        for obj in objects:
            speed = None
            if usable:
                x, y = obj['center']
                same = [(math.hypot(x - px, y - py), (px, py))
                        for label, (px, py) in self.previous if label == obj['label']]
                if same:
                    distance, (px, py) = min(same)
                    if distance <= MAX_JUMP_PX:
                        speed = ((x - px) / dt_ms * 1000.0, (y - py) / dt_ms * 1000.0)
            results.append((obj, speed))
        self.previous = [(obj['label'], tuple(obj['center'])) for obj in objects]
        self.previous_time = capture_ms
        return results


def describe_camera(name, camera, tracker, capture_ms):
    objects = camera['objects']
    if capture_ms is None:          # timestamp strip unreadable: no reliable time, no speed
        tracked = [(obj, None) for obj in objects]
    else:
        tracked = tracker.update(objects, capture_ms)
    if not tracked:
        return f'{name}: -'
    parts = []
    for obj, speed in tracked:
        x, y = obj['center']
        text = f'{obj["label"]} {obj["conf"]:.0%} @({x:.0f},{y:.0f})'
        if speed is not None:
            text += f' v=({speed[0]:+.0f},{speed[1]:+.0f}) px/s'
        parts.append(text)
    return f'{name}: ' + '; '.join(parts)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--host', default='127.0.0.1',
                        help='address to listen on: 127.0.0.1 (this PC only, default) or 0.0.0.0 (any PC)')
    parser.add_argument('--port', type=int, default=5600, help='UDP port (default 5600)')
    parser.add_argument('--raw', action='store_true', help='print every message as JSON')
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.host, args.port))
    print(f'Listening for detections on UDP {args.host}:{args.port} (Ctrl+C to stop)')
    trackers = {'left': SimpleTracker(), 'right': SimpleTracker()}
    last_seq = None
    try:
        while True:
            data, sender = sock.recvfrom(65535)
            try:
                message = json.loads(data.decode('utf-8'))
            except ValueError:
                print(f'Ignored a non-JSON datagram from {sender[0]}')
                continue
            if args.raw:
                print(json.dumps(message, indent=2))
                continue
            if message.get('v') != 1:
                print(f'Unknown message version {message.get("v")!r}; this script reads version 1')
                continue
            clock = time.strftime('%H:%M:%S')
            if message['type'] == 'status':
                print(f'{clock} status from {message["source"]}: detection {message["detection"]}, '
                      f'video {"live" if message["stream_live"] else "not arriving"}')
                continue
            seq = message['seq']
            gap = ''
            if seq is not None and last_seq is not None and seq - last_seq > 1:
                gap = f' (skipped {seq - last_seq - 1})'
            last_seq = seq if seq is not None else last_seq
            capture = message['capture_utc_ms']
            delay = '' if capture is None else f' {message["detected_utc_ms"] - capture["left"]:.0f} ms after capture'
            print(f'{clock} seq {seq}{gap}{delay}')
            for name in ('left', 'right'):
                capture_ms = None if capture is None else capture[name]
                print('   ' + describe_camera(name, message['cameras'][name], trackers[name], capture_ms))
    except KeyboardInterrupt:
        print('Stopped.')


if __name__ == '__main__':
    main()

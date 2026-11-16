#!/usr/bin/env python3
"""Stereo Vision Server v2 multi-client UDP -- Windows / Conda GStreamer, OpenCV and NumPy.

Two independent DirectShow capture pipelines -> latest-frame snapshots ->
side-by-side BGR image + human UTC clock + binary timestamp strip.
Optional NVIDIA NVENC MPEG-TS UDP multi-client unicast publisher (no MediaMTX required).

Timestamp = host time at appsink delivery; NOT hardware exposure time.
To launch:
  conda activate gst-stereo
  python stereo_vision_server_v1.py
  python stereo_vision_server_multiclient.py --clients 192.168.1.248,192.168.1.249
  python stereo_vision_server_multiclient.py --clients-file clients.txt

Dependencies: pygobject, GStreamer, opencv-python, numpy.
"""
import argparse
import ipaddress
from pathlib import Path
import datetime as dt
import os
import struct
import sys
import threading
import time
from dataclasses import dataclass

import cv2
import numpy as np

import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst

Gst.init(None)
MAGIC = 0xA55A
VERSION = 1
FOOTER_H = 100
CELL_W = 6
CELL_H = 16
HEADER_STRUCT = struct.Struct(">HBQIi")  # sync, version, left time ms, seq, right-left us


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def timestamp_text(ns: int) -> str:
    return dt.datetime.fromtimestamp(ns / 1e9, tz=dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " UTC"


@dataclass
class Snapshot:
    pixels: np.ndarray
    captured_ns: int
    count: int


class Camera:
    def __init__(self, index: int, width: int, height: int, fps: int, fmt: str):
        self.index = index
        self.width, self.height, self.fps = width, height, fps
        self.lock = threading.Lock()
        self.latest = None
        self.frames = 0
        if fmt == "mjpeg":
            source = (f'dshowvideosrc device-index={index} do-timestamp=true ! '
                      f'image/jpeg,width={width},height={height},framerate={fps}/1 ! '
                      'jpegdec ! videoconvert ! ')
        else:
            source = (f'dshowvideosrc device-index={index} do-timestamp=true ! '
                      f'video/x-raw,format=YUY2,width={width},height={height},framerate={fps}/1 ! '
                      'videoconvert ! ')
        description = (source +
                       f'video/x-raw,format=BGR,width={width},height={height} ! '
                       'appsink name=grab emit-signals=true sync=false max-buffers=1 drop=true')
        self.pipeline = Gst.parse_launch(description)
        self.sink = self.pipeline.get_by_name("grab")
        self.sink.connect("new-sample", self._on_frame)

    def _on_frame(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            # Caps negotiation fixes BGR format at the requested dimensions.
            # Gst buffers may have padded row strides; VideoMeta provides it if present.
            meta = buffer.get_video_meta() if hasattr(buffer, "get_video_meta") else None
            stride = meta.stride[0] if meta else self.width * 3
            count = self.height * stride
            raw = np.frombuffer(mapped.data, dtype=np.uint8, count=count).reshape(self.height, stride)
            frame = raw[:, :self.width * 3].reshape(self.height, self.width, 3).copy()
            stamped_ns = time.time_ns()  # software arrival timestamp, close to receiving the buffer
        except Exception as exc:
            print(f"Camera {self.index}: buffer error: {exc}", file=sys.stderr)
            return Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(mapped)
        with self.lock:
            self.frames += 1
            self.latest = Snapshot(frame, stamped_ns, self.frames)
        return Gst.FlowReturn.OK

    def read_latest(self):
        with self.lock:
            return self.latest

    def start(self):
        result = self.pipeline.set_state(Gst.State.PLAYING)
        if result == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f"Camera {self.index}: could not enter PLAYING")

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)

    def error(self):
        bus = self.pipeline.get_bus()
        message = bus.pop_filtered(Gst.MessageType.ERROR)
        if message:
            err, debug = message.parse_error()
            return f"Camera {self.index}: {err} ({debug})"
        return None


def parse_clients(entries, port):
    """Parse distinct IPv4 unicast destinations, IP or IP:port."""
    destinations = []
    seen = set()
    for item in entries:
        item = item.strip()
        if not item or item.startswith('#'):
            continue
        parts = item.split(':')
        if len(parts) > 2:
            raise ValueError(f'Invalid destination: {item!r}')
        try:
            host = str(ipaddress.IPv4Address(parts[0]))
            addr = ipaddress.IPv4Address(host)
            if addr.is_multicast or addr.is_unspecified or addr.is_loopback or addr == ipaddress.IPv4Address('255.255.255.255'):
                # Loopback allowed explicitly for convenient same-PC testing.
                if not addr.is_loopback:
                    raise ValueError('multicast / broadcast is not supported')
            dst_port = int(parts[1]) if len(parts) == 2 else port
            if not 1 <= dst_port <= 65535:
                raise ValueError('port must be between 1 and 65535')
        except ValueError as exc:
            raise ValueError(f'Invalid destination {item!r}: {exc}') from exc
        dest = (host, dst_port)
        if dest not in seen:
            destinations.append(dest)
            seen.add(dest)
    return destinations


def read_clients_file(path, default_port):
    lines = Path(path).read_text(encoding='utf-8').splitlines()
    return parse_clients([line.split('#', 1)[0] for line in lines], default_port)


class UdpPublisher:
    """Single NVENC encoder, one MPEG-TS muxer, unicast fan-out at UDP sink."""
    def __init__(self, width: int, height: int, fps: int, destinations, bitrate=3000):
        description = (
            f'appsrc name=source is-live=true format=time block=false do-timestamp=false '
            f'caps="video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1" ! '
            'queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! '
            'videoconvert ! video/x-raw,format=NV12 ! '
            'nvh264enc preset=p1 tune=ultra-low-latency zerolatency=true '
            f'bframes=0 rc-lookahead=0 bitrate={bitrate} gop-size={fps} repeat-sequence-header=true ! '
            'h264parse config-interval=-1 ! video/x-h264,stream-format=byte-stream,alignment=au ! '
            'mpegtsmux alignment=7 ! '
            'multiudpsink name=fanout sync=false async=false send-duplicates=false'
        )
        self.pipeline = Gst.parse_launch(description)
        self.source = self.pipeline.get_by_name('source')
        self.fanout = self.pipeline.get_by_name('fanout')
        if self.fanout is None:
            raise RuntimeError('multiudpsink not available: install gst-plugins-good in gst-stereo')
        self.set_destinations(destinations)
        self.duration = Gst.SECOND // fps
        self.seq = 0

    def set_destinations(self, destinations):
        if not destinations:
            raise ValueError('At least one client is required')
        # GstMultiUDPSink clients is a comma-separated list of host:port destinations.
        value = ','.join(f'{host}:{port}' for host, port in destinations)
        self.fanout.set_property('clients', value)
        self.destinations = list(destinations)

    def start(self):
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('UDP multi-client NVENC pipeline could not start')

    def send(self, frame):
        raw = frame.tobytes()
        gstbuffer = Gst.Buffer.new_allocate(None, len(raw), None)
        gstbuffer.fill(0, raw)
        gstbuffer.pts = self.seq * self.duration
        gstbuffer.duration = self.duration
        self.seq += 1
        ret = self.source.emit('push-buffer', gstbuffer)
        if ret != Gst.FlowReturn.OK:
            print('UDP publisher push returned:', ret, flush=True)

    def error(self):
        bus = self.pipeline.get_bus()
        message = bus.pop_filtered(Gst.MessageType.ERROR)
        if message:
            err, debug = message.parse_error()
            return f'UDP publisher: {err} ({debug})'
        return None

    def stop(self):
        self.pipeline.set_state(Gst.State.NULL)


def draw_binary_strip(image: np.ndarray, left_ns: int, right_ns: int, seq: int):
    """21-byte/168-bit transport-independent visual metadata, with CRC16.

    Structure: > H B Q I i H
       magic(0xA55A), version(1), left UTC ms, combined sequence,
       signed right-minus-left receive time in microseconds, CRC16-CCITT.
    Black bit=1, white bit=0. 6x16 pixel cells; 2 border bars indicate stripe.
    """
    left_ms = left_ns // 1_000_000
    delta_us = max(-2147483648, min(2147483647, (right_ns - left_ns) // 1000))
    payload = HEADER_STRUCT.pack(MAGIC, VERSION, left_ms, seq % (1 << 32), delta_us)
    payload += struct.pack(">H", crc16_ccitt(payload))
    bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    h, w = image.shape[:2]
    x0 = 12
    y0 = h - CELL_H - 11
    if x0 + len(bits) * CELL_W + 12 > w:
        raise RuntimeError("Frame too narrow for binary strip")
    image[y0-3:y0+CELL_H+3, x0-5:x0+len(bits)*CELL_W+5] = (45, 45, 45)
    for i, bit in enumerate(bits):
        x = x0 + i * CELL_W
        image[y0:y0+CELL_H, x:x+CELL_W] = (0, 0, 0) if bit else (255, 255, 255)
    return delta_us


def compose(left: Snapshot, right: Snapshot, seq: int, width: int, height: int):
    canvas = np.zeros((height + FOOTER_H, width * 2, 3), dtype=np.uint8)
    canvas[:height, :width] = left.pixels
    canvas[:height, width:] = right.pixels
    cv2.rectangle(canvas, (0, height), (width * 2, height + FOOTER_H), (35, 35, 35), -1)
    cv2.putText(canvas, "LEFT", (15, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
    cv2.putText(canvas, "RIGHT", (width + 15, 30), cv2.FONT_HERSHEY_SIMPLEX, .8, (255, 255, 255), 2)
    now_ns = time.time_ns()
    cv2.putText(canvas, "L: " + timestamp_text(left.captured_ns), (12, height + 25),
                cv2.FONT_HERSHEY_SIMPLEX, .50, (255, 255, 255), 1, cv2.LINE_AA)
    cv2.putText(canvas, "R: " + timestamp_text(right.captured_ns), (12, height + 48),
                cv2.FONT_HERSHEY_SIMPLEX, .50, (255, 255, 255), 1, cv2.LINE_AA)
    age_l = (now_ns - left.captured_ns) / 1e6
    age_r = (now_ns - right.captured_ns) / 1e6
    cv2.putText(canvas, f"PAIR {seq}   source ages L:{age_l:.0f}ms R:{age_r:.0f}ms",
                (width + 12, height + 25), cv2.FONT_HERSHEY_SIMPLEX, .52,
                (150, 255, 150), 1, cv2.LINE_AA)
    delta_us = draw_binary_strip(canvas, left.captured_ns, right.captured_ns, seq)
    return canvas, age_l, age_r, delta_us


def main():
    parser = argparse.ArgumentParser(description="Dual-camera visual clock server, multi-client UDP unicast")
    parser.add_argument("--left", type=int, default=0)
    parser.add_argument("--right", type=int, default=1)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--format", choices=["mjpeg", "yuy2"], default="mjpeg")
    parser.add_argument("--client-ip", help="Legacy single receiver IP (can combine with --clients)")
    parser.add_argument("--clients", default='', help="Comma-separated unicast IPv4 IPs, optionally IP:port")
    parser.add_argument("--clients-file", help="Text file: one IP or IP:port per line; reloaded every 2 seconds")
    parser.add_argument("--port", type=int, default=5000, help="Default destination UDP port")
    parser.add_argument("--bitrate", type=int, default=3000, help="NVENC bitrate in kbps (default: known-good 3000)")
    parser.add_argument("--no-preview", action="store_true")
    args = parser.parse_args()
    if args.left == args.right:
        parser.error("--left and --right must be different")
    if args.width * 2 < 168 * CELL_W + 24:
        parser.error("Combined video too narrow for the binary stripe; use width >= 516")
    if not 1 <= args.port <= 65535 or args.bitrate < 100:
        parser.error('Invalid --port or --bitrate')
    cli_entries = args.clients.split(',') if args.clients else []
    if args.client_ip:
        cli_entries.append(args.client_ip)
    try:
        fixed_clients = parse_clients(cli_entries, args.port)
        file_clients = read_clients_file(args.clients_file, args.port) if args.clients_file else []
        destinations = list(dict.fromkeys(fixed_clients + file_clients))
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    if not destinations:
        parser.error('Specify --clients, --clients-file, or --client-ip')
    left = Camera(args.left, args.width, args.height, args.fps, args.format)
    right = Camera(args.right, args.width, args.height, args.fps, args.format)
    publisher = UdpPublisher(args.width * 2, args.height + FOOTER_H, args.fps,
                             destinations, args.bitrate)
    next_reload = time.monotonic() + 2.0
    print("Starting cameras...", flush=True)
    try:
        left.start()
        right.start()
        publisher.start()
        print('UDP MPEG-TS NVENC fan-out:', ', '.join(f'{h}:{p}' for h,p in publisher.destinations), flush=True)
        print('168-bit binary timestamp + CRC16 unchanged from working UDP v1', flush=True)
        print("Press Q/Esc to exit, S to save a screenshot.")
        frame_number = 0
        period_ns = int(1e9 / args.fps)
        target_ns = time.monotonic_ns()
        last_report = time.monotonic()
        while True:
            if args.clients_file and time.monotonic() >= next_reload:
                next_reload = time.monotonic() + 2.0
                try:
                    fresh = read_clients_file(args.clients_file, args.port)
                    fresh = list(dict.fromkeys(fixed_clients + fresh))
                    if fresh and fresh != publisher.destinations:
                        publisher.set_destinations(fresh)
                        print('Updated UDP recipients:', ', '.join(f'{h}:{p}' for h,p in fresh), flush=True)
                    elif not fresh:
                        print('Ignoring empty client list; keeping prior recipients', flush=True)
                except (ValueError, OSError) as exc:
                    print('Ignoring invalid clients-file update:', exc, flush=True)
            for component in (left, right, publisher):
                if component:
                    err = component.error()
                    if err:
                        raise RuntimeError(err)
            l, r = left.read_latest(), right.read_latest()
            if l is not None and r is not None:
                frame_number += 1
                frame, age_l, age_r, delta = compose(l, r, frame_number, args.width, args.height)
                publisher.send(frame)
                if not args.no_preview:
                    # Resize preview only; emitted UDP frames remain full-resolution.
                    preview = frame
                    if preview.shape[1] > 1280:
                        scale = 1280 / preview.shape[1]
                        preview = cv2.resize(preview, (1280, round(frame.shape[0] * scale)))
                    cv2.imshow("Stereo Vision UDP Multi-client Server - Q to exit", preview)
                    key = cv2.waitKey(1) & 255
                    if key in (27, ord('q'), ord('Q')):
                        break
                    if key in (ord('s'), ord('S')):
                        name = f"stereo_snapshot_{frame_number:07d}.png"
                        cv2.imwrite(name, frame)
                        print("Saved", name)
                if time.monotonic() - last_report >= 5:
                    print(f"Pair {frame_number} | source ages: L={age_l:.0f}ms R={age_r:.0f}ms | "
                          f"camera callbacks: L={l.count} R={r.count} | delta={delta/1000:.1f}ms", flush=True)
                    last_report = time.monotonic()
            elif not args.no_preview:
                key = cv2.waitKey(1) & 255
                if key in (27, ord('q'), ord('Q')):
                    break
            target_ns += period_ns
            sleep_ns = target_ns - time.monotonic_ns()
            if sleep_ns > 0:
                time.sleep(sleep_ns / 1e9)
            elif sleep_ns < -period_ns * 2:
                target_ns = time.monotonic_ns()  # no catch-up burst
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        publisher.stop()
        left.stop()
        right.stop()
        cv2.destroyAllWindows()
        print("Stopped")


if __name__ == "__main__":
    main()
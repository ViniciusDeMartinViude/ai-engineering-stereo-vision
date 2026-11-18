#!/usr/bin/env python3
"""Stereo Vision Server v2 multi-client UDP -- Windows / Conda GStreamer, OpenCV and NumPy.

Two independent DirectShow capture pipelines -> latest-frame snapshots ->
side-by-side BGR image + human UTC clock + binary timestamp strip.
Optional NVIDIA NVENC MPEG-TS UDP multi-client unicast publisher (no MediaMTX required).

Timestamp = host time at appsink delivery; NOT hardware exposure time.
To launch:
  conda activate gst-stereo
  python stereo_vision_server_multiclient.py --clients 192.168.1.248,192.168.1.249
  python stereo_vision_server_multiclient.py --clients-file clients.txt

Dependencies: pygobject, GStreamer, opencv-python, numpy.
"""
import argparse
import ipaddress
from pathlib import Path

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
from lan_ntp import LanNtpServer
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
    """DirectShow capture with independent per-camera capture-mode fallback.

    The outgoing stream size and fps remain unchanged. Only capture input modes
    vary, and videoscale/convert normalize the successful mode to server output.
    """
    def __init__(self, index: int, width: int, height: int, fps: int, fmt: str):
        self.index = index
        self.width, self.height, self.fps = width, height, fps
        self.lock = threading.Lock()
        self.latest = None
        self.frames = 0
        self.last_frame = 0.0
        self.pipeline = None
        self.sink = None
        self.active_mode = None
        self.preferred_format = fmt
        self.modes = self._build_modes(fmt)

    def _build_modes(self, preferred):
        # Prefer a common native resolution and explicit identical capture FPS.
        # Keep the requested mode first, but prefer standard 30 FPS next;
        # only use unbounded (driver-selected) rates as a final fallback.
        sizes = list(dict.fromkeys([(self.width, self.height), (1280, 720), (640, 480)]))
        formats = [preferred, 'yuy2' if preferred == 'mjpeg' else 'mjpeg']
        rates = list(dict.fromkeys([self.fps, 30, 15, 10]))
        modes = []
        for w, h in sizes:
            for rate in rates:
                for fmt in formats:
                    modes.append((fmt, w, h, rate))
        for w, h in sizes:
            for fmt in formats:
                modes.append((fmt, w, h, None))
        return modes

    @staticmethod
    def mode_label(mode):
        fmt, w, h, rate = mode
        return f'{fmt.upper()} {w}x{h} at {rate if rate is not None else "auto"} FPS'

    def _make_pipeline(self, mode):
        fmt, w, h, rate = mode
        caps = ('image/jpeg' if fmt == 'mjpeg' else 'video/x-raw,format=YUY2')
        caps += f',width={w},height={h}'
        if rate is not None:
            caps += f',framerate={rate}/1'
        decode = 'jpegdec ! ' if fmt == 'mjpeg' else ''
        description = (
            f'dshowvideosrc device-index={self.index} do-timestamp=true ! '
            f'{caps} ! {decode}videoconvert ! videoscale ! '
            f'video/x-raw,format=BGR,width={self.width},height={self.height} ! '
            'appsink name=grab emit-signals=true sync=false max-buffers=1 drop=true'
        )
        self.pipeline = Gst.parse_launch(description)
        self.sink = self.pipeline.get_by_name('grab')
        self.sink.connect('new-sample', self._on_frame)

    def _on_frame(self, sink):
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        try:
            meta = buffer.get_video_meta() if hasattr(buffer, 'get_video_meta') else None
            stride = meta.stride[0] if meta else self.width * 3
            count = self.height * stride
            raw = np.frombuffer(mapped.data, dtype=np.uint8, count=count).reshape(self.height, stride)
            frame = raw[:, :self.width * 3].reshape(self.height, self.width, 3).copy()
            stamped_ns = time.time_ns()
        except Exception as exc:
            print(f'Camera {self.index}: buffer error: {exc}', file=sys.stderr)
            return Gst.FlowReturn.ERROR
        finally:
            buffer.unmap(mapped)
        with self.lock:
            self.frames += 1
            self.latest = Snapshot(frame, stamped_ns, self.frames)
            self.last_frame = time.monotonic()
        return Gst.FlowReturn.OK

    def read_latest(self):
        with self.lock:
            return self.latest

    def error(self):
        if self.pipeline is None:
            return None
        message = self.pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR)
        if message:
            err, debug = message.parse_error()
            return f'Camera {self.index}: {err} ({debug})'
        return None

    def stop(self):
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
            self.pipeline = None
        self.sink = None
        with self.lock:
            self.latest = None
            self.last_frame = 0.0

    def try_mode(self, mode, timeout=1.8, quiet=False):
        """Return (success, diagnostic). A real received frame is required."""
        self.stop()
        label = self.mode_label(mode)
        if not quiet:
            print(f'Camera {self.index}: trying {label}', flush=True)
        try:
            self._make_pipeline(mode)
            result = self.pipeline.set_state(Gst.State.PLAYING)
            if result == Gst.StateChangeReturn.FAILURE:
                return False, 'pipeline could not enter PLAYING'
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                failure = self.error()
                if failure:
                    return False, failure
                if self.read_latest() is not None:
                    self.active_mode = mode
                    if not quiet:
                        print(f'Camera {self.index}: selected {label} -> output '
                              f'{self.width}x{self.height}', flush=True)
                    return True, ''
                time.sleep(0.04)
            return False, f'no frames within {timeout:.1f}s'
        except Exception as exc:
            return False, str(exc)

    def start(self, retry_favorite=False):
        favorite = self.active_mode if retry_favorite else None
        attempts = list(dict.fromkeys(([favorite] if favorite else []) + self.modes))
        previous = None
        for mode in attempts:
            ok, previous = self.try_mode(mode)
            if ok:
                return
        self.stop()
        raise RuntimeError(
            f'Camera {self.index}: no compatible capture mode among {len(attempts)} attempts. '
            f'Last issue: {previous}. Close other camera apps, reconnect USB and '
            'check actual supported modes with FFmpeg -list_options true.'
        )

    def stalled(self, timeout=5.0):
        with self.lock:
            return bool(self.last_frame) and time.monotonic() - self.last_frame > timeout


def negotiate_stereo_pair(left, right, max_attempts=24):
    """Prefer an identical native capture mode for both physical cameras.

    Test each camera sequentially, but require both to deliver real frames
    before accepting the candidate. If no shared mode works, keep independent
    fallbacks available rather than preventing the entire server from running.
    """
    candidates = left.modes[:max_attempts]
    print(f'Stereo negotiation: testing up to {len(candidates)} shared modes', flush=True)
    for mode in candidates:
        label = Camera.mode_label(mode)
        left_ok, left_issue = left.try_mode(mode, timeout=1.25, quiet=True)
        if not left_ok:
            left.stop()
            print(f'  Shared {label}: left unavailable ({left_issue})', flush=True)
            continue
        right_ok, right_issue = right.try_mode(mode, timeout=1.25, quiet=True)
        if right_ok:
            print(f'Stereo negotiated MATCHED capture mode: {label}', flush=True)
            return True
        print(f'  Shared {label}: right unavailable ({right_issue})', flush=True)
        left.stop()
        right.stop()
    print('WARNING: no shared capture mode found; using independent fallbacks. '
          'This may cause stereo timestamp skew.', flush=True)
    left.start(retry_favorite=True)
    right.start(retry_favorite=True)
    print('Capture modes: LEFT=' + Camera.mode_label(left.active_mode) +
          ', RIGHT=' + Camera.mode_label(right.active_mode), flush=True)
    return False


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
    def __init__(self, width: int, height: int, fps: int, destinations, bitrate=3000,
                 multicast=False, multicast_iface=None):
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
        if multicast:
            self.fanout.set_property('auto-multicast', True)
            self.fanout.set_property('ttl-mc', 1)  # local subnet only
            self.fanout.set_property('loop', True)
            if multicast_iface:
                self.fanout.set_property('multicast-iface', multicast_iface)
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

    def stats(self):
        """GStreamer UDP sink counters; bytes-served increases when it sends UDP data."""
        try:
            return (int(self.fanout.get_property('bytes-served')),
                    int(self.fanout.get_property('bytes-to-serve')))
        except (AttributeError, TypeError, ValueError):
            return None

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
    # Larger native-resolution labels remain legible after display downscaling.
    header_scale = max(.8, min(2.0, width / 850.0))
    header_thick = max(2, round(header_scale * 2))
    header_y = max(30, round(34 * header_scale))
    cv2.putText(canvas, "LEFT", (15, header_y), cv2.FONT_HERSHEY_SIMPLEX,
                header_scale, (255, 255, 255), header_thick, cv2.LINE_AA)
    cv2.putText(canvas, "RIGHT", (width + 15, header_y), cv2.FONT_HERSHEY_SIMPLEX,
                header_scale, (255, 255, 255), header_thick, cv2.LINE_AA)
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



def desktop_dimensions():
    """Return desktop dimensions without Qt/Tk."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        try:
            user32.SetProcessDPIAware()
        except (AttributeError, OSError):
            pass
        return max(640, user32.GetSystemMetrics(0)), max(480, user32.GetSystemMetrics(1))
    except (AttributeError, OSError):
        return 1920, 1080


def make_local_preview(frame, width, height, mode='fit', screen=None):
    """Only modifies the server DISPLAY, never the outgoing UDP frame."""
    sw, sh = screen if screen else desktop_dimensions()
    area_w, area_h = max(400, sw - 50), max(300, sh - 120)
    # Keep the 100px telemetry footer (including the 168-bit CRC strip)
    # visible in ALL preview modes. The original frame is never modified.
    if mode == 'left':
        video = np.concatenate((frame[:height, :width, :],
                                frame[height:, :width, :]), axis=0)
    elif mode == 'right':
        # The binary strip occupies the LEFT side of the shared footer.
        # Reuse that footer beneath the right camera rather than cropping it.
        video = np.concatenate((frame[:height, width:, :],
                                frame[height:, :width, :]), axis=0)
    else:
        video = frame
    factor = min(area_w / video.shape[1], (area_h - 76) / video.shape[0], 1.0)
    size = (max(1, round(video.shape[1] * factor)),
            max(1, round(video.shape[0] * factor)))
    if size != (video.shape[1], video.shape[0]):
        video = cv2.resize(video, size, interpolation=cv2.INTER_AREA)
    h, w = video.shape[:2]
    preview = np.zeros((h + 76, w, 3), dtype=np.uint8)
    preview[:h, :w] = video
    cv2.putText(preview, f'VIEW: {mode.upper()}  |  1: BOTH   2: LEFT   3: RIGHT',
                (14, h + 29), cv2.FONT_HERSHEY_SIMPLEX, .76, (245, 245, 245), 2, cv2.LINE_AA)
    cv2.putText(preview, 'F: fullscreen   S: save original   Q/Esc: quit',
                (14, h + 59), cv2.FONT_HERSHEY_SIMPLEX, .68, (210, 230, 210), 2, cv2.LINE_AA)
    return preview


def main():
    parser = argparse.ArgumentParser(description="Dual-camera visual clock server, multi-client UDP unicast")
    parser.add_argument("--left", type=int, default=0)
    parser.add_argument("--right", type=int, default=1)
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=10)
    parser.add_argument("--format", choices=["mjpeg", "yuy2"], default="mjpeg")
    parser.add_argument("--transport", choices=["multicast", "unicast"], default="multicast",
                        help="Default multicast uses one shared stream; unicast uses clients.txt")
    parser.add_argument("--multicast-group", default="239.10.10.10",
                        help="IPv4 multicast address (default 239.10.10.10)")
    parser.add_argument("--multicast-iface", default=None,
                        help="Optional GStreamer interface name for multicast output")
    parser.add_argument("--client-ip", help="Legacy single receiver IP (can combine with --clients)")
    parser.add_argument("--clients", default='', help="Comma-separated unicast IPv4 IPs, optionally IP:port")
    parser.add_argument("--clients-file", help="Text file: one IP or IP:port per line; reloaded every 2 seconds")
    parser.add_argument("--port", type=int, default=5000, help="Default destination UDP port")
    parser.add_argument("--bitrate", type=int, default=3000, help="NVENC bitrate in kbps (default: known-good 3000)")
    parser.add_argument("--ntp-host", default="0.0.0.0",
                        help="Bind address for LAN SNTP clock service")
    parser.add_argument("--ntp-port", type=int, default=9123,
                        help="LAN SNTP port (default 9123; 123 can conflict with Windows Time)")
    parser.add_argument("--no-ntp", action="store_true",
                        help="Disable LAN SNTP clock service")
    parser.add_argument("--no-preview", action="store_true")
    parser.add_argument("--preview-mode", choices=['fit','left','right'], default='fit',
                        help='Local preview layout; changes only the server display')
    args = parser.parse_args()
    if args.left == args.right:
        parser.error("--left and --right must be different")
    if args.width * 2 < 168 * CELL_W + 24:
        parser.error("Combined video too narrow for the binary stripe; use width >= 516")
    if not 1 <= args.port <= 65535 or args.bitrate < 100:
        parser.error('Invalid --port or --bitrate')
    if args.transport == 'multicast':
        try:
            group = ipaddress.IPv4Address(args.multicast_group)
            if not group.is_multicast:
                parser.error('--multicast-group must be an IPv4 multicast address')
        except ipaddress.AddressValueError as exc:
            parser.error(str(exc))
        if args.clients_file or args.clients or args.client_ip:
            print('Multicast mode: ignoring unicast client list. Use --transport unicast to enable it.',
                  flush=True)
        fixed_clients = []
        destinations = [(str(group), args.port)]
    else:
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
            parser.error('Unicast mode requires --clients, --clients-file, or --client-ip')
    left = Camera(args.left, args.width, args.height, args.fps, args.format)
    right = Camera(args.right, args.width, args.height, args.fps, args.format)
    publisher = UdpPublisher(args.width * 2, args.height + FOOTER_H, args.fps,
                             destinations, args.bitrate,
                             multicast=args.transport == 'multicast',
                             multicast_iface=args.multicast_iface)
    next_reload = time.monotonic() + 2.0
    ntp_service = None
    print("Starting cameras...", flush=True)
    try:
        if not args.no_ntp:
            ntp_service = LanNtpServer(args.ntp_host, args.ntp_port)
            try:
                ntp_service.start()
            except OSError as exc:
                print(f"WARNING: LAN SNTP service unavailable: {exc}. "
                      f"Try --ntp-port 9123 or check Windows Firewall.", flush=True)
                ntp_service = None
        negotiate_stereo_pair(left, right)
        publisher.start()
        print('UDP '+args.transport.upper()+' MPEG-TS NVENC destination:', ', '.join(f'{h}:{p}' for h,p in publisher.destinations), flush=True)
        print('168-bit binary timestamp + CRC16 unchanged from working UDP v1', flush=True)
        print("Preview: 1 both, 2 left, 3 right, F fullscreen, S screenshot, Q/Esc quit.")
        preview_mode = args.preview_mode
        fullscreen = False
        preview_initialized = False
        window_title = 'Stereo Vision UDP Multi-client Server'
        if not args.no_preview:
            cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
            # Windows maximized window retains visible taskbar and border.
            cv2.setWindowProperty(window_title, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
            try:
                cv2.setWindowProperty(window_title, cv2.WND_PROP_AUTOSIZE, 0)
                cv2.resizeWindow(window_title, *desktop_dimensions())
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(None, window_title)
                if hwnd:
                    ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            except (OSError, AttributeError):
                pass
        frame_number = 0
        period_ns = int(1e9 / args.fps)
        target_ns = time.monotonic_ns()
        last_report = time.monotonic()
        previous_udp_bytes = 0
        retry_after = {left.index: 0.0, right.index: 0.0}
        last_counts = {left.index: (left.frames, time.monotonic()),
                       right.index: (right.frames, time.monotonic())}
        while True:
            if args.transport == 'unicast' and args.clients_file and time.monotonic() >= next_reload:
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
            for camera in (left, right):
                err = camera.error()
                if camera.stalled():
                    err = err or f'Camera {camera.index}: no frames for 5 seconds'
                if camera.pipeline is None and time.monotonic() >= retry_after[camera.index]:
                    err = err or f'Camera {camera.index}: disconnected; retrying'
                if err and time.monotonic() >= retry_after[camera.index]:
                    print(f'{err}\nAttempting automatic camera recovery...', flush=True)
                    try:
                        camera.start(retry_favorite=True)
                        print(f'Camera {camera.index}: recovery succeeded', flush=True)
                    except RuntimeError as exc:
                        print(f'Camera {camera.index}: {exc}', flush=True)
                        retry_after[camera.index] = time.monotonic() + 8.0
            publisher_error = publisher.error()
            if publisher_error:
                raise RuntimeError(publisher_error)
            l, r = left.read_latest(), right.read_latest()
            if l is not None and r is not None:
                frame_number += 1
                frame, age_l, age_r, delta = compose(l, r, frame_number, args.width, args.height)
                publisher.send(frame)
                if not args.no_preview:
                    # Preview controls never change original encoded video / CRC footer.
                    preview = make_local_preview(frame, args.width, args.height, preview_mode)
                    cv2.imshow(window_title, preview)
                    if not preview_initialized:
                        preview_initialized = True
                    key = cv2.waitKey(1) & 255
                    if key in (ord('1'), ord('2'), ord('3')):
                        preview_mode = {ord('1'): 'fit', ord('2'): 'left', ord('3'): 'right'}[key]
                    if key in (ord('f'), ord('F')):
                        fullscreen = not fullscreen
                        cv2.setWindowProperty(window_title, cv2.WND_PROP_FULLSCREEN,
                                              cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
                    if key in (27, ord('q'), ord('Q')):
                        break
                    if key in (ord('s'), ord('S')):
                        name = f"stereo_snapshot_{frame_number:07d}.png"
                        cv2.imwrite(name, frame)
                        print("Saved", name)
                if time.monotonic() - last_report >= 5:
                    now_report = time.monotonic()
                    left_fps = (left.frames - last_counts[left.index][0]) / max(
                        .001, now_report - last_counts[left.index][1])
                    right_fps = (right.frames - last_counts[right.index][0]) / max(
                        .001, now_report - last_counts[right.index][1])
                    traffic = publisher.stats()
                    if traffic is not None:
                        udp_bytes, queued_bytes = traffic
                        rate = max(0, udp_bytes - previous_udp_bytes) * 8 / max(.001, now_report - last_report) / 1e6
                        previous_udp_bytes = udp_bytes
                        traffic_note = f" | UDP sink sent={udp_bytes}B rate={rate:.2f}Mbps"
                    else:
                        traffic_note = ''
                    print(f"Pair {frame_number} | source ages: L={age_l:.0f}ms R={age_r:.0f}ms | "
                          f"camera measured FPS: L={left_fps:.1f} R={right_fps:.1f} | "
                          f"callbacks: L={l.count} R={r.count} | skew={delta/1000:.1f}ms"
                          f"{traffic_note}", flush=True)
                    last_counts = {left.index: (left.frames, now_report),
                                   right.index: (right.frames, now_report)}
                    last_report = now_report
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
        if ntp_service is not None:
            ntp_service.stop()
        publisher.stop()
        left.stop()
        right.stop()
        cv2.destroyAllWindows()
        print("Stopped")


if __name__ == "__main__":
    main()
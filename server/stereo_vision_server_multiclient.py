#!/usr/bin/env python3
"""Stereo Vision Server -- Windows / Conda GStreamer, OpenCV and NumPy.

Two independent DirectShow capture pipelines -> latest-frame snapshots ->
side-by-side BGR image + human UTC clock + binary timestamp strip ->
one NVIDIA NVENC H.264 MPEG-TS stream over UDP multicast (default) or a
unicast fan-out list. No MediaMTX required.

Capture, compositing and sending run on their own thread (ServerEngine), so
the operator window can never delay the stream. The window is the Qt server
console (same design as the Stereo Vision Studio client); --preview opencv
keeps the older OpenCV window and --no-preview runs headless.

Timestamp = host time at appsink delivery; NOT hardware exposure time.
To launch:
  conda activate gst-stereo-qt312
  python server/stereo_vision_server_multiclient.py
  python server/stereo_vision_server_multiclient.py --transport unicast --clients-file clients.txt
  python server/stereo_vision_server_multiclient.py --self-test

Dependencies: pygobject, GStreamer, opencv, numpy; PySide6 + pyqtgraph for the console.
"""
import argparse
import collections
import datetime as dt
import ipaddress
import math
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
from lan_ntp import LanNtpServer  # noqa: E402
from stamp_protocol import FOOTER_H, STRIP_WIDTH, encode_strip, extract_strip  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import gi  # noqa: E402
gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

Gst.init(None)


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
    def __init__(self, index: int, width: int, height: int, fps: int, fmt: str, log=print):
        self.index = index
        self.width, self.height, self.fps = width, height, fps
        self.log = log
        self.lock = threading.Lock()
        self.latest = None
        self.frame_event = threading.Event()
        self.frames = 0
        self.last_frame = 0.0
        self.pipeline = None
        self.sink = None
        self.active_mode = None
        self.recovering = False
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
        if mode is None:
            return 'no capture mode'
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
        self.frame_event.set()
        return Gst.FlowReturn.OK

    def read_latest(self):
        with self.lock:
            return self.latest

    def seconds_since_frame(self):
        """Seconds since the last delivered frame, or None before the first one."""
        with self.lock:
            return time.monotonic() - self.last_frame if self.last_frame else None

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
        self.frame_event.clear()

    def try_mode(self, mode, timeout=1.8, quiet=False):
        """Return (success, diagnostic). A real received frame is required."""
        self.stop()
        label = self.mode_label(mode)
        if not quiet:
            self.log(f'Camera {self.index}: trying {label}')
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
                        self.log(f'Camera {self.index}: selected {label} -> output '
                                 f'{self.width}x{self.height}')
                    return True, ''
                self.frame_event.wait(0.04)
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


def negotiate_stereo_pair(left, right, max_attempts=24, log=print):
    """Prefer an identical native capture mode for both physical cameras.

    Test each camera sequentially, but require both to deliver real frames
    before accepting the candidate. If no shared mode works, keep independent
    fallbacks available rather than preventing the entire server from running.
    """
    candidates = left.modes[:max_attempts]
    log(f'Stereo negotiation: testing up to {len(candidates)} shared modes')
    for mode in candidates:
        label = Camera.mode_label(mode)
        left_ok, left_issue = left.try_mode(mode, timeout=1.25, quiet=True)
        if not left_ok:
            left.stop()
            log(f'  Shared {label}: left unavailable ({left_issue})')
            continue
        right_ok, right_issue = right.try_mode(mode, timeout=1.25, quiet=True)
        if right_ok:
            log(f'Stereo negotiated MATCHED capture mode: {label}')
            return True
        log(f'  Shared {label}: right unavailable ({right_issue})')
        left.stop()
        right.stop()
    log('WARNING: no shared capture mode found; using independent fallbacks. '
        'This may cause stereo timestamp skew.')
    left.start(retry_favorite=True)
    right.start(retry_favorite=True)
    log('Capture modes: LEFT=' + Camera.mode_label(left.active_mode) +
        ', RIGHT=' + Camera.mode_label(right.active_mode))
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
        gstbuffer = Gst.Buffer.new_wrapped(frame.tobytes())
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


_canvas_cache = {}


def compose(left: Snapshot, right: Snapshot, seq: int, width: int, height: int):
    # Reuse one canvas: the publisher copies it out (tobytes) and the engine
    # copies it for the operator preview, so nothing holds a reference between frames.
    shape = (height + FOOTER_H, width * 2, 3)
    canvas = _canvas_cache.get(shape)
    if canvas is None:
        canvas = _canvas_cache[shape] = np.zeros(shape, dtype=np.uint8)
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
    delta_us = encode_strip(canvas, left.captured_ns, right.captured_ns, seq)
    return canvas, age_l, age_r, delta_us


class ServerEngine:
    """Capture -> compose -> NVENC/UDP publish loop on a background thread.

    The pixel path, timestamp strip, pacing and camera recovery are the same as
    the original single-loop server. Views (Qt console, OpenCV preview, headless)
    only read ``status()`` and ``latest_frame()``; they never touch the pipelines.
    """
    SKEW_TAU_S = 1.5  # display smoothing for the console, as in the client

    def __init__(self, args, destinations, fixed_clients=()):
        self.args = args
        self.fixed_clients = list(fixed_clients)
        self.lock = threading.Lock()
        self.events = collections.deque(maxlen=400)
        self.event_count = 0
        self.phase = 'starting'  # starting -> negotiating -> running -> stopped | failed
        self.error = None
        self.matched = None
        self.frame_number = 0
        self.samples = collections.deque(maxlen=2000)  # (older source age ms, skew R-L ms)
        self.sent_times = collections.deque(maxlen=120)
        self.last_pair = None
        self.skew_smoothed = None
        self._skew_time = None
        self.camera_fps = {'left': 0.0, 'right': 0.0}
        self.udp_bytes = 0
        self.udp_rate_mbps = None
        self.started_mono = time.monotonic()
        self.running_since = None
        self._preview = None
        self._preview_id = 0
        self._last_preview = 0.0
        self.preview_period = 1.0 / max(1.0, float(getattr(args, 'preview_fps', 15)))
        self._stop = threading.Event()
        self.thread = None
        self.ntp_service = None
        self.ntp_error = None
        self.left = Camera(args.left, args.width, args.height, args.fps, args.format, log=self.log)
        self.right = Camera(args.right, args.width, args.height, args.fps, args.format, log=self.log)
        self.publisher = UdpPublisher(args.width * 2, args.height + FOOTER_H, args.fps,
                                      destinations, args.bitrate,
                                      multicast=args.transport == 'multicast',
                                      multicast_iface=args.multicast_iface)

    # ---- logging and lifecycle -------------------------------------------------
    def log(self, message, echo=True):
        """Print to the terminal (unless echo=False) and keep it for the console's event log."""
        if echo:
            print(message, flush=True)
        with self.lock:
            self.events.append((time.time(), str(message)))
            self.event_count += 1

    def start(self):
        self.thread = threading.Thread(target=self._run, name='stereo-server-engine', daemon=True)
        self.thread.start()

    def stop(self, timeout=8.0):
        self._stop.set()
        if self.thread is not None and self.thread.is_alive() and self.thread is not threading.current_thread():
            self.thread.join(timeout)

    def is_alive(self):
        return self.thread is not None and self.thread.is_alive()

    def _run(self):
        try:
            self._start_services()
            if not self._stop.is_set():
                self._loop()
            with self.lock:
                self.phase = 'stopped'
        except Exception as exc:  # keep the console open and show why
            with self.lock:
                self.phase = 'failed'
                self.error = str(exc)
            self.log(f'ERROR: {exc}')
        finally:
            self._shutdown()

    def _start_services(self):
        a = self.args
        self.log('Starting cameras...')
        if not a.no_ntp:
            service = LanNtpServer(a.ntp_host, a.ntp_port)
            try:
                service.start()
                self.ntp_service = service
                # LanNtpServer already printed its own line; record it for the event log only.
                self.log(f'LAN SNTP clock service listening on UDP {a.ntp_host}:{a.ntp_port}', echo=False)
            except OSError as exc:
                self.ntp_error = str(exc)
                self.log(f'WARNING: LAN SNTP service unavailable: {exc}. '
                         'Try another --ntp-port or check Windows Firewall.')
        with self.lock:
            self.phase = 'negotiating'
        matched = negotiate_stereo_pair(self.left, self.right, log=self.log)
        with self.lock:
            self.matched = matched
        self.publisher.start()
        self.log(f'UDP {a.transport.upper()} MPEG-TS NVENC destination: ' +
                 ', '.join(f'{h}:{p}' for h, p in self.publisher.destinations))
        self.log('168-bit binary timestamp + CRC16 unchanged from working UDP v1')
        with self.lock:
            self.phase = 'running'
            self.running_since = time.monotonic()

    def _shutdown(self):
        if self.ntp_service is not None:
            self.ntp_service.stop()
        for part in (self.publisher, self.left, self.right):
            try:
                part.stop()
            except Exception as exc:  # pragma: no cover - best effort on exit
                print(f'Shutdown issue: {exc}', file=sys.stderr)

    # ---- the send loop ---------------------------------------------------------
    def _reload_clients(self):
        a = self.args
        try:
            fresh = read_clients_file(a.clients_file, a.port)
            fresh = list(dict.fromkeys(self.fixed_clients + fresh))
            if fresh and fresh != self.publisher.destinations:
                self.publisher.set_destinations(fresh)
                self.log('Updated UDP recipients: ' + ', '.join(f'{h}:{p}' for h, p in fresh))
            elif not fresh:
                self.log('Ignoring empty client list; keeping prior recipients')
        except (ValueError, OSError) as exc:
            self.log(f'Ignoring invalid clients-file update: {exc}')

    def _check_camera(self, camera, retry_after):
        now = time.monotonic()
        err = camera.error()
        if camera.stalled():
            err = err or f'Camera {camera.index}: no frames for 5 seconds'
        if camera.pipeline is None and now >= retry_after[camera.index]:
            err = err or f'Camera {camera.index}: disconnected; retrying'
        if err and now >= retry_after[camera.index]:
            self.log(f'{err}\nAttempting automatic camera recovery...')
            camera.recovering = True
            try:
                camera.start(retry_favorite=True)
                self.log(f'Camera {camera.index}: recovery succeeded')
            except RuntimeError as exc:
                self.log(f'Camera {camera.index}: {exc}')
                retry_after[camera.index] = time.monotonic() + 8.0
            finally:
                camera.recovering = False

    def _loop(self):
        a = self.args
        left, right, publisher = self.left, self.right, self.publisher
        period_ns = int(1e9 / a.fps)
        target_ns = time.monotonic_ns()
        next_reload = time.monotonic() + 2.0
        retry_after = {left.index: 0.0, right.index: 0.0}
        last_rate = last_report = time.monotonic()
        rate_counts = (left.frames, right.frames)
        report_counts = (left.frames, right.frames)
        previous_bytes = 0
        report_bytes = 0
        while not self._stop.is_set():
            if a.transport == 'unicast' and a.clients_file and time.monotonic() >= next_reload:
                next_reload = time.monotonic() + 2.0
                self._reload_clients()
            for camera in (left, right):
                self._check_camera(camera, retry_after)
            publisher_error = publisher.error()
            if publisher_error:
                raise RuntimeError(publisher_error)
            l, r = left.read_latest(), right.read_latest()
            if l is not None and r is not None:
                seq = self.frame_number + 1
                frame, age_l, age_r, delta = compose(l, r, seq, a.width, a.height)
                publisher.send(frame)
                now = time.monotonic()
                skew_ms = delta / 1000.0
                with self.lock:
                    self.frame_number = seq
                    self.sent_times.append(now)
                    self.samples.append((max(age_l, age_r), skew_ms))
                    self.last_pair = {
                        'sequence': seq, 'left_ns': l.captured_ns, 'right_ns': r.captured_ns,
                        'age_l': age_l, 'age_r': age_r, 'skew_ms': skew_ms,
                        'left_count': l.count, 'right_count': r.count}
                    if self.skew_smoothed is None:
                        self.skew_smoothed = skew_ms
                    else:
                        weight = -math.expm1(-max(0.0, now - self._skew_time) / self.SKEW_TAU_S)
                        self.skew_smoothed += weight * (skew_ms - self.skew_smoothed)
                    self._skew_time = now
                    if now - self._last_preview >= self.preview_period:
                        # A private copy: the compose canvas is reused next frame.
                        self._preview = frame.copy()
                        self._preview_id = seq
                        self._last_preview = now
            now = time.monotonic()
            if now - last_rate >= 1.0:
                elapsed = now - last_rate
                traffic = publisher.stats()
                with self.lock:
                    self.camera_fps = {'left': (left.frames - rate_counts[0]) / elapsed,
                                       'right': (right.frames - rate_counts[1]) / elapsed}
                    if traffic is not None:
                        self.udp_bytes = traffic[0]
                        self.udp_rate_mbps = max(0, traffic[0] - previous_bytes) * 8 / elapsed / 1e6
                        previous_bytes = traffic[0]
                rate_counts = (left.frames, right.frames)
                last_rate = now
            if now - last_report >= 5 and self.last_pair is not None:
                elapsed = max(.001, now - last_report)
                pair = self.last_pair
                traffic_note = ''
                if publisher.stats() is not None:
                    rate = max(0, self.udp_bytes - report_bytes) * 8 / elapsed / 1e6
                    report_bytes = self.udp_bytes
                    traffic_note = f" | UDP sink sent={self.udp_bytes}B rate={rate:.2f}Mbps"
                print(f"Pair {pair['sequence']} | source ages: L={pair['age_l']:.0f}ms R={pair['age_r']:.0f}ms | "
                      f"camera measured FPS: L={(left.frames - report_counts[0]) / elapsed:.1f} "
                      f"R={(right.frames - report_counts[1]) / elapsed:.1f} | "
                      f"callbacks: L={pair['left_count']} R={pair['right_count']} | "
                      f"skew={pair['skew_ms']:.1f}ms{traffic_note}", flush=True)
                report_counts = (left.frames, right.frames)
                last_report = now
            target_ns += period_ns
            sleep_ns = target_ns - time.monotonic_ns()
            if sleep_ns > 0:
                self._stop.wait(sleep_ns / 1e9)
            elif sleep_ns < -period_ns * 2:
                target_ns = time.monotonic_ns()  # no catch-up burst

    # ---- read-only views for the console ---------------------------------------
    def latest_frame(self):
        """(sequence, full-resolution outgoing frame copy) or (0, None)."""
        with self.lock:
            return self._preview_id, self._preview

    def _camera_status(self, camera, side):
        since = camera.seconds_since_frame()
        return {'index': camera.index, 'mode': camera.active_mode,
                'mode_label': Camera.mode_label(camera.active_mode) if camera.active_mode else None,
                'since_frame': since, 'recovering': camera.recovering,
                'open': camera.pipeline is not None, 'frames': camera.frames,
                'fps': self.camera_fps.get(side, 0.0)}

    def status(self):
        a = self.args
        now = time.monotonic()
        cameras = {'left': self._camera_status(self.left, 'left'),
                   'right': self._camera_status(self.right, 'right')}
        with self.lock:
            sent = list(self.sent_times)
            output_fps = 0.0
            if len(sent) > 1 and now - sent[-1] < 1.0:
                output_fps = (len(sent) - 1) / max(1e-6, sent[-1] - sent[0])
            samples = list(self.samples)
            return {
                'phase': self.phase, 'error': self.error, 'matched': self.matched,
                'frame_number': self.frame_number, 'last_pair': dict(self.last_pair) if self.last_pair else None,
                'since_send': (now - sent[-1]) if sent else None,
                'output_fps': output_fps, 'target_fps': a.fps,
                'ages': [s[0] for s in samples], 'skews': [s[1] for s in samples],
                'skew_smoothed': self.skew_smoothed,
                'cameras': cameras,
                'udp_bytes': self.udp_bytes, 'udp_rate_mbps': self.udp_rate_mbps,
                'running_for': (now - self.running_since) if self.running_since else None,
                'transport': a.transport, 'destinations': list(self.publisher.destinations),
                'bitrate_kbps': a.bitrate, 'width': a.width, 'height': a.height,
                'ntp': {'enabled': not a.no_ntp, 'port': a.ntp_port,
                        'running': self.ntp_service is not None,
                        'requests': self.ntp_service.requests if self.ntp_service else 0,
                        'error': self.ntp_error or (self.ntp_service.last_error if self.ntp_service else None)},
                'event_count': self.event_count, 'events': list(self.events),
            }

    def save_snapshot(self, directory='.'):
        """Write the latest full-resolution outgoing frame as PNG; returns the path or None."""
        seq, frame = self.latest_frame()
        if frame is None:
            return None
        path = Path(directory) / f"stereo_snapshot_{seq:07d}.png"
        if not cv2.imwrite(str(path), frame):
            return None
        self.log(f'Saved {path}')
        return str(path)


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


def make_local_preview(frame, width, height, mode='fit', screen=None,
                       left_ns=None, right_ns=None):
    """Legacy OpenCV HUD (--preview opencv) with an enlarged copy of the binary strip."""
    sw, sh = screen if screen else desktop_dimensions()
    area_w = max(450, sw - 50)
    area_h = max(320, sh - 120)
    if mode == 'left':
        video = frame[:height, :width, :]
    elif mode == 'right':
        video = frame[:height, width:, :]
    else:
        video = frame[:height, :, :]
    # Reserve space for LARGE text and the binary strip; never change UDP data.
    footer_h = 172
    factor = min(area_w / video.shape[1],
                 max(120, area_h - footer_h) / video.shape[0], 1.0)
    vw = max(1, round(video.shape[1] * factor))
    vh = max(1, round(video.shape[0] * factor))
    if (vw, vh) != (video.shape[1], video.shape[0]):
        video = cv2.resize(video, (vw, vh), interpolation=cv2.INTER_AREA)
    display_w = max(vw, min(area_w, 1050))
    preview = np.full((vh + footer_h, display_w, 3), (24, 24, 24), dtype=np.uint8)
    preview[:vh, (display_w-vw)//2:(display_w-vw)//2+vw] = video
    font = cv2.FONT_HERSHEY_SIMPLEX
    if left_ns is not None and right_ns is not None:
        cv2.putText(preview, 'LEFT  ' + timestamp_text(left_ns),
                    (14, vh+28), font, .68, (250, 250, 250), 2, cv2.LINE_AA)
        cv2.putText(preview, 'RIGHT ' + timestamp_text(right_ns),
                    (14, vh+57), font, .68, (250, 250, 250), 2, cv2.LINE_AA)
    else:
        cv2.putText(preview, 'LIVE STEREO TIMESTAMP', (14, vh+32), font,
                    .78, (250, 250, 250), 2, cv2.LINE_AA)
    cv2.putText(preview, 'BINARY TIMESTAMP + CRC16 (168 bits)', (14, vh+82),
                font, .61, (190, 235, 250), 2, cv2.LINE_AA)
    roi = extract_strip(frame)
    if roi is not None:
        bar_w = max(1, display_w-28)
        barcode = cv2.resize(roi, (bar_w, 36), interpolation=cv2.INTER_NEAREST)
        preview[vh+90:vh+126, 14:14+bar_w] = barcode
    cv2.putText(preview, f'{mode.upper()}   1: BOTH  2: LEFT  3: RIGHT   '
                'F: FULLSCREEN  S: SAVE  Q: QUIT', (14, vh+153),
                font, .55, (215, 215, 215), 1, cv2.LINE_AA)
    return preview


def run_opencv_preview(engine, args):
    """The pre-console OpenCV window, kept as a fallback when PySide6 is missing."""
    window_title = 'Stereo Vision UDP Multi-client Server'
    cv2.namedWindow(window_title, cv2.WINDOW_NORMAL | cv2.WINDOW_KEEPRATIO)
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
    print("Preview: 1 both, 2 left, 3 right, F fullscreen, S screenshot, Q/Esc quit.")
    mode = args.preview_mode
    fullscreen = False
    shown = -1
    while engine.is_alive():
        seq, frame = engine.latest_frame()
        if frame is not None and seq != shown:
            shown = seq
            pair = engine.status()['last_pair'] or {}
            cv2.imshow(window_title, make_local_preview(
                frame, args.width, args.height, mode,
                left_ns=pair.get('left_ns'), right_ns=pair.get('right_ns')))
        key = cv2.waitKey(15) & 255
        if key in (ord('1'), ord('2'), ord('3')):
            mode = {ord('1'): 'fit', ord('2'): 'left', ord('3'): 'right'}[key]
        elif key in (ord('f'), ord('F')):
            fullscreen = not fullscreen
            cv2.setWindowProperty(window_title, cv2.WND_PROP_FULLSCREEN,
                                  cv2.WINDOW_FULLSCREEN if fullscreen else cv2.WINDOW_NORMAL)
        elif key in (ord('s'), ord('S')):
            engine.save_snapshot()
        elif key in (27, ord('q'), ord('Q')):
            break
    cv2.destroyAllWindows()


def run_headless(engine):
    while engine.is_alive():
        engine.thread.join(0.5)


def build_parser():
    parser = argparse.ArgumentParser(description="Stereo Vision Server: dual-camera visual clock, "
                                                 "NVENC MPEG-TS over UDP multicast or unicast")
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
    parser.add_argument("--no-preview", action="store_true",
                        help="Run headless: no window, console log only (Ctrl+C to stop)")
    parser.add_argument("--preview", choices=['qt', 'opencv'], default='qt',
                        help="Operator window: the Qt server console (default) or the legacy OpenCV preview")
    parser.add_argument("--preview-mode", choices=['fit', 'left', 'right'], default='fit',
                        help='Initial camera layout in the window; changes only the server display')
    parser.add_argument("--preview-fps", type=float, default=15.0,
                        help='Maximum operator preview refresh rate; never affects the stream')
    parser.add_argument("--self-test", action="store_true",
                        help="Check the Qt console with synthetic frames (no cameras, encoder or network)")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    if args.self_test:
        from server_console_qt import self_test
        raise SystemExit(self_test())
    if args.left == args.right:
        parser.error("--left and --right must be different")
    if args.width * 2 < STRIP_WIDTH + 24:
        parser.error("Combined video too narrow for the binary stripe; use width >= 516")
    if not 1 <= args.port <= 65535 or args.bitrate < 100:
        parser.error('Invalid --port or --bitrate')
    if args.fps < 1 or args.preview_fps <= 0:
        parser.error('--fps and --preview-fps must be positive')
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

    view = 'none' if args.no_preview else args.preview
    run_console = None
    if view == 'qt':
        try:
            from server_console_qt import run_console
        except ImportError as exc:
            print(f'Qt server console unavailable ({exc}); using the OpenCV preview. '
                  'Install PySide6 + pyqtgraph (environment-qt312.yml) for the console.', flush=True)
            view = 'opencv'

    engine = ServerEngine(args, destinations, fixed_clients)
    engine.start()
    try:
        if view == 'qt':
            run_console(engine, args)
        elif view == 'opencv':
            run_opencv_preview(engine, args)
        else:
            run_headless(engine)
    except KeyboardInterrupt:
        print("Interrupted")
    finally:
        engine.stop()
        print("Stopped")
    if engine.phase == 'failed':
        raise SystemExit(1)


if __name__ == "__main__":
    main()

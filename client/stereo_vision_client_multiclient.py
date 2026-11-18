#!/usr/bin/env python3
"""Stereo Vision UDP Multi-client Client v7 — decodes the visual timestamp from Stereo Vision Server v1.

Expected source: stereo_vision_server_v1.py (21-byte binary strip, UDP MPEG-TS H.264).
Requires: Python + GStreamer GI bindings + GStreamer plugins + numpy + opencv.

Windows:
    conda activate gst-stereo
    python stereo_vision_client_v6.py --self-test
    python stereo_vision_client_v6.py --port 5000

IMPORTANT: one-way latency is meaningful only when computer clocks are synchronized.
If the client clock reads 40 ms ahead of the server, pass --clock-offset-ms 40.
The server timestamps at frame ARRIVAL at its appsink, not camera exposure.
"""
import argparse
import collections
import datetime as dt
import math
import socket
import statistics
import struct
import sys
import threading
import time
import traceback
from pathlib import Path
import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
from lan_ntp import measure_ntp_offset
import tkinter as tk
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageTk

MAGIC = 0xA55A
VERSION = 1
CELL_W = 6
CELL_H = 16
BIT_COUNT = 168
X0 = 12
Y_BOTTOM_OFFSET = 11 + CELL_H
PACKET_STRUCT = struct.Struct('>HBQIiH')


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for octet in data:
        crc ^= octet << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


@dataclass(frozen=True)
class Stamp:
    left_utc_ms: int
    sequence: int
    right_minus_left_us: int

    @property
    def right_utc_ms(self):
        return self.left_utc_ms + self.right_minus_left_us / 1000.0


def decode_strip(frame: np.ndarray):
    """Returns (Stamp or None, error_string or None), using raw decoded pixels.

    The server encodes bits MSB-first, black=1, white=0, at x=12,
    y=frame_height-27, 6x16 pixels/cell. Requires unscaled original frame.
    """
    h, w = frame.shape[:2]
    y0 = h - Y_BOTTOM_OFFSET
    if w < X0 + BIT_COUNT * CELL_W or y0 < 2:
        return None, f'frame too small: {w}x{h}'
    # Sample central 3x8 pixels in each cell, avoiding H264 block edges.
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    region = gray[y0+4:y0+12, X0:X0+BIT_COUNT*CELL_W]
    if region.shape != (8, BIT_COUNT * CELL_W):
        return None, 'strip outside image'
    cells = region.reshape(8, BIT_COUNT, CELL_W)
    centers = cells[:, :, 2:5].mean(axis=(0, 2))
    # A per-frame adaptive mid-point tolerates changed brightness/compression.
    black, white = float(np.percentile(centers, 8)), float(np.percentile(centers, 92))
    if white - black < 45:
        return None, f'insufficient strip contrast ({white-black:.0f})'
    bits = (centers < (black+white)/2).astype(np.uint8)
    raw = np.packbits(bits).tobytes()
    try:
        magic, version, left_ms, seq, delta_us, crc = PACKET_STRUCT.unpack(raw)
    except struct.error as exc:
        return None, str(exc)
    if magic != MAGIC:
        return None, f'magic mismatch: 0x{magic:04X}'
    if version != VERSION:
        return None, f'protocol version {version}, expected {VERSION}'
    expected = crc16_ccitt(raw[:-2])
    if crc != expected:
        return None, f'CRC mismatch: {crc:04X} != {expected:04X}'
    return Stamp(left_ms, seq, delta_us), None


def iso_utc(milliseconds):
    return dt.datetime.fromtimestamp(milliseconds / 1000, tz=dt.timezone.utc).strftime('%H:%M:%S.%f')[:-3] + ' UTC'


def self_test():
    """Check exact strip compatibility with the server without using the network."""
    w, h = 1280, 580
    frame = np.full((h, w, 3), 35, np.uint8)
    left_ms = 1794834000123
    seq = 7351
    delta_us = 22450
    payload = struct.pack('>HBQIi', MAGIC, VERSION, left_ms, seq, delta_us)
    payload += struct.pack('>H', crc16_ccitt(payload))
    bits = np.unpackbits(np.frombuffer(payload, dtype=np.uint8))
    y = h - Y_BOTTOM_OFFSET
    for i, bit in enumerate(bits):
        frame[y:y+CELL_H, X0+i*CELL_W:X0+(i+1)*CELL_W] = (0, 0, 0) if bit else (255, 255, 255)
    stamp, error = decode_strip(frame)
    assert error is None and stamp == Stamp(left_ms, seq, delta_us), (stamp, error)
    # Robustness test through lossy JPEG; video uses H.264 and must also be field-tested.
    _, jpg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
    stamp2, error2 = decode_strip(cv2.imdecode(jpg, cv2.IMREAD_COLOR))
    assert error2 is None and stamp2 == stamp, (stamp2, error2)
    print('PASS: 168-bit timestamp, sequence, camera offset and CRC-16 decoded.')
    print('PASS: lossy-compression test (JPEG quality 75).')



class MulticastSocketRelay:
    """Join multicast using the same Python socket API as multicast_video_probe.

    Forward the original MPEG-TS UDP datagrams to a private loopback UDP port.
    GStreamer then sees a normal unicast udpsrc and no longer needs to select
    a Windows multicast adapter. No decode/re-encode or packet transformation.
    """
    def __init__(self, group, port, iface_ip, local_port=15000):
        if not iface_ip:
            raise ValueError('Multicast receiver requires --iface-ip (this PC Ethernet IPv4)')
        self.group = group
        self.port = port
        self.iface_ip = iface_ip
        self.local_port = local_port
        self.done = threading.Event()
        self.thread = None
        self.sock = None
        self.error = None
        self.packets = 0
        self.bytes = 0
        self.last_received = 0.0

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            sock.bind(('', self.port))
            membership = socket.inet_aton(self.group) + socket.inet_aton(self.iface_ip)
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
            sock.settimeout(0.5)
        except Exception:
            sock.close()
            raise
        self.sock = sock
        self.thread = threading.Thread(target=self._run, name='multicast-socket-relay', daemon=True)
        self.thread.start()
        print(f'Multicast socket joined {self.group}:{self.port} via {self.iface_ip}; '
              f'forwarding MPEG-TS unchanged to 127.0.0.1:{self.local_port}', flush=True)

    def _run(self):
        output = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        try:
            while not self.done.is_set():
                try:
                    packet, addr = self.sock.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    if self.done.is_set():
                        break
                    raise
                if not packet:
                    continue
                output.sendto(packet, ('127.0.0.1', self.local_port))
                self.packets += 1
                self.bytes += len(packet)
                self.last_received = time.monotonic()
        except Exception as exc:
            self.error = str(exc)
        finally:
            output.close()

    def stop(self):
        self.done.set()
        if self.sock:
            self.sock.close()
        if self.thread:
            self.thread.join(timeout=1.5)

class StreamReceiver:
    def __init__(self, port, transport='multicast', group='239.10.10.10', multicast_iface=None,
                 iface_ip=None, local_port=15000):
        try:
            import gi
            gi.require_version('Gst', '1.0')
            gi.require_version('GstVideo', '1.0')
            from gi.repository import Gst, GstVideo
        except ImportError as exc:
            raise RuntimeError('GStreamer Python bindings missing. Activate conda gst-stereo.') from exc
        self.Gst, self.GstVideo = Gst, GstVideo
        Gst.init(None)
        self.lock = threading.Lock()
        self.latest_frame = None
        self.latest_receive_ns = 0
        self.received = 0
        self.error_text = None
        self.warning_count = 0
        self.bad_frames = 0
        self.last_caps = "(none)"
        self.relay = (MulticastSocketRelay(group, port, iface_ip, local_port)
                      if transport == 'multicast' else None)
        # Preserve the entire compressed H.264 stream until decoding.
        # Dropping TS/H.264 packets BEFORE decode can break reference-frame recovery.
        if transport == 'multicast':
            # Consume the reliable Python-socket multicast join from a local
            # unicast relay, instead of letting GStreamer choose the NIC.
            source = f'udpsrc address=127.0.0.1 port={local_port} buffer-size=4194304 '
        else:
            source = f'udpsrc address=0.0.0.0 port={port} buffer-size=4194304 '
        pipeline_str = (
            source +
            'caps="video/mpegts,systemstream=(boolean)true,packetsize=(int)188" '
            '! tsparse set-timestamps=true '
            '! tsdemux name=demux '
            'demux. ! h264parse ! avdec_h264 ! videoconvert ! video/x-raw,format=BGR '
            '! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream '
            '! appsink name=framesink emit-signals=true sync=false max-buffers=1 drop=true'
        )
        self.pipeline = Gst.parse_launch(pipeline_str)
        self.sink = self.pipeline.get_by_name('framesink')
        self.sink.connect('new-sample', self._sample)

    def _sample(self, sink):
        Gst = self.Gst
        sample = sink.emit('pull-sample')
        if sample is None:
            return Gst.FlowReturn.OK
        buf, caps = sample.get_buffer(), sample.get_caps()
        if buf is None or caps is None or caps.get_size() < 1:
            self.bad_frames += 1
            return Gst.FlowReturn.OK
        # Read negotiated dimensions directly from the caps structure.
        # VideoInfo.from_caps may return success while leaving width and height
        # at zero in some Windows/PyGObject binding combinations.
        structure = caps.get_structure(0)
        ok_w, width = structure.get_int('width')
        ok_h, height = structure.get_int('height')
        self.last_caps = caps.to_string()
        if not ok_w or not ok_h or width <= 0 or height <= 0:
            self.bad_frames += 1
            if self.bad_frames <= 3:
                print(f'Ignoring frame with invalid caps: {self.last_caps}', file=sys.stderr)
            return Gst.FlowReturn.OK
        ok, mapped = buf.map(Gst.MapFlags.READ)
        if not ok:
            self.bad_frames += 1
            return Gst.FlowReturn.OK
        try:
            raw = np.frombuffer(mapped.data, dtype=np.uint8)
            min_stride = width * 3
            meta = self.GstVideo.buffer_get_video_meta(buf)
            # GstVideoInfo may provide a padded row stride. If it does not,
            # fall back to buffer-size-based stride or tightly packed BGR.
            stride = int(meta.stride[0]) if meta is not None else 0
            if stride < min_stride:
                info = self.GstVideo.VideoInfo()
                if info.from_caps(caps) and int(info.stride[0]) >= min_stride:
                    stride = int(info.stride[0])
            if stride < min_stride:
                stride = (raw.size // height) if raw.size % height == 0 else min_stride
            if stride < min_stride or raw.size < height * stride:
                raise ValueError(f'Invalid BGR payload: {raw.size} bytes for {width}x{height}, stride={stride}, caps={self.last_caps}')
            frame = np.ascontiguousarray(raw[:height * stride].reshape(height, stride)[:, :min_stride]
                                          .reshape(height, width, 3)).copy()
            if frame.shape != (height, width, 3):
                raise ValueError(f'Unexpected decoded frame shape {frame.shape}')
            recv_ns = time.time_ns()
        except Exception as exc:
            self.bad_frames += 1
            if self.bad_frames <= 3:
                print(f'Frame conversion issue: {exc}', file=sys.stderr)
            return Gst.FlowReturn.OK
        finally:
            buf.unmap(mapped)
        with self.lock:
            self.latest_frame = frame
            self.latest_receive_ns = recv_ns
            self.received += 1
        return Gst.FlowReturn.OK

    def latest(self):
        with self.lock:
            return self.latest_frame, self.latest_receive_ns, self.received

    def check_error(self):
        if self.relay and self.relay.error:
            raise RuntimeError('Multicast socket relay: ' + self.relay.error)
        Gst = self.Gst
        bus = self.pipeline.get_bus()
        while True:
            msg = bus.pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.WARNING | Gst.MessageType.EOS)
            if not msg:
                break
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                raise RuntimeError(f'GStreamer: {err} ({debug})')
            if msg.type == Gst.MessageType.WARNING:
                err, debug = msg.parse_warning()
                self.warning_count += 1
                if self.warning_count <= 3 or self.warning_count % 50 == 0:
                    print(f'GStreamer warning #{self.warning_count}: {err} ({debug})', file=sys.stderr)
            if msg.type == Gst.MessageType.EOS:
                raise RuntimeError('Video stream ended')
        if self.error_text:
            raise RuntimeError(self.error_text)

    def start(self):
        if self.pipeline.set_state(self.Gst.State.PLAYING) == self.Gst.StateChangeReturn.FAILURE:
            raise RuntimeError('Could not start UDP receiver pipeline')
        if self.relay:
            self.relay.start()

    def stop(self):
        if self.relay:
            self.relay.stop()
        self.pipeline.set_state(self.Gst.State.NULL)


def main():
    p = argparse.ArgumentParser(description='Stereo Vision UDP Multi-client Client v7 / Tkinter display')
    p.add_argument('--port', type=int, default=5000)
    p.add_argument('--transport', choices=['multicast','unicast'], default='multicast')
    p.add_argument('--multicast-group', default='239.10.10.10')
    p.add_argument('--multicast-iface', default=None,
                   help='Legacy GStreamer interface option (Python multicast uses --iface-ip)')
    p.add_argument('--iface-ip', default=None,
                   help='Local Ethernet IPv4 used to join multicast (e.g. 192.168.0.11)')
    p.add_argument('--relay-port', type=int, default=15000,
                   help='Localhost UDP relay port, default 15000')
    p.add_argument('--clock-offset-ms', type=float, default=0.0,
                   help='Additional manual correction, client minus server, in milliseconds')
    p.add_argument('--time-server', default='192.168.0.10',
                   help='Camera server IPv4 for LAN SNTP (default 192.168.0.10)')
    p.add_argument('--ntp-port', type=int, default=9123,
                   help='LAN SNTP UDP port on camera server (default 9123)')
    p.add_argument('--ntp-samples', type=int, default=7,
                   help='Time-offset probes at startup')
    p.add_argument('--no-time-sync', action='store_true',
                   help='Skip automatic LAN time-offset measurement')
    p.add_argument('--no-preview', action='store_true')
    p.add_argument('--self-test', action='store_true')
    p.add_argument('--report-seconds', type=float, default=5.0)
    p.add_argument('--display-mode', choices=['both','left','right'], default='both',
                   help='Initial viewer layout (only affects local display)')
    args = p.parse_args()
    if args.self_test:
        self_test()
        # Exercise dashboard and preview composition with a synthetic frame.
        sample = np.zeros((580, 1280, 3), dtype=np.uint8)
        preview = make_preview(sample, None, args.port, 0, 0, 0, 0, None, None, [], [], '', args.clock_offset_ms)
        assert preview.size[0] > 0 and preview.size[1] > 0
        print('PASS: Pillow dashboard and preview composition.')
        return

    if args.transport == 'multicast':
        import ipaddress
        try:
            if not ipaddress.IPv4Address(args.multicast_group).is_multicast:
                p.error('Invalid IPv4 multicast group')
        except ValueError:
            p.error('Invalid IPv4 multicast group')
    if args.transport == 'multicast' and not args.iface_ip:
        p.error('Multicast requires --iface-ip LOCAL_ETHERNET_IP (e.g. --iface-ip 192.168.0.11)')
    ntp_offset_ms = 0.0
    sync_status = 'disabled'
    if not args.no_time_sync:
        print(f'Checking LAN clock against {args.time_server}:{args.ntp_port}...', flush=True)
        try:
            ntp_offset_ms, rtt_ms, good = measure_ntp_offset(
                args.time_server, args.ntp_port, args.ntp_samples)
            sync_status = f'OK ({good} samples, best RTT {rtt_ms:.2f}ms)'
            print(f'LAN clock offset: client - server = {ntp_offset_ms:+.2f} ms; '
                  f'{sync_status}', flush=True)
        except (OSError, ValueError, TimeoutError) as exc:
            sync_status = f'FAILED: {exc}'
            print(f'WARNING: LAN time alignment failed ({exc}). '
                  'Frame ages may include clock bias.', flush=True)
    effective_clock_offset_ms = ntp_offset_ms + args.clock_offset_ms
    client = StreamReceiver(args.port, args.transport, args.multicast_group,
                            args.multicast_iface, args.iface_ip, args.relay_port)
    count_processed = valid = invalid = skipped = 0
    last_valid_seq = None
    last_issue = ''
    recent_ages = collections.deque(maxlen=300)
    recent_offsets = collections.deque(maxlen=300)
    last_report = time.monotonic()
    started = time.monotonic()
    last_preview = None
    closing = False
    display_mode = args.display_mode
    fullscreen = False
    root = None
    panel = None
    if not args.no_preview:
        root = tk.Tk()
        root.title('Stereo Vision UDP Multi-client Client v7 | Q quit | S screenshot')
        try:
            root.state('zoomed')  # Windows maximized, not exclusive fullscreen
        except tk.TclError:
            root.attributes('-zoomed', True)
        panel = tk.Label(root, bg='black')
        panel.pack(fill='both', expand=True)
        def quit_app(event=None):
            nonlocal closing
            closing = True
        def screenshot(event=None):
            if last_preview is not None:
                name = f'stereo_client_{int(time.time())}.png'
                last_preview.save(name)
                print('Saved', name, flush=True)
        def change_view(mode):
            nonlocal display_mode
            display_mode = mode

        def toggle_fullscreen(event=None):
            nonlocal fullscreen
            fullscreen = not fullscreen
            root.attributes('-fullscreen', fullscreen)

        root.bind('<KeyPress-1>', lambda event: change_view('both'))
        root.bind('<KeyPress-2>', lambda event: change_view('left'))
        root.bind('<KeyPress-3>', lambda event: change_view('right'))
        root.bind('<KeyPress-f>', toggle_fullscreen)
        root.bind('<KeyPress-F>', toggle_fullscreen)
        root.bind('<KeyPress-q>', quit_app)
        root.bind('<KeyPress-Q>', quit_app)
        root.bind('<Escape>', quit_app)
        root.bind('<KeyPress-s>', screenshot)
        root.bind('<KeyPress-S>', screenshot)
        root.protocol('WM_DELETE_WINDOW', quit_app)
        # A loading screen makes the receiving state visible before packets arrive.
        loading = Image.new('RGB', (960, 260), (15, 22, 30))
        ImageDraw.Draw(loading).text((24, 25), f'Waiting for UDP video on port {args.port}...', fill='white')
        photo = ImageTk.PhotoImage(loading, master=root)
        panel.configure(image=photo)
        panel.image = photo
    client.start()
    print(f'Listening on {args.transport.upper()} '
          f'{args.multicast_group if args.transport == "multicast" else "0.0.0.0"}:{args.port}; '
          'press Q/Esc to exit, S for screenshot.', flush=True)
    try:
        while not closing:
            if root is not None:
                root.update()
            client.check_error()
            frame, receive_ns, count = client.latest()
            if frame is not None and frame.ndim == 3 and frame.shape[0] > 0 and frame.shape[1] > 0 and count != count_processed:
                count_processed = count
                stamp, error = decode_strip(frame)
                age_ms = age_right_ms = None
                if stamp is not None:
                    valid += 1
                    age_ms = receive_ns / 1e6 - stamp.left_utc_ms - effective_clock_offset_ms
                    age_right_ms = receive_ns / 1e6 - stamp.right_utc_ms - effective_clock_offset_ms
                    recent_ages.append(age_ms)
                    recent_offsets.append(stamp.right_minus_left_us / 1000)
                    if last_valid_seq is not None:
                        delta = (stamp.sequence - last_valid_seq) % (1 << 32)
                        if 1 < delta < 10000:
                            skipped += delta - 1
                    last_valid_seq = stamp.sequence
                else:
                    invalid += 1
                    last_issue = error or 'unknown'
                if root is not None:
                    last_preview = make_preview(frame, stamp, args.port, count, valid, invalid,
                                                skipped, age_ms, age_right_ms,
                                                recent_ages, recent_offsets, last_issue,
                                                effective_clock_offset_ms,
                                                screen_size=(root.winfo_screenwidth(), root.winfo_screenheight()),
                                                display_mode=display_mode)
                    photo = ImageTk.PhotoImage(last_preview, master=root)
                    panel.configure(image=photo)
                    panel.image = photo
            if time.monotonic() - last_report >= args.report_seconds:
                relay_note = (f' multicast_udp={client.relay.packets} '
                              if client.relay else '')
                text = (f'[{time.monotonic()-started:.0f}s] decoded={count} valid={valid} '
                        f'invalid={invalid} missing_seq={skipped} warnings={client.warning_count} bad_frames={client.bad_frames} ')
                if recent_ages:
                    text += (f'latest={recent_ages[-1]:.1f}ms avg={statistics.mean(recent_ages):.1f}ms '
                             f'L/R offset={recent_offsets[-1]:+.1f}ms')
                else:
                    text += f'waiting for valid timestamps; last error={last_issue}'
                print(text + relay_note + f' clock={sync_status} '
                      f'offset={effective_clock_offset_ms:+.1f}ms', flush=True)
                last_report = time.monotonic()
            time.sleep(.01)
    except KeyboardInterrupt:
        print('Interrupted', flush=True)
    finally:
        client.stop()
        if root is not None:
            root.destroy()


def make_preview(frame, stamp, port, count, valid, invalid, skipped,
                 age_ms, age_right_ms, recent_ages, recent_offsets, last_issue,
                 clock_offset_ms, screen_size=None, display_mode='both'):
    """Responsive preview. Scale VIDEO only; render HUD text at display resolution.

    Does not affect the incoming video, timestamp decoder or screenshot source.
    """
    h, w = frame.shape[:2]
    if h < 1 or w < 1:
        raise ValueError(f'Invalid decoded video frame dimensions: {w}x{h}')
    if screen_size is None:
        screen_size = (1920, 1080)
    sw, sh = screen_size
    target_w = min(w, max(500, sw - 65))
    target_h = max(260, sh - 120)
    # The binary timestamp is in the last 100 pixels of the ORIGINAL frame.
    # The decoder has already read it; do not unnecessarily display tiny pixels.
    video_h = max(1, h - 100)
    video = frame[:video_h]
    half = w // 2
    if display_mode == 'left':
        video = video[:, :half]
    elif display_mode == 'right':
        video = video[:, half:]
    source_rgb = Image.fromarray(np.ascontiguousarray(video[:, :, ::-1]), mode='RGB')
    hud_height = min(285, max(215, round(target_h * .26)))
    video_limit = max(160, target_h - hud_height)
    scale = min(target_w / source_rgb.width, video_limit / source_rgb.height, 1.0)
    new_w = max(1, round(source_rgb.width * scale))
    new_h = max(1, round(source_rgb.height * scale))
    if (new_w, new_h) != source_rgb.size:
        source_rgb = source_rgb.resize((new_w, new_h), Image.Resampling.LANCZOS)
    output_width = max(new_w, min(target_w, 900))
    hud = Image.new('RGB', (output_width, hud_height), (16, 22, 31))
    draw = ImageDraw.Draw(hud)
    font_px = max(17, min(23, round(output_width / 82)))
    try:
        font = ImageFont.truetype('arial.ttf', font_px)
        headline = ImageFont.truetype('arialbd.ttf', font_px + 2)
    except OSError:
        font = ImageFont.load_default()
        headline = font
    def label(msg, x, y, color=(232, 236, 240), bold=False):
        draw.text((x, y), str(msg), fill=color, font=headline if bold else font)
    line = font_px + 11
    label(f'UDP :{port}  |  Frames {count}  |  CRC OK {valid}  |  Errors {invalid}',
          15, 11, bold=True)
    if stamp is not None:
        label(f'Frame {stamp.sequence}  |  UTC {iso_utc(stamp.left_utc_ms)}', 15, 11 + line)
        label(f'Age L {age_ms:.1f} ms   R {age_right_ms:.1f} ms'
              f'   |   Skew {stamp.right_minus_left_us / 1000:+.1f} ms',
              15, 11 + line * 2, (167, 241, 135))
        if recent_ages:
            label(f'Age min {min(recent_ages):.0f}  avg {statistics.mean(recent_ages):.0f}'
                  f'  max {max(recent_ages):.0f} ms   |   Missing {skipped}',
                  15, 11 + line * 3)
    else:
        label('Timestamp unavailable: ' + str(last_issue)[:65],
              15, 11 + line, (252, 120, 108))
    label(f'VIEW {display_mode.upper()}   |   1: Both  2: Left  3: Right'
          f'   F: Fullscreen   S: Screenshot   Q: Exit',
          15, hud_height - line, (188, 201, 220))
    # Render trend graph on a wide screen without shrinking text.
    if len(recent_ages) >= 2 and output_width >= 1100:
        gw, gh = min(460, output_width // 3), max(70, hud_height - 62)
        x0 = output_width - gw - 20
        y0 = 22
        draw.rectangle((x0, y0, x0 + gw, y0 + gh), outline=(87, 103, 122), width=2)
        values = list(recent_ages)
        low, high = min(values), max(values)
        pad = max(10.0, .1 * (high - low))
        low -= pad
        high += pad
        points = [(x0 + round(i * gw / max(1, len(values) - 1)),
                   y0 + gh - round((v - low) / max(1e-9, high - low) * gh))
                  for i, v in enumerate(values)]
        draw.line(points, fill=(255, 219, 110), width=2)
    joined = Image.new('RGB', (output_width, new_h + hud_height), (0, 0, 0))
    joined.paste(source_rgb, ((output_width - new_w) // 2, 0))
    joined.paste(hud, (0, new_h))
    return joined


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        traceback.print_exc()
        print(f'ERROR: {exc}', file=sys.stderr)
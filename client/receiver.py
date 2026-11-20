"""GStreamer H.264/MPEG-TS receiver used by every stereo client.

Multicast is joined with a Python socket tied to the local Ethernet IP and
forwarded unchanged to a loopback ``udpsrc``; unicast binds ``udpsrc`` directly.
"""
import socket
import sys
import threading
import time

import numpy as np


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

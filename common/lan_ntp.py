"""Lightweight LAN SNTP-compatible clock service and offset measurement.

The camera computer's SYSTEM clock is used as the reference. This is relative
clock alignment for latency estimation, not evidence of UTC traceability.
Default port 9123 avoids Windows Time service conflicts on UDP 123.

NTP timestamps are seconds since 1900-01-01; requests and responses use the
standard 48-byte NTP/SNTP wire format.
"""
from __future__ import annotations

import socket
import struct
import threading
import time

NTP_EPOCH = 2208988800
MASK32 = (1 << 32) - 1


def encode_ntp(unix_seconds: float) -> bytes:
    seconds = unix_seconds + NTP_EPOCH
    whole = int(seconds)
    fraction = int((seconds - whole) * (1 << 32))
    return struct.pack("!II", whole & MASK32, fraction & MASK32)


def decode_ntp(packed: bytes) -> float:
    sec, frac = struct.unpack("!II", packed)
    return sec - NTP_EPOCH + frac / float(1 << 32)


class LanNtpServer:
    """Answer SNTP mode-3 requests with the source computer's system time."""

    def __init__(self, host="0.0.0.0", port=9123):
        self.host, self.port = host, int(port)
        self._stop = threading.Event()
        self.sock = None
        self.thread = None
        self.requests = 0
        self.last_error = None

    def start(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind((self.host, self.port))
            sock.settimeout(0.5)
        except Exception:
            sock.close()
            raise
        self.sock = sock
        self.thread = threading.Thread(target=self._serve, name="lan-sntp", daemon=True)
        self.thread.start()
        print(f"LAN SNTP clock service listening on UDP {self.host}:{self.port}; "
              "reference is server Windows clock", flush=True)

    def _serve(self):
        while not self._stop.is_set():
            try:
                request, address = self.sock.recvfrom(512)
                received = time.time()
                if len(request) < 48 or request[0] & 7 != 3:
                    continue
                version = (request[0] >> 3) & 7
                if version not in (3, 4):
                    continue
                reply = bytearray(48)
                reply[0] = (version << 3) | 4
                reply[1] = 2  # local reference only, not independently disciplined
                reply[2] = request[2]  # poll
                reply[3] = 0xEC  # signed precision = -20
                reply[12:16] = b'LOCL'
                reply[16:24] = encode_ntp(received)
                reply[24:32] = request[40:48]  # exact originate timestamp
                reply[32:40] = encode_ntp(received)
                reply[40:48] = encode_ntp(time.time())
                self.sock.sendto(reply, address)
                self.requests += 1
            except socket.timeout:
                pass
            except OSError as exc:
                if not self._stop.is_set():
                    self.last_error = str(exc)
                break

    def stop(self):
        self._stop.set()
        if self.sock:
            self.sock.close()
        if self.thread:
            self.thread.join(timeout=1.2)


def measure_ntp_offset(host: str, port=9123, samples=7, timeout=0.6):
    """Return (client-minus-server offset ms, measured RTT ms, successful_count).

    Use the lowest-RTT sample to limit asymmetrical network/OS scheduling
    interference; this is an estimate, not sub-millisecond clock discipline.
    """
    if not host:
        raise ValueError("Time-server host is required")
    # socket.gaierror is an OSError, which callers already handle.
    results = []
    server_ip = socket.gethostbyname(host)  # resolve once, not per sample
    for _ in range(max(1, min(int(samples), 20))):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(timeout)
            request = bytearray(48)
            request[0] = 0x23  # LI=0 VN=4 mode=3
            request[2] = 4
            t1 = time.time()
            tag = encode_ntp(t1)
            request[40:48] = tag
            sock.sendto(request, (host, port))
            response, sender = sock.recvfrom(512)
            t4 = time.time()
            if (len(response) < 48 or (response[0] & 7) != 4
                    or response[1] == 0 or response[1] >= 16
                    or response[24:32] != tag or sender[0] != server_ip):
                continue
            t2, t3 = decode_ntp(response[32:40]), decode_ntp(response[40:48])
            # NTP standard: server-minus-client = ((t2-t1)+(t3-t4))/2.
            server_minus_client = ((t2 - t1) + (t3 - t4)) / 2
            rtt_ms = ((t4 - t1) - (t3 - t2)) * 1000
            if -0.1 <= rtt_ms <= 3000:
                results.append((-server_minus_client * 1000, max(0, rtt_ms)))
        except (OSError, ValueError):
            pass
        finally:
            sock.close()
        time.sleep(.045)
    if not results:
        raise TimeoutError(f"No valid SNTP response from {host}:{port}")
    best = min(results, key=lambda x: x[1])
    return best[0], best[1], len(results)

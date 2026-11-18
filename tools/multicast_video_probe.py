#!/usr/bin/env python3
"""Measure raw multicast MPEG-TS traffic independently of GStreamer.

On a receiving PC (stop the stereo client first):
  python tools/multicast_video_probe.py --iface-ip 192.168.0.100

Use this PC's own Ethernet IPv4, not the camera server's or the router's.
Video is expected on multicast 239.10.10.10 UDP port 5000.
This script does not display, decode or record video. Ctrl+C exits.
"""
import argparse
import socket
import struct
import time

def main():
    p = argparse.ArgumentParser(description="Diagnose multicast MPEG-TS packets")
    p.add_argument("--iface-ip", required=True, help="THIS computer's Ethernet IPv4")
    p.add_argument("--group", default="239.10.10.10")
    p.add_argument("--port", type=int, default=5000)
    p.add_argument("--seconds", type=float, default=15)
    args = p.parse_args()
    if args.seconds <= 0:
        p.error("--seconds must be positive")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
        sock.bind(("", args.port))
        membership = socket.inet_aton(args.group) + socket.inet_aton(args.iface_ip)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(1)
        print(f"Joined {args.group}:{args.port} on local Ethernet {args.iface_ip}", flush=True)
        print("Close the regular video client first. Sampling packet count, bitrate and TS sync...", flush=True)
        start = last = time.monotonic()
        total_bytes = packets = ts_ok = ts_bad = 0
        senders = {}
        while time.monotonic() - start < args.seconds:
            try:
                data, addr = sock.recvfrom(65535)
            except socket.timeout:
                data = None
            if data is not None:
                packets += 1
                total_bytes += len(data)
                senders[addr[0]] = senders.get(addr[0], 0) + 1
                # MPEG-TS from mpegtsmux alignment=7 has seven 188-byte
                # packets per UDP datagram; allow variable numbers as well.
                good = (len(data) >= 188 and len(data) % 188 == 0 and
                        all(data[i] == 0x47 for i in range(0, len(data), 188)))
                if good:
                    ts_ok += 1
                else:
                    ts_bad += 1
            now = time.monotonic()
            if now - last >= 5:
                elapsed = max(0.001, now - start)
                print(f"[{elapsed:.0f}s] packets={packets} bytes={total_bytes} "
                      f"avg_mbps={8 * total_bytes / elapsed / 1e6:.2f} "
                      f"valid_MPEG_TS_datagrams={ts_ok} other_datagrams={ts_bad} "
                      f"senders={senders}", flush=True)
                last = now
        if packets == 0:
            print("RESULT: no video packets arrived on this Ethernet interface/port.", flush=True)
        elif ts_ok == 0:
            print("RESULT: UDP data arrived, but no recognizable 188-byte MPEG-TS datagrams.", flush=True)
        else:
            print(f"RESULT: multicast MPEG-TS is arriving ({ts_ok}/{packets} recognized packets). "
                  "Investigate client pipeline/caps/decoder if preview stays blank.", flush=True)
    finally:
        sock.close()

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped")

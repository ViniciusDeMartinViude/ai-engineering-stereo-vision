#!/usr/bin/env python3
"""Simple multicast diagnostic independent of GStreamer/video.

Run on receiver first:
  python tools/multicast_probe.py receive --iface-ip 192.168.0.100
Run on sender:
  python tools/multicast_probe.py send --iface-ip 192.168.0.101
Replace example IPs with the actual LOCAL IPv4 on each machine.
"""
import argparse
import socket
import struct
import time

def main():
    p = argparse.ArgumentParser()
    p.add_argument('role', choices=['send', 'receive'])
    p.add_argument('--iface-ip', required=True, help='IPv4 of this machine\'s Ethernet interface')
    p.add_argument('--group', default='239.10.10.10')
    p.add_argument('--port', type=int, default=5001,
                   help='Probe port 5001 (separate from video port 5000)')
    a = p.parse_args()
    socket.inet_aton(a.iface_ip)
    socket.inet_aton(a.group)
    if a.role == 'receive':
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', a.port))
        membership = socket.inet_aton(a.group) + socket.inet_aton(a.iface_ip)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        sock.settimeout(5)
        print(f'Joined {a.group}:{a.port} via Ethernet {a.iface_ip}; awaiting probe packets...', flush=True)
        while True:
            try:
                data, addr = sock.recvfrom(2048)
                print(f'RECEIVED {len(data)} bytes from {addr[0]}:{addr[1]}: {data[:120]!r}', flush=True)
            except socket.timeout:
                print('NO PACKETS in 5s (check sender IP/interface, switch and firewall)', flush=True)
    else:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(a.iface_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        print(f'Sending probe from {a.iface_ip} to {a.group}:{a.port}', flush=True)
        count = 0
        while True:
            count += 1
            payload = f'stereo-multicast-probe seq={count} time={time.time():.3f}'.encode()
            sock.sendto(payload, (a.group, a.port))
            print('SENT', payload.decode(), flush=True)
            time.sleep(1)

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('Stopped')

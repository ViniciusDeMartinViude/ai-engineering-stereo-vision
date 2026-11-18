# AI Engineering Stereo Vision

Stereo vision capture and low-latency distribution for the Emirates Skills AI Engineering competition.

## Current baseline: UDP multicast (default)

Two Logitech MX Brio cameras are combined into a side-by-side video stream on a Windows capture PC. A single NVIDIA NVENC H.264 encoder produces MPEG-TS packets distributed as **UDP multicast** to student PCs using GStreamer's `multiudpsink`. Each client independently decodes video and the original 168-bit embedded timestamp/CRC-16 strip.

The earlier single-client UDP system showed very high timestamp decode reliability in testing. **Multi-client operation still needs validation on real hardware.** WebRTC/MediaMTX experiments were less reliable for the visual timestamp and are not the current baseline.

## Quick start (Windows + Conda)

1. Install GStreamer with DirectShow, NVENC and UDP plugins and Python GStreamer bindings (see `docs/WINDOWS_SETUP.md`).
2. Keep all computers on the same wired LAN/VLAN; ensure IGMP snooping works on the Ethernet switch (and an IGMP querier is present where required).
3. Start the server:

   ```powershell
   conda activate gst-stereo
   python server/stereo_vision_server_multiclient.py
   ```

4. On each student PC:

   ```powershell
   conda activate gst-stereo
   python client/stereo_vision_client_multiclient.py --self-test
   python client/stereo_vision_client_multiclient.py --port 5000 --iface-ip RECEIVER_ETHERNET_IP
   ```

The default shared multicast group is `239.10.10.10:5000`. Students join it automatically. The student PCs must allow inbound UDP port 5000 on the trusted LAN. Only one process can bind the receiving port on each machine. Stop VLC or an older receiver before starting the Python client.

## Scope

- Capture and present two stereo views, using camera application timestamps (not hardware exposure synchronization).
- Binary timestamp and CRC decoding; compute *apparent* frame age when machine clocks are aligned.
- UDP multicast distribution without MediaMTX; explicit UDP unicast fallback remains available.
- Expand later into stereo calibration, depth estimation and conveyor tracking.

## Notes

- Keep camera calibration and robot safety controls separate until validated.
- Do not commit private network details, passwords, recordings, or trained model weights.
- The server and client have not been end-to-end verified on all competition computers yet.

See `docs/WINDOWS_SETUP.md` for setup and troubleshooting.

## Unicast fallback

If the classroom Ethernet switch or VLAN does not forward multicast, use the previous unicast mode:

```powershell
Copy-Item config/clients.example.txt clients.txt
notepad clients.txt
python server/stereo_vision_server_multiclient.py --transport unicast --clients-file clients.txt
python client/stereo_vision_client_multiclient.py --transport unicast --port 5000
```

Multicast does **not** use `clients.txt`. Avoid enabling IPTV/VLAN settings merely to turn on multicasting; IGMP snooping on the actual Ethernet switch is the relevant feature. An IGMP querier may be needed in a snooping-enabled VLAN.

### Windows multicast interface selection

The receiving client now defaults to a **Python socket multicast join**, explicitly tied to the local Ethernet IPv4 address. This matches the working raw multicast probe, then forwards each unchanged MPEG-TS datagram over loopback to the original GStreamer H.264 decoder. Specify `--iface-ip` with each receiver's **own** Ethernet IP (e.g. `192.168.0.11`). Use `--transport unicast` to skip the relay entirely.

## LAN clock alignment and maximized windows

The **camera server** starts a small NTP/SNTP-compatible LAN service on **UDP port 9123** by default. This uses its **Windows system clock** as the reference; it does not discipline that clock from an external NTP source. Standard Windows Time may already use UDP 123, so the independent service uses 9123 to avoid a port conflict.

On the source machine, allow inbound UDP 9123 only on the trusted Private LAN (PowerShell as Administrator):

```powershell
New-NetFirewallRule -DisplayName "Stereo Vision LAN SNTP" -Direction Inbound -Protocol UDP -LocalPort 9123 -Action Allow -Profile Private
```

Start the server normally (the clock service is enabled by default):

```powershell
python server/stereo_vision_server_multiclient.py --width 1920 --height 1080 --fps 10 --format mjpeg --bitrate 12000
```

On **each client**, supply the server's Ethernet IP. For this deployment the camera server was `192.168.0.10` and the sample student receiver was `192.168.0.11`:

```powershell
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11 --time-server 192.168.0.10
```

At startup the client requests 7 SNTP time samples, selects the lowest network RTT, reports the client-minus-server offset, and **subtracts it from apparent frame-age calculations**. It does **not change the Windows system clock** or require administrator privileges on student clients. Use `--ntp-port` on both sides to change the port, or `--no-time-sync` on the client to disable. If the time server cannot be reached, the client warns and continues with uncorrected frame age. `--clock-offset-ms` adds an optional manual correction.

Both preview windows now open **maximized** on Windows (showing the taskbar). Press `F` for borderless fullscreen when desired.

**Interpretation:** The displayed video frame age is an estimate relative to the camera server's Windows clock. It can still include network asymmetry and timestamps taken after physical exposure. NTP correction cannot eliminate actual MPEG-TS/H.264 buffering, so an apparent three-second delay that persists after synchronization is a real pipeline-latency problem to investigate separately.

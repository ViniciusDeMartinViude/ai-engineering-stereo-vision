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
   python client/stereo_vision_client_multiclient.py --port 5000
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

# AI Engineering Stereo Vision

Stereo vision capture and low-latency distribution for the Emirates Skills AI Engineering competition.

## Current baseline: UDP multi-client

Two Logitech MX Brio cameras are combined into a side-by-side video stream on a Windows capture PC. A single NVIDIA NVENC H.264 encoder produces MPEG-TS packets distributed as **UDP unicast** to multiple student PCs using GStreamer's `multiudpsink`. Each client independently decodes video and the original 168-bit embedded timestamp/CRC-16 strip.

The earlier single-client UDP system showed very high timestamp decode reliability in testing. **Multi-client operation still needs validation on real hardware.** WebRTC/MediaMTX experiments were less reliable for the visual timestamp and are not the current baseline.

## Quick start (Windows + Conda)

1. Install GStreamer with DirectShow, NVENC and UDP plugins and Python GStreamer bindings (see `docs/WINDOWS_SETUP.md`).
2. On the source PC edit `config/clients.example.txt`, copy it to `clients.txt`, and replace the example addresses with student computer addresses.
3. Start the server:

   ```powershell
   conda activate gst-stereo
   python server/stereo_vision_server_multiclient.py --clients-file clients.txt
   ```

4. On each student PC:

   ```powershell
   conda activate gst-stereo
   python client/stereo_vision_client_multiclient.py --self-test
   python client/stereo_vision_client_multiclient.py --port 5000
   ```

The student PCs must allow inbound UDP port 5000 on the trusted LAN. Only one process can bind the receiving port on each machine. Stop VLC or an older receiver before starting the Python client.

## Scope

- Capture and present two stereo views, using camera application timestamps (not hardware exposure synchronization).
- Binary timestamp and CRC decoding; compute *apparent* frame age when machine clocks are aligned.
- Multi-client UDP fan-out without multicast or MediaMTX.
- Expand later into stereo calibration, depth estimation and conveyor tracking.

## Notes

- Keep camera calibration and robot safety controls separate until validated.
- Do not commit private network details, passwords, recordings, or trained model weights.
- The server and client have not been end-to-end verified on all competition computers yet.

See `docs/WINDOWS_SETUP.md` for setup and troubleshooting.

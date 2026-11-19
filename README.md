# AI Engineering Stereo Vision

Stereo vision capture and low-latency distribution for the Emirates Skills AI Engineering competition.

## Current baseline: UDP multicast (default)

Two Logitech MX Brio cameras are combined into a side-by-side video stream on a Windows capture PC. A single NVIDIA NVENC H.264 encoder produces MPEG-TS packets distributed as **UDP multicast** to student PCs using GStreamer's `multiudpsink`. Each client independently decodes video and the original 168-bit embedded timestamp/CRC-16 strip.

The earlier single-client UDP system showed very high timestamp decode reliability in testing. **Multi-client operation still needs validation on real hardware.** WebRTC/MediaMTX experiments were less reliable for the visual timestamp and are not the current baseline.

## Supported Python version: 3.12

**Use Python 3.12.x on Windows for the PySide6 + PyQtGraph client.** The tested Qt application now lives on `main`. For a clean environment, use `conda env create -f environment-qt312.yml` and `conda activate gst-stereo-qt312-qt312`. See [Qt client setup](docs/QT_CLIENT.md). The older Tkinter client is retained as a fallback.

## Quick start (Windows + Conda)

1. Install the Python **3.12** environment from `environment-qt312.yml` and the Windows GStreamer plugins (see `docs/WINDOWS_SETUP.md`).
2. Keep all computers on the same wired LAN/VLAN; ensure IGMP snooping works on the Ethernet switch (and an IGMP querier is present where required).
3. Start the server:

   ```powershell
   conda activate gst-stereo-qt312
   python server/stereo_vision_server_multiclient.py
   ```

4. On each student PC:

   ```powershell
   conda activate gst-stereo-qt312
   python client/stereo_vision_client_qt.py --self-test
   python client/stereo_vision_client_qt.py --port 5000 --iface-ip RECEIVER_ETHERNET_IP --time-server SERVER_ETHERNET_IP
   ```

The default shared multicast group is `239.10.10.10:5000`. Students join it automatically. The student PCs must allow inbound UDP port 5000 on the trusted LAN. Only one process can bind the receiving port on each machine. Stop VLC or an older receiver before starting the Python client.

The **recommended GUI** is `client/stereo_vision_client_qt.py` (PySide6 + PyQtGraph). The previous `client/stereo_vision_client_multiclient.py` (Tkinter) remains available for troubleshooting.

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

## YOLO object detection on the student client

The client can run **local Ultralytics YOLO object detection** on both stereo images, independently. This is optional: without \`--yolo-model\`, the existing multicast viewer works unchanged. It draws bounding boxes, class names and confidence percentages over the displayed images, along with an inference status/count. The model sees only the camera image regions (not the 100-pixel binary timestamp footer). Original H.264 packets and CRC16 timestamp are unchanged.

Install YOLO into the Python 3.12 \`gst-stereo-qt312\` Conda environment **on each student/receiver PC that will run inference**:

\`\`\`powershell
conda activate gst-stereo-qt312
python -m pip install ultralytics
\`\`\`

Start the receiver (example Ethernet IP from the working test):

\`\`\`powershell
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --yolo-model yolo26n.pt --yolo-conf 0.35 --yolo-imgsz 640 --yolo-device auto
\`\`\`

Or use a **locally trained model**:

\`\`\`powershell
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11 --yolo-model "C:\models\best.pt" --yolo-conf 0.5 --yolo-imgsz 640 --yolo-device 0
\`\`\`

The pretrained model file may be downloaded by Ultralytics if not present; a custom \`best.pt\` path must exist locally. For GPU inference, make sure PyTorch has a working CUDA installation; \`--yolo-device 0\` explicitly selects GPU 0 (an invalid device produces a reported error). \`--yolo-device cpu\` forces CPU.

**Architecture:** Each decoded stereo frame is split into LEFT and RIGHT camera images; a separate worker batches the two views for YOLO. Its pending input buffer has only one slot, so a slow model processes the newest available frame instead of accumulating seconds of extra latency. YOLO inference does not block the multicast decoder, NTP synchronization or the Tkinter video UI. Bounding boxes are mapped from each camera's original pixel coordinates into the selected display mode (both / left / right). Results older than one second since inference completed are hidden.

This adds object detection only, **not** stereo matching, depth calculation or 3D object coordinates. To estimate height/depth, the cameras still require calibrated intrinsics, extrinsics and temporal pairing.

## Interactive left/right YOLO regions of interest

The stereo client now offers four buttons in the maximized window toolbar:

- **Select LEFT ROI**: switches to left camera view, then drag a rectangle with the left mouse button; the green outline marks the left camera's active detection region
- **Reset LEFT**: restores YOLO inference on the full left camera image
- **Select RIGHT ROI**: switches to right camera view, then drag to choose its independent region; the pink outline marks the right selection
- **Reset RIGHT**: restores inference on the full right camera image

The two camera selections can be different. Clicking **1** shows both cameras and both ROI outlines together; **2** and **3** switch back to individual views. Each crop uses original camera pixel coordinates, independent of screen scaling or maximized mode. A selection narrower than 16 source pixels or shorter than 16 source pixels is ignored.

When YOLO is enabled with `--yolo-model`, each camera is cropped to its own ROI **before** inference. Its detection boxes are translated back into original camera coordinates before being displayed. Changing an ROI invalidates the previous YOLO detections. Without a YOLO model the colored ROI outlines can still be selected and displayed.

These controls only affect the local receiving client. The original multicast video, full-resolution camera pixels, NTP timing and embedded binary CRC16 strip are not altered.

After pulling:

```powershell
python client/stereo_vision_client_multiclient.py --self-test
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --yolo-model yolo26n.pt
```

No changes are needed on the camera server.

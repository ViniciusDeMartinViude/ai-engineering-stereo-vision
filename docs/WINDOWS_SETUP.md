# Windows setup — UDP unicast multi-client

The current baseline uses **two Logitech MX Brio cameras**, **GStreamer**, **NVIDIA NVENC H.264**, **MPEG-TS over UDP multicast**, and the original **168-bit visual timestamp with CRC-16**.

## Supported Python version

**Python 3.12.x is the required version for the merged Qt client and the recommended Windows development environment.** Use `environment-qt312.yml` to provision PySide6, PyQtGraph, GStreamer and their dependencies together from conda-forge. Keep any legacy working environment intact; do not change Python in-place.

## Server dependencies

In PowerShell:

```powershell
conda env create -f environment-qt312.yml
conda activate gst-stereo-qt312
python --version  # must show Python 3.12.x
conda activate gst-stereo-qt312
gst-inspect-1.0 dshowvideosrc
gst-inspect-1.0 nvh264enc
gst-inspect-1.0 multiudpsink
```

The source computer needs an NVIDIA GPU and functioning NVENC GStreamer plugin. Check that both camera indexes are correct before streaming.

## Server startup

```powershell
Copy-Item config/clients.example.txt clients.txt
notepad clients.txt
python server/stereo_vision_server_multiclient.py --clients-file clients.txt
```

Replace sample IPs with receiving computers' actual wired Ethernet IPv4 addresses. Changing `clients.txt` during streaming reloads recipients in approximately two seconds.

For the first same-computer test:

```powershell
python server/stereo_vision_server_multiclient.py --clients 127.0.0.1
```

Useful options: `--fps 10 --width 640 --height 480 --format mjpeg --bitrate 3000 --no-preview --port 5000`.

## Client dependencies and startup

**Recommended client:** `python client/stereo_vision_client_qt.py --iface-ip YOUR_RECEIVER_IPV4 --time-server SERVER_IPV4` under `gst-stereo-qt312`. Run `python client/stereo_vision_client_qt.py --self-test` first; see [Qt Client Guide](QT_CLIENT.md). The following Tkinter commands are retained as fallback examples.

Use the same Python 3.12 Conda environment (NVENC not required on student computers):

```powershell
conda activate gst-stereo
python client/stereo_vision_client_multiclient.py --self-test
python client/stereo_vision_client_multiclient.py --port 5000
```

Close VLC and older clients before receiving; a single receiver normally owns UDP port 5000.

On **each receiving computer**, allow UDP on a trusted Private network (PowerShell as Administrator):

```powershell
New-NetFirewallRule -DisplayName "Stereo Vision UDP 5000" -Direction Inbound -Protocol UDP -LocalPort 5000 -Action Allow -Profile Private
```

Check IP addresses:

```powershell
Get-NetIPAddress -AddressFamily IPv4 | Where-Object {$_.AddressState -eq 'Preferred'}
```

## Test plan

1. First run the server with `127.0.0.1` and one client on the same PC; confirm increasing decoded frame and valid CRC counters.
2. Run on one remote PC.
3. Add a second destination IP to `clients.txt`; save without restarting server.
4. Compare valid CRC ratio, missing sequence counts and apparent latency across both clients.
5. Increase clients gradually and monitor source network utilization.

## Interpretation and limitations

- Direct UDP *unicast*: one encoded stream, but the source network transmits a separate copy to each destination.
- The timestamp reflects a software frame-arrival time, **not** guaranteed simultaneous camera exposure.
- Apparent one-way frame age can be biased by host clock offsets; synchronize clocks before interpreting it as absolute latency.
- UDP has no retransmission or guaranteed delivery. If losses increase, check link utilization, switch congestion and receiver processing.
- WebRTC/WebSocket prototypes are not required for this baseline.


## Automatic camera negotiation (current server)

The server first tests the requested camera input mode, then independently tries alternate MJPEG and YUY2 capture modes at the configured resolution, 1280×720, and 640×480, including common 10/15/30 FPS rates and unconstrained frame rate. It **accepts a mode only after an actual frame arrives**, not just when GStreamer enters PLAYING. Each successful capture is normalized to the configured outgoing resolution. Different input modes may be selected for the left and right cameras.

If a camera later produces a GStreamer error or stops delivering frames for 5 seconds, the server attempts to reconnect it, prioritizing its last working mode. If all modes fail, it retries after a short cooldown without terminating the UDP publisher. Expect temporary gaps in the stereo stream during recovery.

Example:

\`\`\`powershell
python server/stereo_vision_server_multiclient.py --clients-file clients.txt --width 640 --height 480 --fps 10 --format mjpeg
\`\`\`

Watch the console for \`Camera 0: selected ...\` and \`Camera 1: selected ...\`. They can differ. The output FPS and pixel dimensions do not need to match either camera's capture format exactly.

### If no capture mode works

- Close Logi Options+, Teams, and other applications that might access the webcams.
- Unplug and reconnect the cameras directly to USB ports, then restart the server.
- Confirm the correct DirectShow device indexes; connecting to a different USB port can reorder them.
- Inspect supported modes with FFmpeg: \`ffmpeg -list_devices true -f dshow -i dummy\`, followed by \`ffmpeg -f dshow -list_options true -i video="EXACT DEVICE NAME"\`.
- Persistent \`not-negotiated\` errors with all modes indicate a driver/device/USB negotiation problem that software fallback may not resolve.

**Note:** Input fallback can resize a lower-resolution camera image; this does not create real detail. For stereo depth work, prefer identical native capture resolutions and carefully calibrated manual focus on both cameras.

## Coordinated stereo-mode negotiation

The current server tests **matching input capture modes** on both cameras before accepting independent fallbacks. It keeps native dimensions and FPS matched where possible, rather than accepting a left camera at 10 FPS and right camera at 30 FPS solely because those worked first. It tests up to 24 shared modes; unsuccessful attempts may take time. For each candidate it requires both cameras to deliver a real image.

If no shared mode works, the server uses per-camera fallback as before and prints a warning. It also prints *measured* callback FPS for both cameras every 5 seconds, because a requested FPS does not guarantee an actual FPS.

Run on the capture server:

```powershell
git pull origin main
python server/stereo_vision_server_multiclient.py --clients-file clients.txt --width 640 --height 480 --fps 10 --format mjpeg --bitrate 3000
```

Watch for `Stereo negotiated MATCHED capture mode`, or the explicit warning when a matching mode is unavailable. Check the **measured** FPS and timestamp skew; software-timestamp pairing is not the same as synchronized sensor exposure. Avoid using this stream for moving-object metric-depth measurements without calibration and motion-skew characterization.

## Full HD preview and readable UI

Both server and client now scale their **display** to the Windows monitor instead of forcing a 1280-pixel-wide preview. This leaves the original Full HD source frames and UDP binary timestamp encoding untouched.

On the server: press `1` to view both cameras, `2` for a larger left-camera preview, `3` for a larger right-camera preview, `F` for fullscreen and `S` to save the original full-resolution composite. The server now opens the Qt **server console** (see [Server console](SERVER_CONSOLE.md)): close the window to stop, `Esc` returns to the maximized window and `D` opens the Stream tab. With `--preview opencv` the old window is used, where `Q` or Escape exits.

On a student client: the same `1`/`2`/`3` and `F` keys switch local display modes. Text in the dashboard is rendered at its final display size, preventing small letters from being shrunk alongside the video. The client continues decoding timestamps from the full incoming frame before applying any display crop.

Example Full HD capture (the two 1920×1080 views are downscaled *only for viewing* on a Full HD monitor):

```powershell
python server/stereo_vision_server_multiclient.py --clients-file clients.txt --width 1920 --height 1080 --fps 10 --format mjpeg --bitrate 12000
```

If your computer is running at a different native desktop resolution, the viewer adapts its preview size automatically. The Windows preview is still constrained by the physical display; use single-camera focus mode to inspect more detail.

## Multicast networking (default)

Both programs default to `--transport multicast` with the administratively scoped IPv4 group `239.10.10.10` and UDP port `5000`. No per-client IP configuration is necessary:

```powershell
python server/stereo_vision_server_multiclient.py --width 1920 --height 1080 --fps 10 --format mjpeg --bitrate 12000
python client/stereo_vision_client_multiclient.py --port 5000
```

Run the second command on each receiving machine. The receiver explicitly joins the multicast group using GStreamer's `udpsrc multicast-group` and `auto-multicast` properties. If Windows has multiple active network adapters, use `--multicast-iface` with the name expected by GStreamer or disable the unused adapter for testing. On the sender, `multiudpsink` sends one copy to the multicast group with multicast TTL=1 (local LAN only).

**Important:** The TP-Link Archer C54 router's IPTV/VLAN page showing IGMP snooping enabled does not verify IGMP support on a separate Ethernet switch. Check the actual switch, keep sender and receivers on the same VLAN, and provide an IGMP querier if the switch's snooping implementation requires one. No need to enable IPTV/VLAN to use multicast.

**Unicast fallback** for individual destinations:
```powershell
python server/stereo_vision_server_multiclient.py --transport unicast --clients-file clients.txt
python client/stereo_vision_client_multiclient.py --transport unicast --port 5000
```

Use the local Windows firewall to permit inbound UDP port 5000 on the trusted private network. Change the group on both ends with `--multicast-group` if necessary. The video encoder, 168-bit strip and CRC-16 decoder are unchanged.

## Multicast troubleshooting: waiting for UDP frames

A client opening its preview but showing no video is not enough to identify the cause: no multicast packets, wrong receiving interface, Windows Firewall, or MPEG-TS decoding are all possible.

1. On both PCs run `Get-NetIPConfiguration` to identify **each machine's own Ethernet IPv4**.
2. Temporarily close the video client and run the raw Python probe (no GStreamer/encoder):
   - On the student receiver: `python tools/multicast_probe.py receive --iface-ip RECEIVER_ETHERNET_IP`
   - On the camera server: `python tools/multicast_probe.py send --iface-ip SERVER_ETHERNET_IP`
3. The probe uses **UDP 5001** to avoid interfering with the MPEG-TS stream on 5000. Allow inbound UDP 5001 on the trusted Private LAN for this test. The receiving screen should show `RECEIVED` every second.
4. If the probe receives nothing, check sender and receiver are on the same VLAN, actual switch IGMP snooping/querier, and any Windows VPN/virtual Ethernet adapters. IGMP settings in the router screenshot alone do not establish that the switch is forwarding multicast.
5. If the probe works, inspect network and video separately:
   ```powershell
   # Receiver: count raw MPEG-TS packets using GStreamer; stop video client first
   gst-launch-1.0 -v udpsrc multicast-group=239.10.10.10 auto-multicast=true port=5000 ! fakesink dump=false
   ```
   If this receives data, isolate the GStreamer caps / MPEG-TS decoder next.

You can always revert to tested unicast without changing code:
```powershell
# Server
python server/stereo_vision_server_multiclient.py --transport unicast --clients-file clients.txt
# Receiver
python client/stereo_vision_client_multiclient.py --transport unicast --port 5000
```

## Video multicast diagnosis: the 5001 probe passes, but no picture

The generic probe on UDP 5001 only establishes that the LAN can carry multicast packets. It does **not** prove that the GStreamer process is sending MPEG-TS on UDP 5000 or using the same network interface.

1. Stop the student video client, keep the camera server running, and inspect server logs. The report now prints `UDP sink sent=...B rate=...Mbps` every five seconds. If it stays zero, check the NVENC encoder and multicast fanout.
2. On a receiving PC, use its own local Ethernet IPv4 address:
   ```powershell
   python tools/multicast_video_probe.py --iface-ip RECEIVER_ETHERNET_IPV4
   ```
   This test listens on the **actual video port 5000**, joins `239.10.10.10` on the specified adapter, and validates raw MPEG-TS sync bytes `0x47` at 188-byte boundaries. Stop the normal client first to avoid port conflicts.
3. If no packets arrive on port 5000 while UDP sink bytes are increasing, the sender may be using another NIC. Check network adapters on the source with `Get-NetAdapter` and compare GStreamer's multicast interface setting with the successful Python probe. The `--multicast-iface` argument is an optional GStreamer **interface name**, not necessarily an IPv4 address; actual naming depends on the Windows/GStreamer build.
4. If MPEG-TS packets arrive but no video is decoded, run a standalone receiver:
   ```powershell
   gst-launch-1.0 -v udpsrc multicast-group=239.10.10.10 auto-multicast=true port=5000 caps="video/mpegts,systemstream=(boolean)true,packetsize=(int)188" ! tsparse ! tsdemux ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false
   ```
   Some builds require `tsdemux name=d d. ! h264parse ...` for a dynamic pad. Look for error messages or whether video appears.

Avoid modifying the CRC decoder until actual MPEG-TS/H.264 frame delivery is confirmed. The unicast fallback remains available.

## Windows GStreamer multicast interface workaround

If `tools/multicast_video_probe.py` receives valid MPEG-TS datagrams but GStreamer is silent at the `udpsrc`, GStreamer may have joined the wrong NIC. The latest **multicast client** uses the same proven Python socket join as the probe, then forwards the original packets, byte-for-byte, to GStreamer over `127.0.0.1:15000`.

On the receiver `192.168.0.11`:

```powershell
git pull origin main
python client/stereo_vision_client_multiclient.py --port 5000 --iface-ip 192.168.0.11
```

On other receivers, replace `--iface-ip` with **that receiver's own Ethernet IPv4**. This is mandatory in multicast mode. Do **not** provide the camera server IP or router IP.

The server requires **no changes**. The relay does not transcode, alter timestamps or change UDP packet contents. It does add a loopback pass and small CPU overhead. Expect status output to show `multicast_udp=...` increasing even before decoded video appears. If that counter increases but `decoded=0`, investigate MPEG-TS demux/decoder rather than multicast delivery.

If UDP port 15000 is already in use by another local process, specify `--relay-port 15001`. Stop the raw multicast video probe and other video clients before starting this application.

The direct GStreamer multicast pipeline is still available as a diagnostic, and may work if the correct Ethernet interface is explicitly chosen. The Python relay is the current default Windows workaround because the raw socket test proved the interface can receive packets.

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

## Binary timestamp visible again in maximized previews

The server and client preview layouts include the original **100-pixel metadata footer**, including the black-and-white 168-bit timestamp/CRC16 strip. This fixes an earlier display-only crop that hid it. In `1` (both cameras) mode the entire original composite is displayed and scaled to the available window. In `2` and `3` (single-camera) mode the original shared footer (left half, where the strip is encoded) is shown underneath the selected camera. No network video pixels, encoded metadata, or decoder logic are modified.

Both Windows windows continue opening **maximized**, with `F` available for fullscreen. Pull the latest changes on both PCs and restart both applications.

## Enlarged timestamp footer and latency chart legend

The server and receiver now show a **display-only enlarged** copy of the original 168-bit binary timestamp/CRC16 row, scaled with nearest-neighbor to retain sharp black-and-white bars. The original encoded bit cells inside the multicast MPEG-TS frames are unchanged. The server footer also displays large LEFT and RIGHT UTC labels. The receiver displays both UTC timestamps in large type.

The receiver latency graph now has a legend (**yellow line = estimated frame age, milliseconds**), top and bottom Y-axis numbers, and time/order direction toward newer frames. The chart is shown in its own HUD region to avoid overlapping text. Both windows still start maximized, and `F` toggles fullscreen.

Pull on both machines and restart each program:
```powershell
git pull origin main
```

## YOLO object detection on the student client

The client can run **local Ultralytics YOLO object detection** on both stereo images, independently. This is optional: without \`--yolo-model\`, the existing multicast viewer works unchanged. It draws bounding boxes, class names and confidence percentages over the displayed images, along with an inference status/count. The model sees only the camera image regions (not the 100-pixel binary timestamp footer). Original H.264 packets and CRC16 timestamp are unchanged.

Install YOLO into your existing \`gst-stereo\` Conda environment **on each student/receiver PC that will run inference**:

\`\`\`powershell
conda activate gst-stereo
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

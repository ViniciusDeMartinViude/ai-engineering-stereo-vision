# Windows setup — UDP unicast multi-client

The current baseline uses **two Logitech MX Brio cameras**, **GStreamer**, **NVIDIA NVENC H.264**, **MPEG-TS over UDP multicast**, and the original **168-bit visual timestamp with CRC-16**.

## Server dependencies

In PowerShell:

```powershell
conda create -n gst-stereo -c conda-forge python=3.11 gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad gst-libav pygobject pycairo numpy opencv -y
conda activate gst-stereo
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

Install the same Conda environment (NVENC not required on student computers):

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

On the server: press `1` to view both cameras, `2` for a larger left-camera preview, `3` for a larger right-camera preview, `F` for fullscreen, `S` to save the original full-resolution composite, `Q` or Escape to exit.

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

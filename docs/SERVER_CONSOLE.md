# Stereo Vision Server console

The camera server now has an operator window in the same design as the Stereo Vision Studio client. It replaces the old OpenCV preview, which drew Hershey text and an enlarged barcode on top of the video. The console reuses the client's theme, icons, status pills, KPI cards with seven-segment readouts, strip chart, barcode view and overlays from `client/`.

**Nothing that clients receive has changed:** same capture negotiation, frame composition, burned-in LEFT/RIGHT labels, 168-bit timestamp + CRC-16 strip, NVENC settings, multicast/unicast transport and LAN SNTP service.

## Run

```powershell
conda activate gst-stereo-qt312
python server/stereo_vision_server_multiclient.py --self-test
python server/stereo_vision_server_multiclient.py
```

The console needs PySide6 + pyqtgraph, which `environment-qt312.yml` installs. If they are missing, the server prints a note and opens the legacy OpenCV preview instead.

| Option | Effect |
|---|---|
| *(default)* `--preview qt` | Qt server console, opened maximized |
| `--preview opencv` | The previous OpenCV preview window (1/2/3, F, S, Q/Esc) |
| `--no-preview` | Headless: terminal log only, Ctrl+C stops |
| `--preview-mode left\|right\|fit` | Initial camera layout in the window |
| `--preview-fps 15` | Maximum preview refresh rate (never affects the stream) |
| `--self-test` | Builds the console with synthetic frames and checks its states. Needs no cameras, encoder or network |

All other options (`--width`, `--fps`, `--format`, `--bitrate`, `--transport`, `--clients-file`, `--ntp-port`, ...) are unchanged.

## What the window shows

- **App bar pills**, each with a colour, a shape and a word, with details in the tooltip; click any pill to open the Stream tab:
  - **Cameras**: *Both live* / *Left stalled* / *Right lost* / *Negotiating*. The tooltip shows each device, its capture mode, its measured fps and the time since its last frame.
  - **Stream**: the current UDP output rate (e.g. *3.2 Mbps*), or *Starting*, *Stalled*, *Not sending*, *No UDP output* or *Stopped*. The tooltip lists the transport, destinations, encoder settings and total bytes sent.
  - **Clock**: *Serving :9123* with the number of SNTP requests answered, *Off* (`--no-ntp`) or *Unavailable* (the port could not be bound).
  - **Pairing**: *Matched modes* when both cameras negotiated the same native mode. *Independent* means each camera fell back to its own mode, which can add skew.
- **Camera tiles** show the left and right halves of the **outgoing** frame, exactly as clients receive it. Each header has the device index, the native capture mode, the measured camera fps and the source age of the last sent frame. Drag to pan, use the wheel to zoom and double-click to fit. **Expand view** / **Both cameras** switch the layout. Overlays show *Opening camera*, *Camera stalled* (0.5 s), *No signal* (3 s), *Recovering camera* or *Server stopped*, over a dimmed last image.
- **KPI cards:**
  - **Camera skew R−L**: the main reading, smoothed with τ = 1.5 s like the client, with the last raw value below.
  - **Output rate**: pairs sent per second, against `--fps`. OK at 90 % or more, Watch from 50 %.
  - **Source age**: mean age of the older camera frame when each pair is composed, over the last 100 pairs, with P95. This is the capture-to-send part of the latency.
  - Cards turn grey with a *Stale* badge when nothing is being sent.
- **Analysis card tabs:**
  - **Camera skew** and **Source age**: strip charts with 100/500/2000-frame windows, pause (`P`) and hover readout.
  - **Stream**: the outgoing 168-bit strip labelled by field. The server decodes it with the same decoder the clients use, so *CRC valid* confirms the encoding. Also shows the pair sequence, UTC times, per-camera source age, destination, UDP rate and bytes, encoder, output frame size, SNTP requests, and the **event log** (negotiation, recovery, client-list reloads, errors).
- **Snapshot** (`S`) saves the full-resolution outgoing frame as `stereo_snapshot_NNNNNNN.png` in the current folder, as before.

Status limits are the provisional defaults in `client/theme.py` and are read-only on the server. Per-workstation limits remain a client feature.

Keyboard: `1` both cameras · `2` left · `3` right · `F` full screen · `Esc` maximized · `D` Stream tab · `P` pause the chart · `S` snapshot. Closing the window (or Ctrl+C in the terminal) stops the stream and releases the cameras.

## Architecture change

Capture, compositing, NVENC encoding and UDP sending now run on their own thread (`ServerEngine` in `server/stereo_vision_server_multiclient.py`). The console only reads state snapshots and a copy of the latest frame, made at most `--preview-fps` times per second. Previously the preview was drawn inside the send loop, so a slow redraw could delay a frame. Camera recovery, which can take tens of seconds, also no longer freezes the window. The terminal still prints the 5-second `Pair ... | source ages ... | skew ...` report.

If the engine stops on an error (for example the NVENC pipeline fails), the window stays open. It shows *Stopped* and the reason in the pills, the overlays and the event log.

## Verify on the camera PC

1. `python server/stereo_vision_server_multiclient.py --self-test` (28 checks).
2. Start the server. Cameras and Pairing show *Negotiating*, then *Both live* and *Matched modes*. Stream shows about 3 Mbps at the default bitrate, and Clock shows *Serving :9123*.
3. Press `D`: *Outgoing strip decodes · CRC valid*, and the pair sequence increases.
4. Start a client, `python client/stereo_vision_client_qt.py --iface-ip CLIENT_IP --time-server SERVER_IP`. Its Stream, Clock and Data pills turn green and the SNTP request count on the server increases.
5. Unplug one camera: its tile shows *Camera stalled*, then *Recovering camera* / *No signal*, and the event log records the recovery attempts. Plug it back in and it recovers.

Tested on the camera PC (2 × MX Brio, RTX 5070, conda-forge GStreamer 1.28.9, Python 3.12) with the client on the same PC over loopback multicast, 30 s per run:

| Server | Valid / invalid / lost | Mean delay | P95 |
|---|---|---|---|
| Previous (OpenCV preview) | 301 / 0 / 0 | 287 ms | 373 ms |
| Console (this version) | 290 / 0 / 0 | 261 ms | 348 ms |

Camera mode negotiation varied between runs. Both servers negotiate the same way.

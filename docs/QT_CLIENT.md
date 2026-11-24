# AI Stereo Vision Studio — PySide6 + PyQtGraph

**Official client on `main`. Required Python version: 3.12.x (Windows).**

This is the recommended desktop UI. The original Tkinter client is retained as a fallback. The camera server, original Tkinter client, GStreamer multicast relay, 168-bit CRC16 timestamp protocol, LAN SNTP, and local YOLO worker are still available and unchanged.

## Setup (Windows / Conda) — required Python 3.12

**Use Python 3.12.x for the Qt client. Do not downgrade the existing working `gst-stereo` environment in place.**
The older environment may have PySide6/Qt DLLs installed through several package
managers. Create a completely new environment from this branch instead.

### A. Update main

```powershell
git fetch origin
git switch main
git pull --ff-only origin main
```

### B. Create the Python 3.12 environment

You can create it from the reproducible YAML file:

```powershell
conda env create -f environment-qt312.yml
conda activate gst-stereo-qt312
```

If you **already have** an incomplete environment with that exact name,
delete only that experimental environment (never delete `gst-stereo`):

```powershell
conda deactivate
conda env remove -n gst-stereo-qt312
conda env create -f environment-qt312.yml
conda activate gst-stereo-qt312
```

The YAML installs Python 3.12, GStreamer, PyGObject, PySide6, PyQtGraph,
OpenCV, NumPy and Pillow from **conda-forge** in one solver transaction.
Do not run a separate `pip install PySide6` on top of this environment.

### C. Check Python and binary imports

```powershell
python --version
where.exe python
python -c "from PySide6 import QtCore, QtWidgets; import pyqtgraph; print('Qt:', QtCore.qVersion())"
python -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None); print('GStreamer OK')"
gst-inspect-1.0 udpsrc
gst-inspect-1.0 tsdemux
gst-inspect-1.0 avdec_h264
python client/stereo_vision_client_qt.py --self-test
```

The Python version should be `3.12.x`. The Qt and GStreamer import checks
should pass *before* starting multicast.

### D. Optional YOLO installation — only after the basic Qt client works

```powershell
python -m pip install ultralytics
python -c "import torch; print('CUDA available:', torch.cuda.is_available())"
```

Ultralytics may install or update PyTorch or OpenCV dependencies. If Qt/DLL
imports break after installing Ultralytics, create another fresh environment
and check the package changes before proceeding. `--yolo-device cpu` is a
valid initial fallback when CUDA is not ready.

### Diagnosing Qt DLL import failures

If `QtCore` fails with a DLL import error even in the clean Python 3.12
environment, inspect `conda list`, `where.exe python`, `where.exe qmake`,
and Windows `PATH` / Qt-related environment variables for foreign Qt DLLs.

Do not mix pip PySide6 wheels with Conda's Qt libraries in the same environment.

Start the app on the tested Windows receiver at `192.168.0.11` (change to the **local Ethernet IPv4** of each student PC):

```powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10
```

With a custom local model:

```powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --yolo-model "C:\models\best.pt" --yolo-conf 0.5 --yolo-imgsz 640 --yolo-device 0
```

With a general pretrained model, replace the model path with `yolo26n.pt` (Ultralytics may download weights).

## Features

- Maximized **dark-themed** desktop app; the camera row is sized to the video aspect ratio, with the remaining height for the dashboard
- App-bar status pills for **Stream, Clock, YOLO and Data integrity**; click any pill for Diagnostics
- High-resolution image rendering with PyQtGraph and independent per-camera zoom / pan, plus a **Fit** button to reset the view
- Mouse-only ROI per camera: **drag** to set (outside area is shaded), **double-click** or **×** to clear; native-coordinate cropping before YOLO. Each camera's ROI is remembered on this PC. At the next start it's applied to the first frame. If the camera resolution changed but has the same shape, the ROI is scaled. If the shape changed (e.g. 640×480 → 1920×1080), it isn't applied, because the view is different. A hint says so, and the ROI is kept for when that resolution returns.
- **Output** pill: publishes both cameras' detections as JSON over UDP for another program (e.g. a robot backend). It shows the send rate and ripples on each message; click it for the switch, destination and the latest message. See [Detection output](DETECTION_OUTPUT.md)
- **Speed** per camera (gauge button in the camera header): shows each tracked object's speed on its label (`#7 · 142 mm/s`, or `still`), with an arrow to where it will be in half a second. See [Object speed](#object-speed)
- **Magnifier** while you drag an ROI or a levelling line: a 200-pixel window in a corner of the camera tile enlarges the image around the pointer about 4× (up to 12× native pixels), without smoothing. The box or line you're drawing and a crosshair are drawn on top. It follows new frames, and it moves to another corner when the pointer or the drag's start point comes near it. Once **Level** is armed, it also shows while you hover, so you can place the start of the line.
- **Level** per camera, for cameras that aren't square to the conveyor: click the spirit-level button, then drag a line along the conveyor, and the image turns so the belt runs straight (see [Levelling the cameras](#levelling-the-cameras))
- Latest-frame asynchronous YOLO predictions with per-class colours on both cameras; **Labels** options show class name, class ID, confidence, box centre (x, y), box size and a centre marker (presets Minimal / Standard / Detailed)
- KPI readouts (average delay, P95, smoothed skew) coloured by OK / Watch / High limits that each workstation can set in the **Status limits** window; greyed as *Stale* when frames stop
- **Detections** tab in single-camera view: class, ID, confidence, centre and size per detection, sortable, copyable (Ctrl+C) and pausable
- Delay/skew history with threshold bands, mean line and hover readout; Diagnostics with the field-annotated 168-bit CRC16 barcode
- Original incoming frames unchanged; client-only image adjustments affect display and YOLO input only

Keyboard: `1` both cameras, `2` left, `3` right, `F` fullscreen, `Esc` cancels a levelling line or returns to the maximized window, `D` Diagnostics, `P` pause the chart or detections table, `L` cycle label presets, `M` choose the YOLO model.

With `--model-library http://SERVER_IP:8765`, the **Model** button lists the camera server's trained models and switches detection to the one you pick, downloading it first if needed. See [Model library](MODEL_LIBRARY.md).

## Levelling the cameras

When a camera isn't mounted square to the conveyor, the belt runs across its image at an angle. Each camera can be turned in the client to straighten it:

1. Click the **Level** button (spirit-level icon) in that camera's header. The hint at the bottom of the image changes to *Drag along the conveyor*.
2. Drag a line along an edge of the belt, as long as you can. The magnifier helps you put both ends exactly on the edge, and a label shows the tilt while you draw.
3. Release. The image turns so that line is horizontal, and the header shows the angle, e.g. **Level 4.99°**.

- A line closer to vertical is made vertical instead, so a belt running up the image is never turned sideways.
- To fine-tune, draw again on the levelled image. The new correction is added to the current angle.
- **×** next to the angle undoes it. **Esc**, or the Level button again, cancels a line you haven't drawn yet. A drag shorter than 40 pixels is ignored.
- Each camera has its own angle. The client remembers both on this PC and applies them at the next start.

What is turned:
- The image you see, the ROIs you draw and what YOLO looks at all use the levelled image. Box coordinates, centres and the Detections table are in levelled-image pixels.
- The image keeps its size, so the corners the turn uncovers are black. An existing ROI stays where it is on screen; redraw it if it should follow the belt. Detections from before the change are dropped.
- The incoming stream and the timestamp strip are not touched: delay, skew and the Diagnostics barcode are read from the original frame.
- It costs about 4 ms per frame for one 1920×1080 camera, and 6 ms for both, on the camera PC's CPU.

This is a 2D rotation for viewing and detection, not stereo calibration. It doesn't correct lens distortion or perspective, and depth measurements will need a proper calibration later.

## Object speed

Click a camera's **Speed** button (the gauge, next to Level) to show how fast each detected object moves on that camera. The label gets a second line with its track number and speed, e.g. `#7 · 142 mm/s`. An arrow points from the object to where it will be half a second later. Objects slower than 4 px/s read `still`. A new object reads `measuring` for its first ~0.15 s.

**Millimetres per second:** open the small arrow beside the Speed button, choose **Set scale…**, and drag along something of known length lying on the belt, such as a ruler or a box of known size. The magnifier helps you place both ends. Type its length in mm. That camera then shows mm/s. The scale is per camera, remembered on this PC, and **Clear scale** returns to px/s. The scale is right for objects on the belt plane. It's less exact near the image edges, because of lens distortion and perspective.

How it's measured:
- **Tracking:** each camera has its own Ultralytics ByteTrack tracker, so an object keeps the same number from frame to frame. Ultralytics' `model.track()` would share one tracker between the two images in a batch, mixing the cameras. So the client feeds each camera's detections to its own tracker, from the same single YOLO run. Tracking costs no measurable YOLO time.
- **Speed:** the same measure as Ultralytics' `SpeedEstimator`, the displacement between the first and last position over a short history divided by the time between them. Two differences:
  - Time comes from each frame's **camera capture timestamp**. `SpeedEstimator` counts frames at an assumed frame rate, which is wrong here because YOLO skips frames whenever it's slower than the video.
  - The history is a rolling 0.7 s window, so the speed keeps updating. `SpeedEstimator` fixes each object's speed after its first frames.
- Levelling a camera starts new tracks on it, because positions before and after the turn aren't comparable.
- The speeds, track IDs and scale are also sent with the [detection output](DETECTION_OUTPUT.md).

The client sets `MKL_THREADING_LAYER=SEQUENTIAL` when it starts. PyTorch brings Intel's OpenMP runtime, and conda's NumPy (MKL) would load LLVM's OpenMP on the tracker's first matrix operation. Two OpenMP runtimes in one process abort it ("OMP: Error #15"). Single-threaded MKL loads none, and its matrices here are tiny.

## Technical details

The client uses the shared `client/receiver.py` (`StreamReceiver`) and `common/stamp_protocol.py` (`decode_strip`, `iso_utc`). No changes to the server or transmission format are needed. Qt runs on the main thread, GStreamer delivers its most recent frames in its own callback, and the YOLO worker operates in a separate thread with a one-frame pending queue.

ROIs are in **camera pixels** (of the levelled image when a camera is levelled), not pixels on the Windows screen. They are preview overlays, not a new network encoding. Two camera images feed YOLO independently, and each bounding box is translated back to its camera space.

## Known limitations

- This Qt implementation and requires **live validation on the actual Windows/Conda computer**; the GitHub code was not executed on the physical setup.
- No saved stereo calibration yet. ROIs, level angles and speed scales are remembered per camera on each PC.
- Side-by-side camera capture still has independent sensor exposures; no hardware camera synchronization.
- Depth/disparity and stereo calibration are not yet implemented.
- Qt plugin conflicts can occur if a Conda/Python environment also contains OpenCV packages with bundled Qt plugins. Diagnose Qt platform plugin errors separately before modifying the working GStreamer stack.

To run the retained Tkinter fallback (also available on `main`):

```powershell
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11
```

## Seven-segment timing instruments (bottom telemetry area)

Two large, custom-rendered, anti-aliased **seven-segment** instruments are displayed alongside the latency plot. The original 168-bit binary timestamp / CRC16 strip and plot are both retained:

- **AVERAGE DELAY** (green) is the arithmetic mean of the most recent **up to 300 valid, NTP-corrected left-camera frame ages**, displayed as a rolling average.
- **CAMERA SKEW R-L** (amber) is a time-smoothed signed right-camera-minus-left-camera timestamp difference. A positive sign means the right camera timestamp is later. This is an exponential moving average (1.5-second time constant), not the rolling arithmetic average used for delay.
- Both instruments render **three significant digits** with a moving decimal point: e.g. `1.23 ms`, `12.3 ms`, `123 ms`, `.125 ms`. Values below `0.1 ms` switch to `µs`; values of about `1000 ms` and above switch to `s`. Negative skew shows a minus sign.
- Invalid-CRC frames do not update the readings. Before the first valid frame each instrument displays dashes.
- This is a client display-only improvement; timestamp extraction, multicast, YOLO and ROI selection are unchanged.

Update `main`, activate the Python 3.12 environment, and run:

```powershell
git switch main
git pull --ff-only origin main
conda activate gst-stereo-qt312
python client/stereo_vision_client_qt.py --self-test
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10
```

## Smoother skew display and graph in seconds

The bottom telemetry is arranged **left to right**: rolling-average-delay seven-segment display, signed camera-skew seven-segment display, and the latency trend graph. The enlarged 168-bit binary timestamp/CRC16 strip remains above them.

The **skew display** applies a time-based exponential moving average with a **1.5-second time constant**, and the two large instruments redraw their digits at most **four times per second**. The smoother uses valid CRC frames only; the first valid sample initializes it immediately. The signed raw skew measurement, binary timestamps and NTP offset are **not** filtered or altered. The rolling delay average still covers up to 300 valid frames.

The **graph** continues to show unsmoothed NTP-corrected frame-age samples stored and plotted in **milliseconds**. The custom Y axis automatically switches its visible tick labels and title between **Delay (ms)** and **Delay (s)** according to the current visible range (at about 1000 ms). Zooming updates the unit. PyQtGraph SI-prefix auto-scaling is disabled to avoid misleading `kms` labels.

Run `python client/stereo_vision_client_qt.py --self-test` to check ROI, seven-segment formatting and skew filtering after updating `main`.

## Dynamic latency graph units

The frame-age graph uses a custom `DelayAxis` in `client/stereo_vision_client_qt.py`. Measurement and plotting stay in raw milliseconds, with only the **displayed tick values** converted to seconds when the visible Y-axis range reaches approximately 1000 ms. The axis title changes between `Delay (ms)` and `Delay (s)`; the plot legend remains `Frame delay`, independent of units. The update follows the current visible Y range, including manual zoom. No SI prefixes are applied to the Y axis. The seven-segment displays and skew smoothing are unchanged.

Run `python client/stereo_vision_client_qt.py --self-test` after `git pull origin main` to check both display-unit cases.

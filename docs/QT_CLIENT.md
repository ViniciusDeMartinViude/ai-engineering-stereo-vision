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

- Maximized **dark-themed** desktop app, with resizable camera panels and single-camera focus
- High-resolution image rendering with PyQtGraph and independent per-camera zoom / pan
- **Mouse drag** over each camera after choosing `Select LEFT ROI` or `Select RIGHT ROI`
- Editable/resizable ROI handles, reset per camera, native-coordinate cropping before YOLO
- Latest-frame asynchronous YOLO predictions, class names, boxes, confidence on both cameras
- 168-bit binary CRC16 visual strip remains visible and sharp; original incoming frames unchanged
- NTP-corrected frame age, left/right UTC timestamps, camera skew, CRC statistics
- Real-time latency chart with labelled axes and legend

Keyboard: `1` both cameras, `2` left, `3` right, `F` fullscreen, `Esc` maximized window.

## Technical details

The new client imports the existing `StreamReceiver`, `decode_strip`, and `iso_utc` from `stereo_vision_client_multiclient.py`. No changes to the server or transmission format are needed. Qt runs on the main thread, GStreamer delivers its most recent frames in its own callback, and the YOLO worker operates in a separate thread with a one-frame pending queue.

ROIs are in **original camera pixels**, not pixels on the Windows screen. They are preview overlays, not a new network encoding. Two camera images feed YOLO independently, and each bounding box is translated back to its camera space.

## Known limitations

- This Qt implementation and requires **live validation on the actual Windows/Conda computer**; the GitHub code was not executed on the physical setup.
- Current ROIs are session-only; no saved calibration/ROI configuration yet.
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

The **graph** continues to show unsmoothed NTP-corrected frame-age samples, but plots **seconds** (the millisecond samples divided by 1000) and uses the explicit Y-axis label **Delay (s)** with readable tick labels. PyQtGraph SI-prefix auto-scaling is disabled to avoid misleading `kms` / kilo-millisecond units.

Run `python client/stereo_vision_client_qt.py --self-test` to check ROI, seven-segment formatting and skew filtering after updating `main`.

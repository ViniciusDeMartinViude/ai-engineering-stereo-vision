# AI Stereo Vision Studio — PySide6 + PyQtGraph

**Experimental UI branch:** `feature/pyside6-pyqtgraph-client`

This is a second client UI, not a replacement for the proven Tkinter client. The camera server, original Tkinter client, GStreamer multicast relay, 168-bit CRC16 timestamp protocol, LAN SNTP, and local YOLO worker are still available and unchanged.

## Setup (Windows / Conda) — recommended Python 3.12

**Do not downgrade the existing working `gst-stereo` environment in place.**
The older environment may have PySide6/Qt DLLs installed through several package
managers. Create a completely new environment from this branch instead.

### A. Update the feature branch

```powershell
git fetch origin
git switch feature/pyside6-pyqtgraph-client
git pull --ff-only origin feature/pyside6-pyqtgraph-client
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

The error `ImportError: DLL load failed while importing QtCore: The specified
procedure could not be found` is **not proof** that Python 3.15 is the sole
cause. Check the active Python version and installed packages. If it persists
in the clean Python 3.12 environment, inspect `conda list`, `where.exe
python`, `where.exe qmake`, and Windows `PATH` / Qt-related environment
variables for foreign Qt DLLs.

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

- This is the first Qt implementation and requires **live validation on the actual Windows/Conda computer**; the GitHub code was not executed on the physical setup.
- Current ROIs are session-only; no saved calibration/ROI configuration yet.
- Side-by-side camera capture still has independent sensor exposures; no hardware camera synchronization.
- Depth/disparity and stereo calibration are not yet implemented.
- Qt plugin conflicts can occur if a Conda/Python environment also contains OpenCV packages with bundled Qt plugins. Diagnose Qt platform plugin errors separately before modifying the working GStreamer stack.

To switch back to the established version:

```powershell
git switch main
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11
```

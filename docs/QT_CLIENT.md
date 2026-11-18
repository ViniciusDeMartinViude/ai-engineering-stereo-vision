# AI Stereo Vision Studio — PySide6 + PyQtGraph

**Experimental UI branch:** \`feature/pyside6-pyqtgraph-client\`

This is a second client UI, not a replacement for the proven Tkinter client. The camera server, original Tkinter client, GStreamer multicast relay, 168-bit CRC16 timestamp protocol, LAN SNTP, and local YOLO worker are still available and unchanged.

## Setup (Windows / Conda)

\`\`\`powershell
git fetch origin
git switch feature/pyside6-pyqtgraph-client
conda activate gst-stereo
python -m pip install PySide6 pyqtgraph
python client/stereo_vision_client_qt.py --self-test
\`\`\`

Optional YOLO inference:

\`\`\`powershell
python -m pip install ultralytics
\`\`\`

Start the app on the tested Windows receiver at \`192.168.0.11\` (change to the **local Ethernet IPv4** of each student PC):

\`\`\`powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10
\`\`\`

With a custom local model:

\`\`\`powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --yolo-model "C:\models\best.pt" --yolo-conf 0.5 --yolo-imgsz 640 --yolo-device 0
\`\`\`

With a general pretrained model, replace the model path with \`yolo26n.pt\` (Ultralytics may download weights).

## Features

- Maximized **dark-themed** desktop app, with resizable camera panels and single-camera focus
- High-resolution image rendering with PyQtGraph and independent per-camera zoom / pan
- **Mouse drag** over each camera after choosing \`Select LEFT ROI\` or \`Select RIGHT ROI\`
- Editable/resizable ROI handles, reset per camera, native-coordinate cropping before YOLO
- Latest-frame asynchronous YOLO predictions, class names, boxes, confidence on both cameras
- 168-bit binary CRC16 visual strip remains visible and sharp; original incoming frames unchanged
- NTP-corrected frame age, left/right UTC timestamps, camera skew, CRC statistics
- Real-time latency chart with labelled axes and legend

Keyboard: \`1\` both cameras, \`2\` left, \`3\` right, \`F\` fullscreen, \`Esc\` maximized window.

## Technical details

The new client imports the existing \`StreamReceiver\`, \`decode_strip\`, and \`iso_utc\` from \`stereo_vision_client_multiclient.py\`. No changes to the server or transmission format are needed. Qt runs on the main thread, GStreamer delivers its most recent frames in its own callback, and the YOLO worker operates in a separate thread with a one-frame pending queue.

ROIs are in **original camera pixels**, not pixels on the Windows screen. They are preview overlays, not a new network encoding. Two camera images feed YOLO independently, and each bounding box is translated back to its camera space.

## Known limitations

- This is the first Qt implementation and requires **live validation on the actual Windows/Conda computer**; the GitHub code was not executed on the physical setup.
- Current ROIs are session-only; no saved calibration/ROI configuration yet.
- Side-by-side camera capture still has independent sensor exposures; no hardware camera synchronization.
- Depth/disparity and stereo calibration are not yet implemented.
- Qt plugin conflicts can occur if a Conda/Python environment also contains OpenCV packages with bundled Qt plugins. Diagnose Qt platform plugin errors separately before modifying the working GStreamer stack.

To switch back to the established version:

\`\`\`powershell
git switch main
python client/stereo_vision_client_multiclient.py --iface-ip 192.168.0.11
\`\`\`

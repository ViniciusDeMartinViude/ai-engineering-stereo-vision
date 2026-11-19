# Control-room UI branch — Python 3.12

This is the **experimental** control-room redesign, kept separate from the working `main` branch. Do not merge until it passes the hardware tests below.

## Run on the Windows receiver

```powershell
cd C:\Users\vviud\aiesnc27\ai-engineering-stereo-vision
git fetch origin
git switch feature/control-room-ui
git pull --ff-only origin feature/control-room-ui
conda activate gst-stereo-qt312
python --version
python client/stereo_vision_client_qt.py --self-test
```

Python must report **3.12.x**. The self-test checks ROI selection controls, seven-segment formatting, skew EMA, the dynamic ms/s axis, and core dashboard widgets **without networking**.

To use the same RTSP-free multicast stream you already have:

```powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --yolo-model yolo26n.pt --yolo-device 0
```

Replace 192.168.0.11 with the receiver's Ethernet IP if it changed. Stop other UDP port 5000 clients before launching. Keep your existing camera server running; this branch does not change it.

## Visible changes

- Stream, clock, and YOLO state chips with text/icons (waiting/live/stale/error)
- Segmented view selector (keyboard 1/2/3), a help button (F fullscreen, Esc maximized)
- Two PyQtGraph camera panels with receiving FPS, size, delay HUDs, and stale/no-signal overlays
- Reused YOLO rectangle/label graphics, and the original independent ROI controls
- Compact aligned telemetry grouped as valid/error counts, timestamps, last delay/skew, and clock
- CRC barcode moved into an expandable Diagnostics pane (stays collapsed unless explicitly opened)
- Unified KPI cards: average delay (last 100 valid frames), p95 & jitter, skew EMA (1.5-second time constant) plus last raw skew
- Framed graph with Delay/Skew toggle, selectable 100/500/2000-frame window, pause, mean/target lines and crosshair inspection
- Original seven-segment LEDs with dim inactive segments, more legible minus sign, and no trailing decimal on integer readings
- All core theme colors, type sizes, card QSS and provisional thresholds centralized in `client/theme.py`
- Chart values remain raw milliseconds; dynamic tick units still switch between ms and s
- Text and charts refresh at 10 Hz while decoded video refreshes when frames arrive

## Ground-truth semantics and caveats

- FPS in the HUD is **received/decoded stereo frame rate**, not independently measured physical sensor FPS.
- Average delay and p95 use the **last up to 100 valid decoded frames**. The configurable graph may show a longer sample window.
- Display skew is an exponential moving average; last skew remains unfiltered.
- The yellow reference line uses **provisional** thresholds in `theme.py`. They are not validated performance requirements. Do not infer hardware pass/fail from them without setting your competition targets.
- Clock quality is based on whether sync completed and its raw RTT, not on a known absolute synchronization guarantee.
- Original GStreamer receiver, multicast packet data, timestamp/CRC decoder, SNTP estimation, and YOLO worker are **unchanged**.
- The existing image path still converts the incoming BGR arrays to RGB for PyQtGraph. Avoid assuming this redesign adds zero GUI overhead without measuring it.
- This code has not been run on the user's Windows system; live UI/screen-size behavior still requires validation.

## Test checklist

1. Run `--self-test`; if it fails, capture the entire traceback.
2. With the real server, compare 60 seconds of latency and CRC counters against `main` on the **same LAN and workload**.
3. Confirm both cameras appear, fullscreen / 1 / 2 / 3 work, and use left-drag ROI, right-drag pan, wheel zoom and double-left-click ROI reset on both camera feeds.
4. Unplug/reconnect the network temporarily and observe the stream STALE / NO SIGNAL overlay, then recovery.
5. Toggle Diagnostics to view the original binary strip; check that CRC errors visibly flag it.
6. Verify average and p95 units, signed instantaneous vs smoothed skew, and graph Delay/Skew, Pause, sample-window selector and hover.
7. Check 1920×1080, 1366×768 and 125%/150% Windows scaling; capture screenshots if labels overlap.
8. Monitor Task Manager or `nvidia-smi` while YOLO runs. If delay consistently increases vs main, record both averages and receiver FPS.
9. Switch back to the known-good application with `git switch main` (ensure no uncommitted local changes).

## Changelog / audit alignment

| Reported issue | Redesign change |
|---|---|
| Unclear overall health | Textual Stream/Clock/YOLO chips |
| Excess video margins | Compact header/ROI chrome; preserved aspect ratio |
| Barcode wastes space | Expandable Diagnostics |
| Footer clipping | Native compact status bar + help |
| Chart lacks card | Matching framed chart |
| Ghost LEDs / decimal / minus | Low-opacity inactive segments, integer-point fix, wider minus |
| Dense telemetry | Aligned labeled groups |
| Confusing delay/skew values | Explicit last / average / EMA windows |
| No FPS/freshness | Received FPS HUD + stale overlay |
| No latency distribution | p95, min/max, jitter |
| Clock sync buried | Dedicated chip and offset/RTT telemetry |
| YOLO status hidden | Dedicated status chip and inference time |
| Chart jump/overlap | Stable envelope, no legend, mean and provisional target |
| Weak view selection | Exclusive segmented control |
| Too many per-frame updates | 10 Hz status/chart timer, reused YOLO items |

Not yet implemented: independently measured **hardware camera FPS**, machine-specific threshold calibration, or removing the original RGB array conversions. Those require additional validated instrumentation or performance testing.

## Camera-first layout adjustment

The camera splitter now receives the flexible vertical space while the bottom KPI/chart row is limited to a compact 110–145 pixel strip. On a typical maximized 1920×1080 display, the target is **approximately two-thirds of the available window height for the cameras**. Actual visible image height still depends on screen scale, window chrome and the incoming image aspect ratio; video remains uncropped and undistorted. The telemetry and diagnostics areas remain compact.

**All three KPI cards** (average delay, P95 delay and smoothed skew) now use three-significant-digit seven-segment readouts. P95 remains the 95th percentile of the most recent up to 100 valid frame ages; the jitter (standard deviation of consecutive delay differences) remains visible in its card's secondary line. No timestamp, latency, skew or decoding calculations were changed.

After updating the branch, verify that all three LED readouts show, the video area is taller, and no controls overlap at the target resolution. If 1366×768 clips the bottom strip, report the screen scaling and screenshot.

## Camera-header controls and reclaimed screen space

The view selector buttons are integrated into **both camera headers**: **◫** restores the two-camera split view and **▣** expands the corresponding left or right camera. Hover tooltips describe the actions; shortcuts **1 / 2 / 3** remain. The separate view-selector toolbar and its **? help modal** were removed; fullscreen **F** and maximized **Esc** shortcuts remain.

The former full-width CRC/timestamp/clock telemetry bar now lives **inside expanded Diagnostics** with the original barcode. The collapsed Diagnostics band shows a small summary (**CRC Valid / Invalid / Lost**) and warns when error/loss counters are nonzero. This releases a full row of vertical space in normal operation.

The lower Average Delay, Delay P95 and Skew seven-segment readouts and framed graph can now grow between approximately **148 and 192 px**, using the reclaimed height while the camera splitter retains the remainder of the window. The ROI controls remain in each camera panel. Expect exact geometry to vary with Windows text scaling and window chrome.

### Verification

1. Pull `feature/control-room-ui`, run `--self-test`, and launch with the known-good Python 3.12 environment.
2. Verify camera-header **◫** and **▣** buttons, plus shortcuts 1/2/3/F/Esc.
3. Open Diagnostics and verify CRC counts, timestamps, clock offset/RTT and barcode. Close it and verify the detailed band disappears.
4. At the same screen resolution, compare the increased seven-segment and chart heights, plus the available camera image height. Check no overlap in fullscreen.

## Refined camera headers

Each panel again uses a readable **LEFT CAMERA** or **RIGHT CAMERA** title, followed by a clear detection-count label. A **single view button** replaces the previous pair of cryptic icon buttons: in split mode it reads **Expand view**, and when that camera is expanded it reads **Both cameras**. The button and its tooltip update together, and keyboard shortcuts **1** (both), **2** (left), and **3** (right) remain available. No new toolbar is introduced.

Use the same Python 3.12 environment and run `python client/stereo_vision_client_qt.py --self-test` after updating the branch. Confirm that each camera's one button expands its panel and returns to both-camera mode, and that toggling does not affect YOLO detections or ROI.

## Camera-header surface and spacing polish

- The permanent bottom shortcut/status line is hidden; the available shortcuts still work (1/2/3 camera views, F fullscreen, Esc maximized). ROI coordinates stay visible beside each camera's ROI controls, instead of flashing in a footer.
- The camera splitter handle width is now **9 px** for a slightly wider visual gap between the two views.
- The LEFT CAMERA and RIGHT CAMERA header bands, and the Diagnostics header containing the CRC Valid / Invalid / Lost summary, now use the **raised-surface** color, border and radius from `client/theme.py`, matching the Expand view button. This avoids the sharp black-background transition.

Recheck both-camera and single-camera layouts, Diagnostics collapse/expand and keyboard shortcuts after updating the branch.

## Mouse-only camera navigation (no ROI buttons)

All Select ROI and Reset ROI buttons are removed. The camera image is interactive immediately; no selection mode is required:

| Gesture | Action |
|---|---|
| **Left-click and drag** | Draw a new ROI in that camera; replaces its previous ROI and updates the YOLO worker's ROI |
| **Right-click and drag** | Pan the image view; does not change ROI coordinates or YOLO crop |
| **Double left-click** | Clear the ROI and return YOLO detection to that camera's **full frame**; does not reset zoom |
| **Mouse wheel** | Zoom in/out around the pointer in PyQtGraph; does not modify image-space ROI coordinates |

The ROI rectangle is now a passive, non-movable graphic overlay rather than a draggable `pg.RectROI`; this prevents mouse gestures from conflicting with the requested controls. The ROI coordinate text stays in the camera header. The two camera panels have independent ROIs and independent zoom/pan view transforms. The existing streaming, timestamp, time sync, and YOLO inference implementations are unchanged.

Test both camera views with and without YOLO, then select a new ROI while zoomed in and confirm the coordinates refer to the original source pixels. Double-click inside the image to clear the ROI; verify the view stays zoomed until manually zoomed out. Recheck the independent right-camera ROI separately.

## Periodic LAN clock resynchronization (5 seconds)

The Qt client now measures its client-minus-server clock offset **on startup and every 5 seconds** using the existing `common/lan_ntp.py` SNTP estimator (`--time-server`, `--ntp-port`, `--ntp-samples`). Measurements run in a single background executor, not on the GUI thread. If a measurement takes longer than the interval, it is not duplicated or queued.

Every successful result **replaces**, rather than adds to, the prior correction: `effective_offset_ms = --clock-offset-ms + latest_ntp_offset_ms`. This is required to avoid manufactured clock drift. Video timestamps and the latency calculation continue to use their original formulas. If a correction differs by 1 ms or more, the recent delay history is cleared so the average/P95 do not mix samples computed against different clock offsets.

If no NTP response arrives, the client keeps using the last successful correction and reports unavailable/stale synchronization in the clock status. Once the last successful sample is older than 15 seconds, the chip reads **SYNC STALE**; successful measurements restore the synced status. `--no-time-sync` skips all periodic NTP work.

### Verify

1. Start the client and observe the **CLOCK** chip and Diagnostics clock offset/RTT fields.
2. Over at least 30 seconds, confirm the offset updates approximately every 5 seconds without cumulatively growing.
3. Temporarily stop the LAN NTP server or block its UDP port 9123; the video and YOLO should continue while the clock becomes stale.
4. Restore the NTP server and verify the sync chip recovers automatically.
5. Run `python client/stereo_vision_client_qt.py --self-test` to check that two successive offset measurements replace rather than accumulate.

This aligns the application's **latency correction** to the server; it does not set or discipline the Windows system clock and is not evidence of traceable UTC synchronization.

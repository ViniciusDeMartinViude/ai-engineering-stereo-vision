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

## Client-side image adjustments (competition-friendly)

The client header has a **☷ Image adjustments** button that toggles a translucent, non-modal floating panel. The panel stays out of the way while live video and YOLO continue updating. It contains:

- **Exposure compensation** (−2.00 to +2.00 EV, applied as software pixel gain)
- **Brightness** (−100 to +100)
- **Contrast** (−100 to +100)
- **White balance temperature** (cool blue ↔ warm red)
- **White balance tint** (magenta ↔ green)
- **Apply adjustments** checkbox, **Reset to neutral**, and **Hide controls** actions

**The same adjustments apply to both camera previews and to the stereo frame submitted to the local YOLO worker.** The multicast server and the original camera settings are not modified, so every competition workstation receives the same source video. Each workstation may have its own adjustments, stored locally using Qt QSettings. The button displays a dot when a non-neutral correction is enabled.

The client processes only the video area (the first `height - 100` rows), leaving the metadata/timestamp footer **byte-for-byte unchanged**. CRC and timestamp decoding always operate on the untouched original received frame. ROI coordinates still refer to the original full-resolution image. The LUT is rebuilt only when settings change; normal video processing uses OpenCV's C++ `cv2.LUT`.

**Important limitation:** Exposure compensation is a software brightness gain, *not* camera shutter, gain or auto-exposure control. If the glossy green belt is saturated by reflected lighting, no post-capture adjustment can recover missing details. Physical lighting improvements may still be necessary. Changes made on one workstation do not change the image other competitors receive, but they can produce different YOLO results; for a fair competition, define which image adjustment settings competitors are allowed to use.

### Test plan

1. Pull the branch, activate the existing Python 3.12 Conda environment, and run `python client/stereo_vision_client_qt.py --self-test`.
2. Launch live streaming and YOLO; open **☷ Image adjustments**.
3. Increase Exposure and Temperature: both displayed camera images should change together, followed by corresponding YOLO results.
4. Disable **Apply adjustments**: both images should immediately return to the original decoded color, without resetting slider values.
5. Toggle the panel closed/open and confirm slider positions persist. Restart the application to confirm per-client persistence.
6. Expand Diagnostics and confirm **CRC Valid** continues to increment without new invalid timestamp errors.
7. Compare receiver FPS and end-to-end delay with all controls neutral versus active, on the same source and machine. Pixel correction may add client-side CPU load.

## Visual and UX redesign (`feature/ui-redesign`)

A visual pass over the whole Qt client. Streaming, timestamp decoding, SNTP, YOLO and the image-adjustment maths are unchanged; the work is in `client/theme.py`, `client/control_room_widgets.py`, `client/seven_segment.py`, `client/image_adjustments.py`, the new `client/icons.py`, and the layout code in `client/stereo_vision_client_qt.py`.

### Layout

- **No letterboxing.** The camera row takes exactly the height its aspect ratio can use at the current width; the remaining height goes to the dashboard (230–430 px). Expanding one camera gives it the extra height and compresses the dashboard.
- **App bar** with four status pills: **Stream**, **Clock**, **YOLO** and **Data** (CRC/lost-frame integrity over the last 10 s). Each pill pairs a colour with a shape (● ▲ ✕ ◆ ○) and a word, keeps a fixed width, and shows details (multicast group, packets/s, RTT, model, error text) in its tooltip. Clicking any pill opens Diagnostics.
- **Dashboard**: average delay as the main reading, P95 and smoothed skew beside it, and an analysis card with **Frame delay | Camera skew | Diagnostics** tabs.

### Meaning through colour

- KPI digits and badges now use the provisional thresholds in `theme.py` (**OK / Watch / High**), and turn grey with a **Stale** badge while no frames arrive, so an old green reading never looks live.
- Cyan is now reserved for interactive elements. Detections get a stable colour per class, chosen to contrast with the green belt; the ROI is white.
- All text colours meet WCAG AA contrast on the surfaces they use.

### Camera tiles

- The header shows the L/R badge, object count (class breakdown in the tooltip), the ROI size with a **×** clear button, live FPS/delay, a **Fit** button (appears only after zooming or panning), and **Expand view / Both cameras**. On narrow windows the FPS readout, then the title, are hidden first.
- **The area outside the ROI is shaded**, so it is obvious what YOLO ignores. While dragging, a live readout shows the selection size in native pixels.
- Hovering over the video briefly shows the mouse gestures: drag sets the ROI, right-drag pans, the wheel zooms, double-click clears the ROI.
- The **Waiting for video** card shows the multicast group, port and interface. **Stream stalled** (after 0.5 s) and **No signal** (after 3 s) dim the last image and hide detections, because boxes on a frozen frame would be misleading for a moving belt.
- The FPS/delay HUD no longer covers the server's burned-in LEFT/RIGHT label.

### Chart and Diagnostics

- The chart shades the provisional watch/high bands (± bands for skew), labels the mean line, fades the area under the line, labels the x-axis in positive "frames ago", and shows a value readout with a crosshair on hover. 100/500/2000-frame windows and pause are segmented controls.
- Diagnostics labels the original 168-bit barcode by protocol field: magic, version, left UTC, sequence, skew and CRC, each with its decoded value. It also shows the latest decode error text and a grid with the timestamps, delay/skew, clock offset/RTT/last sync, UDP packets/s and YOLO status. It scrolls when space is short.

### Image adjustments panel

Opens under its button and always stays inside the window/screen. Controls are grouped as Exposure and White balance, with a cool/warm and magenta/green gradient on the white-balance sliders and direction words in the values (e.g. `-18 cool`). Double-click a slider to reset it. **Apply adjustments** is an on/off switch (use it for before/after comparison). The **Adjust image** button is highlighted while adjustments are active.

### Keyboard

`1` both · `2` left · `3` right · `F` full screen · `Esc` maximized · **`D` toggle Diagnostics** (new) · **`P` pause the chart or detections table** (new) · **`L` cycle label presets** (new, see Detection labels below).

### Verify on the receiver

1. `python client/stereo_vision_client_qt.py --self-test` (now also checks the overlays, ROI shading, KPI states and analysis tabs).
2. Start without the server running: both tiles show **Waiting for video** with the correct group/port/interface, and **Stream** reads *Waiting*.
3. Start the server: video fills both tiles without black bands. **Stream / Clock / YOLO / Data** turn green and the KPIs read *OK* if delay is under 150 ms.
4. Draw an ROI on each camera; check the shaded outside area, the header size, **×** clear and double-click clear. Zoom with the wheel; **Fit** appears and resets the view.
5. Stop the server for ~5 s: *Stream stalled* then *No signal*, detections disappear, KPIs show *Stale*; restart and confirm recovery.
6. Press `D`: barcode fields decode, CRC shows *valid*, counters match. Press `P` and hover the chart.
7. Repeat at 1366×768 and with Windows scaling at 125 %/150 %; capture screenshots if anything clips.
8. Compare receiver FPS and delay against the previous build on the same LAN; the per-frame path is unchanged apart from detection colouring.

## Detection labels (annotation detail options)

The app bar's **Labels** button (shortcut **`L`** cycles the presets) opens a popover that controls what each YOLO detection shows on the video. Changes apply immediately to both cameras and are saved per workstation, like the image adjustments.

| Option | Shows | Example |
|---|---|---|
| Class name | Model class name | `box` |
| Class ID | Numeric class index from the model | `id 0` |
| Confidence | Detection confidence | `94%` |
| Center (x, y) | Box centre in native camera pixels, origin top-left | `(812, 455)` |
| Box size (w × h) | Box width × height in native camera pixels | `190×140 px` |
| Center marker | Crosshair at each box centre (constant on-screen size at any zoom) | ⊕ |
| Show detections | Master switch to inspect the raw image; object counts keep updating | |

Presets: **Minimal** (class name), **Standard** (class name + confidence, the default) and **Detailed** (all of the above). Any other combination shows as **Custom**. Labels put identity on the first line and geometry on the second, e.g. `box · id 0 · 94%` / `(812, 455) · 190×140 px`; a live preview in the popover shows the current format. Labels that would run past the image's right edge align to their box's right edge instead.

Centre and size use the same native camera pixel space as the ROI (YOLO crop offsets are already removed), so they can be compared directly with ROI coordinates. They describe the detection box, not a calibrated 3-D position.

### Verify

1. `python client/stereo_vision_client_qt.py --self-test` (checks label text for each preset, Custom detection, centre markers and hiding).
2. With YOLO running, press `L` three times: labels cycle Minimal → Standard → Detailed and a "Labels: …" tip confirms each step.
3. Open **Labels**, switch **Center (x, y)** on, and compare a label's centre with the ROI header for a box you know; zoom in and check that labels and centre markers keep their screen size.
4. Turn **Show detections** off: boxes, labels and markers disappear while the camera headers still count objects.
5. Restart the client: the last label options are restored.

## Status limits (OK / Watch / High)

The limits that colour the KPI cards and shade the charts are configurable per workstation. Open the **Status limits** window from **Limits** in the chart header, or by clicking any KPI status badge (● OK / ▲ Watch / ✕ High). It is modal and dims the window; video and measurements keep updating behind it.

| Metric | Applies to | Default OK below | Default Watch below |
|---|---|---|---|
| Frame delay | Average delay, delay P95 and the frame-delay chart | 150 ms | 300 ms |
| Camera skew \|R − L\| | Smoothed skew and the skew chart (absolute value) | 5 ms | 16 ms |

Readings at or above the Watch limit show **High**. Watch always stays above OK: raising OK past Watch pushes Watch up and vice versa, and values commit on Enter, focus change or the arrow keys rather than while typing. Each metric shows a scale bar with the current reading marked and a line such as `Now: average 125 ms → OK · P95 138 ms → OK`, so the effect is visible before saving. **Save** stores the limits locally (`QSettings`, like the image adjustments and labels), **Cancel**/**Esc** discards, and **Restore defaults** returns to the provisional values in `client/theme.py`. The defaults remain provisional, not validated competition limits.

## Detections tab (single-camera view)

When one camera is expanded (`2`, `3` or **Expand view**), the analysis card gains a **Detections** tab listing that camera's latest YOLO detections: class (with its colour), class ID, confidence, centre x/y, width and height, all in native camera pixels with the origin at the top-left (the same space as the ROI). Rows are sorted left to right by centre x; click a header to sort by another column. **Copy** or **Ctrl+C** copies the selected rows, or all rows, as tab-separated text with a header line, ready to paste into a spreadsheet. **P** (or the pause button) freezes both the table and the chart so values on a moving belt can be read. The tab disappears when returning to both cameras; while it is open the dashboard is slightly taller so more rows fit. If the stream stalls the table is cleared rather than showing stale positions.

### Verify

1. `python client/stereo_vision_client_qt.py --self-test` (checks OK/Watch consistency, defaults, settings round-trip, chart bands, and the Detections tab's visibility, filtering, copying and pausing).
2. Click the **Average delay** badge, set OK to 100 ms, watch the preview line change, then **Save**: the badge, digits, chart bands and legend update at once. Restart the client and confirm the limits persist; **Restore defaults** brings back 150/300 ms.
3. Press `2`: the **Detections** tab appears. Open it, compare a row's centre with the label on the video (Labels → Center), press `P`, select rows and press Ctrl+C, then paste into a spreadsheet.
4. Press `1`: the tab disappears and the card returns to the chart.

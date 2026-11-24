# Detection output: JSON over UDP

The Qt client can send what YOLO detects on both cameras to another program, such as a student's robot backend that tracks objects on the conveyor, measures their speed and commands a robot to pick them up. It sends one small JSON message per processed stereo frame, over UDP, as soon as YOLO finishes that frame.

```
camera server ──video──► student PC: Stereo Vision Studio ──UDP JSON──► student backend ──► robot
                          (decode, level, YOLO)              127.0.0.1:5600     (track, speed, pick)
```

Why UDP:
- The client never waits for the backend. The backend can start, stop or crash without affecting the video or detection.
- A lost message is replaced by the next one about 0.1–0.2 s later, which is what a real-time loop wants anyway.
- Every language can receive it with its standard library, with nothing to install.

## Turning it on

In the client, click the **Output** pill in the app bar, switch **Publish over UDP** on, and set the destination. The default destination is `127.0.0.1:5600`: the backend on the same PC, port 5600. The client remembers the switch and the destination on this PC.

Or start the client with the option. It applies to that run only:

```powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10 --publish 127.0.0.1:5600
```

To send to a backend on another PC, give that PC's IPv4 address, e.g. `192.168.0.21:5600`. That PC's firewall must allow inbound UDP on the port.

### Seeing that it works

- The **Output** pill shows the send rate, e.g. `5.5 msg/s`. Its status mark ripples each time a message leaves.
  - **No listener** (amber): nothing is listening on that port of this PC, because Windows answers "port unreachable". Start the backend or check the port.
  - **Status only** (blue): there are no detections to send (detection off, model loading or no video), so a status message goes out once a second instead.
  - **Off** (grey): publishing is switched off.
- The **Output panel**, opened by clicking the pill, shows the counters and the **latest message** as formatted JSON. **Pause** holds it so you can read it. **Copy** puts it on the clipboard as an example for your code.
- For a message sent to another PC, the client can't tell whether it arrived. Run the example receiver there to check.

## Example receiver

[`tools/receive_detections.py`](../tools/receive_detections.py) is a starting point for a backend. It's one file, Python 3.8+, standard library only:

```powershell
python tools/receive_detections.py                 # listen on 127.0.0.1:5600
python tools/receive_detections.py --host 0.0.0.0  # also accept messages from other PCs
python tools/receive_detections.py --raw           # print every message as JSON
```

It prints each frame's objects per camera with a speed in pixels per second, from a simple nearest-object match between consecutive frames:

```
15:27:07 seq 177504 566 ms after capture
   left: Sugaey 88% @(231,191) v=(+0,+0) px/s; Nabtat-Ali 37% @(168,367) v=(+5,-2) px/s
   right: -
```

## Message format (version 1)

All times are **milliseconds since 1970 (UTC) in the camera server's clock**. The client keeps its offset to the server measured (the Clock pill) and converts every time it sends. So times from several clients, and the capture time written into each frame by the server, are on one timebase.

### `detections`: one per processed frame

```json
{
  "v": 1,
  "type": "detections",
  "source": "T-100-08",
  "seq": 177504,
  "stamp_ok": true,
  "capture_utc_ms": { "left": 1795519627204.0, "right": 1795519627202.999 },
  "received_utc_ms": 1795519627437.0,
  "detected_utc_ms": 1795519627769.7,
  "clock_synced": true,
  "inference_ms": 184.3,
  "model": "firstry.pt",
  "cameras": {
    "left": {
      "size": [640, 480],
      "level_deg": 0.0,
      "roi": null,
      "objects": [
        { "class_id": 3, "label": "Sugaey", "conf": 0.88,
          "box": [184.2, 138.0, 278.9, 245.1], "center": [231.6, 191.6] }
      ]
    },
    "right": { "size": [640, 480], "level_deg": 0.0, "roi": null, "objects": [] }
  }
}
```

| Field | Meaning |
|---|---|
| `v` | Format version, `1`. Check it, and ignore messages with a version your code doesn't know. |
| `type` | `"detections"` or `"status"` |
| `source` | The sending PC's name |
| `seq` | The server's frame sequence number. A jump of more than 1 means frames were skipped: YOLO always takes the newest frame, so a slow model skips some. `null` when `stamp_ok` is false. |
| `stamp_ok` | `false` when the frame's timestamp strip couldn't be read (rare). Then `seq` and `capture_utc_ms` are `null`. Don't use that frame for speed. |
| `capture_utc_ms.left` / `.right` | When each camera captured this frame. **Use these to compute speed**, the left time for left-camera objects and the right time for right-camera objects. The two differ by the camera skew, typically a few to tens of ms. |
| `received_utc_ms` | When the client received the frame from the network |
| `detected_utc_ms` | When YOLO finished and the message was built. `detected_utc_ms − capture_utc_ms.left` is how old the data is when it's sent (about 0.3–0.6 s here). The robot must aim where the object will be, not where it was. |
| `clock_synced` | `false` until the client has measured its clock offset. Before that, times are this PC's own clock. |
| `inference_ms` | YOLO time for this frame (both cameras together) |
| `model` | The model that produced the detections |
| `cameras.left` / `.right` | One block per camera, always both present |
| `size` | Image width and height in pixels |
| `level_deg` | The rotation applied with the client's **Level** tool. Coordinates are in the levelled image (positive = turned counter-clockwise, about the image centre). |
| `roi` | `[x1, y1, x2, y2]` of the region YOLO looked at, or `null` for the whole image |
| `objects` | Detections, most confident first, at most 100 per camera (`truncated` then gives the real count) |
| `class_id`, `label` | The model's class number and name |
| `conf` | Confidence, 0–1 |
| `box` | `[x1, y1, x2, y2]` in pixels, origin top-left, y down |
| `center` | Centre of the box `[x, y]` |

A frame where nothing is detected still produces a message with empty `objects`. So "no objects" is different from "no messages".

### `status`: when there is nothing to detect

Sent once a second while no `detections` message has gone out for a second:

```json
{ "v": 1, "type": "status", "source": "T-100-08", "sent_utc_ms": 1795519616512.4,
  "clock_synced": true, "detection": "off", "stream_live": true, "model": null }
```

`detection` is `off` (no model chosen), `loading`, `error` or `running`. `stream_live` is `false` when video has stopped arriving. If neither kind of message arrives for a couple of seconds, the client is closed, publishing is off, or the network or port is wrong.

## Things to know when building the backend

- **No object IDs.** YOLO reports "a Galaxy at (x, y)" in each frame, not "the same Galaxy as before". Match objects between frames yourself. On a conveyor, the nearest object of the same class, moving in the belt direction, works well. Smooth speeds over several frames.
- **Pixels, not millimetres.** Speeds are in px/s until the cameras are calibrated. For a flat belt, measuring four points of known position on the belt plane gives an image-to-belt conversion (a homography).
- **Two cameras, two views.** Each camera reports its own detections in its own pixels. Using both together (for example for height or position from stereo) needs the stereo calibration that is still to be done.
- **Message size.** Typically 0.5–3 kB, well below the 64 kB UDP limit. Read with a 64 kB buffer.
- **Rate.** At most one message per processed frame: up to the video rate (~10 fps) with a fast model, fewer with a slow one (about 5–6 per second with the 131 MB model on CPU).

## Tested

- 7 unit tests in `tests/test_detection_output.py`:
  - Destination parsing.
  - The message fields, times converted to the server clock, objects sorted and capped.
  - Frames with an unreadable timestamp.
  - The publisher delivering, counting and switching off, and reporting "no listener" without counting it as an error.
  - The example receiver printing objects and a 500 px/s speed from two messages 100 ms apart.
- Live on the camera PC with the real stream and the library's `firstry.pt` model on CPU:
  - 5.5 messages per second reached `receive_detections.py` with no send errors, and the Output panel showed them.
  - Stopping the receiver turned the pill to **No listener** within 2 s.
- Not yet tested: a backend on another PC, and a moving belt.

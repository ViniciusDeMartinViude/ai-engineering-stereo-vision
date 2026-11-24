# YOLO model library

The camera server holds the trained YOLO models, and every client downloads them from it. You publish a model once on the camera PC, and each student PC picks it from a list in the client. The client downloads the model in the background, checks it and switches detection to it without restarting. Each PC keeps its own copy, so a model is downloaded once and still works when the server is unreachable.

```
training PC / laptop                camera PC (server)                     student PC (client)
  upload_model.py / .cmd  ── PUT ──►  models/            ── HTTP, TCP 8765 ──►  %LOCALAPPDATA%\StereoVisionStudio\models\
  (token, over Wi-Fi)                   index.json          speed-capped          <sha256>\belt-v3.pt  (verified copy)
                                        belt-v3.pt                              Model button > pick > download > detect
```

- **Uploads need a token:** clients can list and download; publishing needs the upload token you create on the camera PC. Model files are executable code when loaded (PyTorch pickles), so only people you give the token to can publish. Without a token file, uploads are off. Nothing can be deleted over the network.
- **Checked:** every download is verified against the SHA-256 the server lists and is discarded on any mismatch. This catches corrupted or interrupted transfers. It does not authenticate the server, which is trusted on this LAN.
- **Speed-capped:** all downloads together are limited to 30 Mbit/s by default (`--library-rate-mbps`). The camera PC's wired link currently runs at 100 Mbps, and an uncapped download would saturate it and drop video frames for every client.
- **Formats:** `.pt`, `.onnx` and `.torchscript`. TensorRT `.engine` files are refused, because they only run on the GPU model and TensorRT version they were built with.

## On the camera PC: publish models

```powershell
conda activate C:\conda-envs\gst-stereo-qt312
python tools/model_library.py add runs\detect\train\weights\best.pt --name belt-v3 --description "Dates on the belt, Nov 24"
python tools/model_library.py list
python tools/model_library.py remove belt-v2
```

- Adding a model under a name that already exists replaces it. Clients see the new file (new SHA-256) and download it again when chosen.
- Files copied straight into `models/` are also listed, under their file name. The Models tab's **Open folder** button opens it.
- The server picks up changes while it runs; no restart is needed.
- Class names are read from the model when Ultralytics is installed on the camera PC (`python -m pip install ultralytics`). Otherwise pass `--classes "Ajwa,Galaxy,Sokari,Medjool"`.
- `models/` is git-ignored: trained weights are never committed.

The server starts the library automatically:

| Server option | Default | Meaning |
|---|---|---|
| `--model-library DIR` | `models/` | Library folder |
| `--library-host IP` | `0.0.0.0` | Listen on every network of this PC, or only one address |
| `--library-port N` | `8765` | TCP port |
| `--library-rate-mbps N` | `30` | Total download speed cap; `0` removes it |
| `--no-library` | | Do not serve models |
| `--upload-token-file FILE` | `config/library-upload.token` | Uploads are accepted only with this token; no file means uploads are off |
| `--upload-on IP` | any | Accept uploads only on these local addresses (comma-separated), e.g. the Wi‑Fi IP |
| `--upload-max-mb N` | `512` | Largest model file accepted |

In the server console, the **Library** pill shows the model count, or *Sending N* while clients download. The **Models** tab (`M`) lists each model's classes, size, date and download count, the addresses clients should use, and whether uploads are on. Every upload, and every refused attempt, appears in the event log (Stream tab).

## From other computers: upload over Wi‑Fi

Use this to publish straight from the PC where you train, instead of copying files to the camera PC first.

**1. On the camera PC, once:** create the upload token and start the server so it accepts uploads on its Wi‑Fi address only. That way an upload never uses the 100 Mbps video link.

```powershell
python tools/model_library.py token
python server/stereo_vision_server_multiclient.py --upload-on 192.168.0.102
```

The `token` command prints the token and stores it in `config/library-upload.token`, which is git-ignored. `token --new` replaces it, and old copies stop working after the server restarts. Treat the token like a password: anyone who has it can publish models that every client may load.

**2. On each computer that uploads:** copy the scripts from `tools/` (no repository or conda needed), then set the address and token once:

```powershell
setx STEREO_LIBRARY_URL http://192.168.0.102:8765
setx STEREO_LIBRARY_TOKEN <the token>
setx STEREO_LIBRARY_VIA <this computer's Wi-Fi IP>
```

Open a new terminal after `setx`. The client reads the same `STEREO_LIBRARY_URL` and `STEREO_LIBRARY_VIA`, so on a student PC these settings serve both uploading and downloading. `STEREO_LIBRARY_VIA` is only needed when that computer has Wi‑Fi and Ethernet on the same `192.168.0.x` subnet (like the student PCs). A laptop on Wi‑Fi only can leave it out.

**3. Upload**, with whichever script fits that computer:

| Script | Needs | Upload | List |
|---|---|---|---|
| `upload_model.py` | Python 3.8+ (standard library only) | `python upload_model.py best.pt --name belt-v4 --description "Nov 24"` | `python upload_model.py --list` |
| `upload_model.cmd` + `upload_model.ps1` | Nothing: Windows PowerShell 5.1 | `upload_model.cmd best.pt -Name belt-v4 -Description "Nov 24"` | `upload_model.cmd -List` |

Every setting can also be given on the command line: `--server`/`-Server`, `--token`/`-Token` (or `--token-file`), `--via`/`-Via`, plus `--classes`/`-Classes` and `--imgsz`/`-Imgsz`. With Python, class names are read from the model automatically when Ultralytics is installed on that computer (as on a training PC). Otherwise pass them, or they show as unknown.

What the scripts do:
1. Ask the server whether this token may upload, before sending anything.
2. Send the file with a progress bar, and its SHA-256 so the server can verify it.
3. Check that the library lists the new version.

Uploading an existing name replaces that model, and students see the new version next time they open the Model list. The `.cmd` launcher runs the PowerShell script with `-ExecutionPolicy Bypass` for that one run, so Windows' script policy on the computer doesn't need to change.

Typical refusals and what they mean:

| Message | Fix |
|---|---|
| `Uploads are off on this server` | Run `python tools/model_library.py token` on the camera PC and restart the server |
| `Wrong or missing upload token` | Copy the current token (`token` prints it) |
| `Uploads are only accepted on 192.168.0.102` | You used the wired address; use the Wi‑Fi one, plus `--via` |
| `Cannot reach the library ... timed out` | Firewall on the camera PC (TCP 8765, see below), different Wi‑Fi, or missing `--via` |
| `not an address of this computer` | `--via` must be this computer's own Wi‑Fi IP (`ipconfig`) |
| `'CLIENT_WIFI_IP' is not an IPv4 address` | A placeholder or name was left in `--via` or `STEREO_LIBRARY_VIA`; put the real IP there |
| `.engine is not accepted` | Upload the `.pt` (or `.onnx`); TensorRT engines are tied to one GPU |

The token travels unencrypted over HTTP. That's fine on your own classroom Wi‑Fi, but don't use it across networks you don't control.

## On each student PC: choose a model

Set the library address once per PC. Use the camera PC's Wi‑Fi address, and this PC's own Wi‑Fi IP from `ipconfig` (the example below uses `192.168.0.12`):

```powershell
setx STEREO_LIBRARY_URL http://192.168.0.102:8765
```
```powershell
setx STEREO_LIBRARY_VIA 192.168.0.12
```

Then, in a new terminal, start the client as usual, with this PC's Ethernet IP:

```powershell
python client/stereo_vision_client_qt.py --iface-ip 192.168.0.11 --time-server 192.168.0.10
```

The `--model-library` and `--library-via` options do the same for one run and override the variables. Names in capitals in these docs, such as `CLIENT_WIFI_IP`, are placeholders: the client refuses a `--library-via` that is not an IP address and says so, instead of failing later with `getaddrinfo failed`.

Click **Model** in the app bar (shortcut `M`). Each model shows its classes, size and date, and its state on this PC: *Active*, *Downloaded*, *↓ 5.2 MB* (not downloaded yet) or *Downloading NN %*. Clicking a model downloads it if needed and switches detection to it. ROIs carry over, and old boxes are cleared. **Turn detection off** stops YOLO.

| Client option | Meaning |
|---|---|
| `--model-library URL` | The camera server's library, `http://IP:8765`. Default: `STEREO_LIBRARY_URL` |
| `--library-via IP` | This PC's address on the network to download over (pins the adapter; see below). Default: `STEREO_LIBRARY_VIA` |
| `--yolo-model library:NAME` | Start with this library model (downloads it first if needed) |
| `--yolo-model PATH` | A local model file, as before |
| `--model-cache DIR` | Download folder (default `%LOCALAPPDATA%\StereoVisionStudio\models`) |

The client remembers the last library model per PC and loads it again at the next start. If the library can't be reached, the client uses its saved list and cached files, and the picker says so. To try the library from a PC without starting the client:

```powershell
python tools/model_library.py check http://SERVER_WIFI_IP:8765 --via CLIENT_WIFI_IP
```

`check` also reads `STEREO_LIBRARY_VIA` when `--via` is left out.

## Two networks today, VLANs later

The camera PC is currently on two networks that both use `192.168.0.x`:

| Adapter | Address | Carries |
|---|---|---|
| Ethernet (100 Mbps) | 192.168.0.10, no gateway | Video multicast and SNTP to the clients |
| Wi‑Fi | 192.168.0.102, gateway 192.168.0.1 | Router / internet |

**Now:** download models over Wi‑Fi so they never touch the video link. Point clients at the server's Wi‑Fi address, `--model-library http://192.168.0.102:8765`. Also pass `--library-via` with the client's **own** Wi‑Fi address.

`--library-via` is needed because both adapters share one subnet. Windows would otherwise send the request out of the Ethernet adapter, where nothing answers for `.102`, and the download would time out. Pinning the source address makes Windows use the Wi‑Fi adapter. DHCP addresses on Wi‑Fi can change, so check them with `ipconfig` if a client reports that the library is unreachable.

**After the VLAN switch:** put the video on its own VLAN and use the other VLAN (or a dedicated "models" VLAN) for downloads, each on its own subnet. Then `--library-via` is no longer needed. If models must share the video link, keep the speed cap: 30 Mbit/s leaves room for the ~3 Mbps stream on 100 Mbps, and you can raise it on gigabit.

## Firewall

On the camera PC, as Administrator, allow the library port:

```powershell
New-NetFirewallRule -DisplayName "Stereo Vision Model Library" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Private -RemoteAddress LocalSubnet
```

**Check the network profile first.** On this camera PC both adapters currently report the **Public** profile, and the rule above, like the SNTP rule in the README, applies to Private networks only. So other PCs are probably blocked from the library and from clock sync (UDP 9123) today. Either mark the two trusted classroom networks as Private:

```powershell
Set-NetConnectionProfile -InterfaceAlias "Ethernet" -NetworkCategory Private
```
```powershell
Set-NetConnectionProfile -InterfaceAlias "Wi-Fi" -NetworkCategory Private
```

or add `-Profile Private,Public` to the rule. With Public, anyone on the same Wi‑Fi subnet can list and download the models, so prefer Private on trusted networks.

## Tested

On the camera PC (2× MX Brio, RTX 5070, Python 3.12, Ultralytics 8.4 with CPU PyTorch). The client ran on the same PC, so cross-PC routing between two real student PCs is still to be checked.

- 8 unit tests in `tests/test_model_library.py`: indexing, replace and remove, refusing `.engine`, verified download, rejecting a corrupt file, cache reuse offline, speed cap, adapter pinning, unknown paths returning 404, parallel downloads.
- 7 unit tests in `tests/test_model_uploads.py`: uploads off without a token, wrong or missing token, upload and replace with metadata, corrupt / wrong-format / oversize files refused, `--upload-on` address restriction, token file, and the Python uploader script end to end. The whole suite passed 15 runs in a row.
- Live uploads with the server accepting them on its Wi‑Fi address only:
  - `upload_model.py --via 192.168.0.102` published a 5.2 MB model in 1.2 s, reading its classes locally.
  - The same upload to the wired address was refused, as were a wrong token and a non-model file.
  - `upload_model.cmd` (PowerShell 5.1) replaced the model and listed the library, and refused a wrong token and a wrong `-Via` address with clear messages.
  - The uploads were also sent from the camera PC itself, so uploading from a separate computer is still to be checked.
- A unit test checks that `--library-via` / `--via` placeholders and host names are refused with a clear message. On the camera PC, `STEREO_LIBRARY_URL` and `STEREO_LIBRARY_VIA` set to its Wi‑Fi address let `check` and the client list and download a model without any library options.
- Server console and client self-tests cover the Library pill, the Models tab and the model picker.
- Live run: published a trained `best.pt` with its four classes read from the model. Listed it with `check ... --via 192.168.0.102` and started a client with `--yolo-model library:...`. The client downloaded 5.2 MB over the Wi‑Fi address in 1.4 s (the 30 Mbit/s cap) and ran detection (about 25–40 ms per pair on CPU).
- A second model published while everything was running appeared in the picker. Clicking it downloaded and switched detection live.
- After a restart, the client reloaded the remembered model from its cache without downloading again. With the library unreachable, it still loaded from the cache and showed the error over the saved list.

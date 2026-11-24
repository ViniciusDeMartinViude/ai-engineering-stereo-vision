# YOLO model library

The camera server holds the trained YOLO models, and every client downloads them from it. You publish a model once on the camera PC, and each student PC picks it from a list in the client. The client downloads the model in the background, checks it and switches detection to it without restarting. Each PC keeps its own copy, so a model is downloaded once and still works when the server is unreachable.

```
camera PC (server)                                student PC (client)
  models/                                           %LOCALAPPDATA%\StereoVisionStudio\models\
    index.json        ── HTTP, TCP 8765 ──►            <sha256>\belt-v3.pt   (verified copy)
    belt-v3.pt           read-only, speed-capped    Model button ► pick ► download ► detect
```

- **Read-only:** clients can list and download, never upload or delete. Model files are executable code when loaded (PyTorch pickles), so only the operator of the camera PC publishes them.
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

In the server console, the **Library** pill shows the model count, or *Sending N* while clients download. The **Models** tab (`M`) lists each model's classes, size, date and download count, plus the addresses clients should use.

## On each student PC: choose a model

```powershell
python client/stereo_vision_client_qt.py --iface-ip CLIENT_ETHERNET_IP --time-server SERVER_ETHERNET_IP --model-library http://SERVER_WIFI_IP:8765 --library-via CLIENT_WIFI_IP
```

Click **Model** in the app bar (shortcut `M`). Each model shows its classes, size and date, and its state on this PC: *Active*, *Downloaded*, *↓ 5.2 MB* (not downloaded yet) or *Downloading NN %*. Clicking a model downloads it if needed and switches detection to it. ROIs carry over, and old boxes are cleared. **Turn detection off** stops YOLO.

| Client option | Meaning |
|---|---|
| `--model-library URL` | The camera server's library, `http://IP:8765` |
| `--library-via IP` | This PC's address on the network to download over (pins the adapter; see below) |
| `--yolo-model library:NAME` | Start with this library model (downloads it first if needed) |
| `--yolo-model PATH` | A local model file, as before |
| `--model-cache DIR` | Download folder (default `%LOCALAPPDATA%\StereoVisionStudio\models`) |

The client remembers the last library model per PC and loads it again at the next start. If the library can't be reached, the client uses its saved list and cached files, and the picker says so. To try the library from a PC without starting the client:

```powershell
python tools/model_library.py check http://SERVER_WIFI_IP:8765 --via CLIENT_WIFI_IP
```

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
- Server console and client self-tests cover the Library pill, the Models tab and the model picker.
- Live run: published a trained `best.pt` with its four classes read from the model. Listed it with `check ... --via 192.168.0.102` and started a client with `--yolo-model library:...`. The client downloaded 5.2 MB over the Wi‑Fi address in 1.4 s (the 30 Mbit/s cap) and ran detection (about 25–40 ms per pair on CPU).
- A second model published while everything was running appeared in the picker. Clicking it downloaded and switched detection live.
- After a restart, the client reloaded the remembered model from its cache without downloading again. With the library unreachable, it still loaded from the cache and showed the error over the saved list.

"""YOLO model library: the camera server holds trained models, clients download them.

Server side
    A folder (default ``models/`` in the repository, git-ignored) with the
    model files and an ``index.json`` manifest. ``tools/model_library.py add``
    publishes a model; files dropped into the folder are indexed automatically.
    ``ModelLibraryServer`` serves the folder over HTTP:

        GET /api/models            JSON index of the library
        GET /api/models/<sha256>   one model file, by content hash
        GET /api/upload            can this token upload here? (token required)
        PUT /api/models/<name>     publish a model (token required)

    Downloads are paced by one shared speed cap so a download can never starve
    the video stream on a shared link. Models are executable code when loaded,
    so uploads are OFF unless the operator creates an upload token
    (``tools/model_library.py token``); they can also be limited to one local
    address (e.g. the Wi-Fi adapter). Nothing can be deleted over HTTP.

Client side
    ``LibraryClient`` lists the library, downloads a model into a per-user
    cache (``%LOCALAPPDATA%/StereoVisionStudio/models/<sha256>/<file>``),
    verifies the SHA-256 and reuses the cached copy afterwards, also offline.
    ``source_ip`` pins downloads to one local interface (e.g. the Wi-Fi
    adapter), which matters when two networks share one subnet.

Standard library only, so the server needs no extra packages.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import http.client
import http.server
import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

FORMAT = 1
DEFAULT_PORT = 8765
DEFAULT_RATE_MBPS = 30.0
DEFAULT_UPLOAD_MAX_MB = 512
INDEX_NAME = 'index.json'
# Formats a client can load on any machine. TensorRT .engine files are tied to
# one GPU model and TensorRT version, so they are not shared.
SUPPORTED_SUFFIXES = ('.pt', '.onnx', '.torchscript')
CHUNK = 64 * 1024
_SHA_RE = re.compile(r'^[0-9a-f]{64}$')
_NAME_RE = re.compile(r'[^A-Za-z0-9._-]+')


class LibraryError(RuntimeError):
    """Library unreachable, model missing, or a download failed verification."""


@dataclass(frozen=True)
class ModelEntry:
    name: str
    file: str
    sha256: str
    size: int
    description: str = ''
    classes: tuple = ()
    task: str = ''
    imgsz: int | None = None
    added: str = ''

    @property
    def suffix(self):
        return Path(self.file).suffix.lower()

    def to_json(self):
        data = asdict(self)
        data['classes'] = list(self.classes)
        return data

    @classmethod
    def from_json(cls, data):
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in dict(data).items() if k in known}
        values['classes'] = tuple(str(c) for c in values.get('classes') or ())
        values['size'] = int(values.get('size') or 0)
        if values.get('imgsz') is not None:
            values['imgsz'] = int(values['imgsz'])
        if not _SHA_RE.match(str(values.get('sha256', ''))):
            raise ValueError(f'invalid sha256 for model {values.get("name")!r}')
        return cls(**values)


def clean_name(text):
    name = _NAME_RE.sub('-', str(text).strip()).strip('-.')
    if not name:
        raise ValueError('model name is empty')
    return name


def sha256_file(path, chunk=1024 * 1024):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        while True:
            block = handle.read(chunk)
            if not block:
                break
            digest.update(block)
    return digest.hexdigest()


def format_size(count):
    count = float(count or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'B' else f'{count:.1f} {unit}'
        count /= 1024.0
    return f'{count:.1f} GB'


def read_model_metadata(path):
    """Class names and task from the model itself, when Ultralytics is installed."""
    try:
        from ultralytics import YOLO
    except Exception:
        return {}
    try:
        model = YOLO(str(path))
        names = model.names
        if isinstance(names, dict):
            names = [names[k] for k in sorted(names)]
        return {'classes': tuple(str(n) for n in names), 'task': str(getattr(model, 'task', '') or '')}
    except Exception:
        return {}


def _now_text():
    return dt.datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')


class LibraryStore:
    """The library folder and its index.json manifest (server side)."""

    def __init__(self, root):
        self.root = Path(root)
        self.lock = threading.RLock()
        self._hash_cache = {}  # (file, size, mtime_ns) -> sha256
        self._entries = []
        self._stamp = None

    @property
    def index_path(self):
        return self.root / INDEX_NAME

    def _read_manifest(self):
        try:
            data = json.loads(self.index_path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []
        except (OSError, ValueError) as exc:
            raise LibraryError(f'cannot read {self.index_path}: {exc}') from exc
        entries = []
        for item in data.get('models', []):
            try:
                entries.append(ModelEntry.from_json(item))
            except (TypeError, ValueError):
                continue
        return entries

    def _write_manifest(self, entries):
        payload = {'format': FORMAT, 'models': [e.to_json() for e in entries]}
        temp = self.index_path.with_suffix('.json.tmp')
        temp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding='utf-8')
        os.replace(temp, self.index_path)

    def _hash(self, path):
        stat = path.stat()
        key = (path.name, stat.st_size, stat.st_mtime_ns)
        if key not in self._hash_cache:
            self._hash_cache[key] = sha256_file(path)
        return self._hash_cache[key], stat.st_size

    def _folder_stamp(self):
        try:
            return tuple(sorted((p.name, p.stat().st_size, p.stat().st_mtime_ns)
                                for p in self.root.iterdir() if p.is_file()))
        except FileNotFoundError:
            return ()

    def refresh(self):
        """Re-read the folder: drop entries whose file is gone, re-hash changed
        files and index model files that were dropped in without the tool."""
        with self.lock:
            self.root.mkdir(parents=True, exist_ok=True)
            stamp = self._folder_stamp()
            if stamp == self._stamp:
                return list(self._entries)
            entries = []
            changed = False
            listed = set()
            for entry in self._read_manifest():
                path = self.root / entry.file
                if not path.is_file() or entry.file in listed:
                    changed = True
                    continue
                sha, size = self._hash(path)
                if (sha, size) != (entry.sha256, entry.size):
                    entry = replace(entry, sha256=sha, size=size, added=_now_text())
                    changed = True
                entries.append(entry)
                listed.add(entry.file)
            names = {e.name.lower() for e in entries}
            for path in sorted(self.root.iterdir()):
                if (path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
                        and path.name not in listed):
                    sha, size = self._hash(path)
                    name = clean_name(path.stem)
                    base, n = name, 2
                    while name.lower() in names:
                        name, n = f'{base}-{n}', n + 1
                    entries.append(ModelEntry(name, path.name, sha, size, added=_now_text(),
                                              description='Added to the folder'))
                    names.add(name.lower())
                    changed = True
            if changed:
                self._write_manifest(entries)
            self._entries = entries
            self._stamp = self._folder_stamp()
            return list(entries)

    def entries(self):
        return self.refresh()

    def find(self, name):
        wanted = name.lower()
        for entry in self.refresh():
            if entry.name.lower() == wanted:
                return entry
        return None

    def find_by_sha(self, sha):
        with self.lock:
            for entry in self._entries:
                if entry.sha256 == sha:
                    return entry, self.root / entry.file
        return None, None

    def add(self, source, name=None, description='', classes=None, imgsz=None, task=None):
        """Copy a model into the library (replacing one with the same name)."""
        source = Path(source)
        if not source.is_file():
            raise LibraryError(f'{source} does not exist')
        suffix = source.suffix.lower()
        if suffix not in SUPPORTED_SUFFIXES:
            raise LibraryError(f'{suffix or "this file type"} is not shared; use one of '
                               + ', '.join(SUPPORTED_SUFFIXES)
                               + ' (TensorRT .engine files only work on the GPU they were built on)')
        name = clean_name(name or source.stem)
        meta = read_model_metadata(source) if classes is None or task is None else {}
        with self.lock:
            entries = [e for e in self.refresh() if e.name.lower() != name.lower()]
            old = self.find(name)
            target = self.root / f'{name}{suffix}'
            if old is not None and old.file != target.name:
                (self.root / old.file).unlink(missing_ok=True)
            if source.resolve() != target.resolve():
                shutil.copy2(source, target)
            sha, size = self._hash(target)
            entry = ModelEntry(
                name=name, file=target.name, sha256=sha, size=size,
                description=description or (old.description if old else ''),
                classes=tuple(classes) if classes is not None else tuple(meta.get('classes', ())),
                task=task if task is not None else meta.get('task', ''),
                imgsz=int(imgsz) if imgsz else None, added=_now_text())
            entries = [e for e in entries if e.file != target.name] + [entry]
            self._write_manifest(entries)
            self._stamp = None
            self.refresh()
            return entry

    def remove(self, name):
        with self.lock:
            entry = self.find(name)
            if entry is None:
                raise LibraryError(f'no model named {name!r}')
            (self.root / entry.file).unlink(missing_ok=True)
            self._write_manifest([e for e in self._entries if e.name != entry.name])
            self._stamp = None
            self.refresh()
            return entry


def new_upload_token():
    return secrets.token_urlsafe(24)


def load_upload_token(path):
    """The upload token stored in ``path``, or None when the file is missing or empty."""
    try:
        token = Path(path).read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return token or None


def save_upload_token(path, token):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + '\n', encoding='utf-8')
    return path


class _Pacer:
    """One speed cap shared by every download (a token bucket with no burst)."""

    def __init__(self, rate_mbps):
        self.bytes_per_s = rate_mbps * 1e6 / 8 if rate_mbps and rate_mbps > 0 else None
        self.lock = threading.Lock()
        self.next_time = 0.0

    def wait(self, count):
        if self.bytes_per_s is None:
            return
        with self.lock:
            now = time.monotonic()
            start = max(now, self.next_time)
            self.next_time = start + count / self.bytes_per_s
        delay = start - now
        if delay > 0:
            time.sleep(delay)


def local_ipv4_addresses():
    addresses = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(info[4][0])
    except OSError:
        pass
    return sorted(a for a in addresses if not a.startswith(('127.', '169.254.')))


class _QuietHTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        # A client closing its socket early is normal; anything else is logged once.
        import sys
        error = sys.exc_info()[1]
        if not isinstance(error, (ConnectionError, TimeoutError)):
            print(f'Model library: request from {client_address[0]} failed: {error!r}', flush=True)


class ModelLibraryServer:
    """HTTP service for the library folder (runs on the camera PC).

    Downloads are open to the LAN; uploads need ``upload_token`` and, when
    ``upload_on`` lists local addresses, must arrive on one of them.
    """

    def __init__(self, store, host='0.0.0.0', port=DEFAULT_PORT, rate_mbps=DEFAULT_RATE_MBPS,
                 log=print, upload_token=None, upload_on=None,
                 upload_max_bytes=DEFAULT_UPLOAD_MAX_MB * 1024 * 1024):
        self.store = store
        self.upload_token = upload_token or None
        self.upload_on = [ip for ip in (upload_on or []) if ip] or None
        self.upload_max_bytes = int(upload_max_bytes)
        self.uploads = 0
        self.rejected_uploads = 0
        self.host, self.port = host, int(port)
        self.rate_mbps = float(rate_mbps or 0)
        self.log = log
        self.pacer = _Pacer(self.rate_mbps)
        self.lock = threading.Lock()
        self.httpd = None
        self.thread = None
        self.error = None
        self.requests = 0
        self.downloads = 0
        self.failed = 0
        self.active = {}  # id -> (client ip, model name, bytes sent, size)
        self.bytes_sent = 0
        self.per_model = {}  # sha256 -> completed downloads
        self.last_client = None

    # ---- lifecycle -------------------------------------------------------------
    def start(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            server_version = 'StereoVisionModelLibrary/1'
            protocol_version = 'HTTP/1.1'

            def log_message(self, *args):  # quiet; the library keeps its own log
                pass

            def do_GET(self):
                server._handle(self)

            def do_PUT(self):
                server._handle_upload(self)

        try:
            self.store.refresh()
            httpd = _QuietHTTPServer((self.host, self.port), Handler)
        except OSError as exc:
            self.error = str(exc)
            raise
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, kwargs={'poll_interval': 0.5},
                                       name='model-library', daemon=True)
        self.thread.start()
        cap = f'{self.rate_mbps:g} Mbit/s cap' if self.rate_mbps > 0 else 'no speed cap'
        self.log(f'Model library: {len(self.store.entries())} model(s) in {self.store.root}, '
                 f'serving on TCP {self.host}:{self.port} ({cap}); '
                 + ', '.join(self.urls()))
        self.log('Model library uploads: ' + self.upload_summary())

    def upload_summary(self):
        if not self.upload_token:
            return 'off (no upload token; create one with tools/model_library.py token)'
        where = ('only on ' + ', '.join(self.upload_on)) if self.upload_on else 'on every address'
        return f'on, token required, accepted {where}, up to {self.upload_max_bytes // (1024 * 1024)} MB'


    def stop(self):
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None

    def urls(self):
        hosts = local_ipv4_addresses() if self.host in ('0.0.0.0', '') else [self.host]
        return [f'http://{h}:{self.port}' for h in hosts]

    # ---- requests ----------------------------------------------------------------
    def _send_json(self, handler, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        handler.send_response(status)
        handler.send_header('Content-Type', 'application/json; charset=utf-8')
        handler.send_header('Content-Length', str(len(body)))
        handler.send_header('Cache-Control', 'no-store')
        handler.send_header('Connection', 'close')
        handler.close_connection = True
        handler.end_headers()
        handler.wfile.write(body)

    def _handle(self, handler):
        with self.lock:
            self.requests += 1
        path = urllib.parse.urlsplit(handler.path).path.rstrip('/')
        client = handler.client_address[0]
        try:
            if path == '/api/upload':
                denied = self._upload_denied(handler)
                if denied:
                    with self.lock:
                        self.rejected_uploads += 1
                    self.log(f'Model library: upload check from {client} refused ({denied[0]}): {denied[1]}')
                    self._send_json(handler, denied[0], {'error': denied[1]})
                else:
                    self._send_json(handler, 200, {
                        'enabled': True, 'max_bytes': self.upload_max_bytes,
                        'formats': list(SUPPORTED_SUFFIXES)})
                return
            if path in ('', '/api', '/api/models'):
                entries = self.store.entries()
                self._send_json(handler, 200, {
                    'format': FORMAT, 'server': socket.gethostname(),
                    'models': [e.to_json() for e in entries]})
                return
            match = re.fullmatch(r'/api/models/([0-9a-f]{64})', path)
            if match:
                self.store.refresh()
                entry, file_path = self.store.find_by_sha(match.group(1))
                if entry is None or not file_path.is_file():
                    self._send_json(handler, 404, {'error': 'model not in the library'})
                    return
                self._send_file(handler, entry, file_path, client)
                return
            self._send_json(handler, 404, {'error': 'unknown path'})
        except (ConnectionError, OSError) as exc:
            with self.lock:
                self.failed += 1
            self.log(f'Model library: transfer to {client} interrupted ({exc})')

    def _upload_denied(self, handler):
        """(HTTP status, reason) when this request may not upload, else None."""
        if not self.upload_token:
            return 403, ('Uploads are off on this server. On the camera PC run '
                         '"python tools/model_library.py token" and restart the server.')
        local = handler.connection.getsockname()[0]
        if self.upload_on and local not in self.upload_on:
            return 403, (f'Uploads are only accepted on {", ".join(self.upload_on)}; this request '
                         f'reached the server on {local}. Use that address (and --via).')
        supplied = handler.headers.get('Authorization', '')
        supplied = supplied[7:].strip() if supplied.startswith('Bearer ') else ''
        if not hmac.compare_digest(supplied.encode('utf-8'), self.upload_token.encode('utf-8')):
            return 401, 'Wrong or missing upload token.'
        return None

    DISCARD_LIMIT = 16 * 1024 * 1024

    def _discard_body(self, handler):
        """Read and drop a refused upload's body so the sender gets the answer.

        Closing a socket with unread data makes Windows reset the connection,
        and the sender would see 'connection aborted' instead of the reason.
        Bodies over 16 MB are not read; the uploaders check before sending.
        """
        try:
            remaining = int(handler.headers.get('Content-Length', '0'))
        except ValueError:
            return
        if not 0 < remaining <= self.DISCARD_LIMIT:
            return
        try:
            handler.connection.settimeout(10)
            while remaining:
                block = handler.rfile.read(min(CHUNK, remaining))
                if not block:
                    break
                remaining -= len(block)
        except OSError:
            pass

    def _handle_upload(self, handler):
        with self.lock:
            self.requests += 1
        client = handler.client_address[0]
        unquote = urllib.parse.unquote

        def refuse(status, message, body_unread=True):
            with self.lock:
                self.rejected_uploads += 1
            self.log(f'Model library: upload from {client} refused ({status}): {message}')
            if body_unread:
                self._discard_body(handler)
            self._send_json(handler, status, {'error': message})

        try:
            match = re.fullmatch(r'/api/models/([^/]+)', urllib.parse.urlsplit(handler.path).path)
            if not match:
                self._send_json(handler, 404, {'error': 'upload to /api/models/<name>'})
                return
            denied = self._upload_denied(handler)
            if denied:
                refuse(*denied)
                return
            try:
                name = clean_name(unquote(match.group(1)))
            except ValueError as exc:
                refuse(400, str(exc))
                return
            filename = unquote(handler.headers.get('X-Model-File', ''))
            suffix = Path(filename).suffix.lower()
            if suffix not in SUPPORTED_SUFFIXES:
                refuse(415, f'{suffix or "this file type"} is not accepted; use '
                       + ', '.join(SUPPORTED_SUFFIXES))
                return
            expected = handler.headers.get('X-Model-SHA256', '').lower()
            if not _SHA_RE.match(expected):
                refuse(400, 'X-Model-SHA256 header missing or invalid')
                return
            try:
                length = int(handler.headers.get('Content-Length', ''))
            except ValueError:
                refuse(411, 'Content-Length required')
                return
            if length <= 0 or length > self.upload_max_bytes:
                refuse(413, f'{format_size(length)} is over the '
                       f'{format_size(self.upload_max_bytes)} upload limit')
                return
            incoming = self.store.root / '.incoming'
            incoming.mkdir(parents=True, exist_ok=True)
            temp = incoming / f'{secrets.token_hex(8)}{suffix}'
            digest = hashlib.sha256()
            started = time.monotonic()
            try:
                remaining = length
                with open(temp, 'wb') as out:
                    while remaining:
                        block = handler.rfile.read(min(CHUNK, remaining))
                        if not block:
                            raise ConnectionError('upload ended early')
                        out.write(block)
                        digest.update(block)
                        remaining -= len(block)
                if digest.hexdigest() != expected:
                    refuse(422, 'The file arrived corrupted (SHA-256 mismatch); nothing was changed.',
                           body_unread=False)
                    return
                classes = unquote(handler.headers.get('X-Model-Classes', ''))
                classes = [c.strip() for c in classes.split(',') if c.strip()] or None
                imgsz = handler.headers.get('X-Model-Imgsz', '')
                replaced = self.store.find(name) is not None
                entry = self.store.add(temp, name, unquote(handler.headers.get('X-Model-Description', '')),
                                       classes=classes, imgsz=int(imgsz) if imgsz.isdigit() else None)
            finally:
                temp.unlink(missing_ok=True)
            with self.lock:
                self.uploads += 1
            seconds = time.monotonic() - started
            self.log(f'Model library: received {entry.name} ({format_size(entry.size)}) from {client} '
                     f'in {seconds:.1f} s' + (' (replaced the previous version)' if replaced else ''))
            self._send_json(handler, 201, {'model': entry.to_json(), 'replaced': replaced})
        except LibraryError as exc:
            refuse(400, str(exc))
        except (ConnectionError, OSError) as exc:
            with self.lock:
                self.failed += 1
            self.log(f'Model library: upload from {client} interrupted ({exc})')

    def _send_file(self, handler, entry, path, client):
        size = path.stat().st_size
        handler.send_response(200)
        handler.send_header('Content-Type', 'application/octet-stream')
        handler.send_header('Content-Length', str(size))
        handler.send_header('X-Model-SHA256', entry.sha256)
        handler.send_header('Content-Disposition', f'attachment; filename="{entry.file}"')
        handler.send_header('Connection', 'close')
        handler.close_connection = True
        handler.end_headers()
        key = object()
        started = time.monotonic()
        with self.lock:
            self.active[key] = [client, entry.name, 0, size]
            self.last_client = client
        try:
            with open(path, 'rb') as handle:
                while True:
                    block = handle.read(CHUNK)
                    if not block:
                        break
                    self.pacer.wait(len(block))
                    handler.wfile.write(block)
                    with self.lock:
                        self.active[key][2] += len(block)
                        self.bytes_sent += len(block)
            with self.lock:
                self.downloads += 1
                self.per_model[entry.sha256] = self.per_model.get(entry.sha256, 0) + 1
            seconds = time.monotonic() - started
            self.log(f'Model library: sent {entry.name} ({format_size(size)}) to {client} '
                     f'in {seconds:.1f} s')
        finally:
            with self.lock:
                self.active.pop(key, None)

    def status(self):
        try:
            entries = self.store.entries()
            store_error = None
        except LibraryError as exc:
            entries, store_error = [], str(exc)
        with self.lock:
            return {
                'running': self.httpd is not None, 'error': self.error or store_error,
                'folder': str(self.store.root), 'host': self.host, 'port': self.port,
                'urls': self.urls(), 'rate_mbps': self.rate_mbps,
                'models': entries, 'per_model': dict(self.per_model),
                'requests': self.requests, 'downloads': self.downloads, 'failed': self.failed,
                'active': [tuple(v) for v in self.active.values()],
                'bytes_sent': self.bytes_sent, 'last_client': self.last_client,
                'uploads_enabled': bool(self.upload_token), 'upload_on': list(self.upload_on or []),
                'uploads': self.uploads, 'rejected_uploads': self.rejected_uploads,
                'upload_summary': self.upload_summary(),
            }


def check_source_ip(value):
    """The local IPv4 address to connect from, or None. Rejects names and placeholders.

    A host name here would be looked up by DNS and fail with a cryptic
    "getaddrinfo failed"; say what is expected instead.
    """
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return str(ipaddress.IPv4Address(text))
    except ValueError:
        raise ValueError(f"{text!r} is not an IPv4 address. Give this PC's own address on the network "
                         "to download over, e.g. its Wi-Fi IP from ipconfig (like 192.168.0.12)") from None


def default_cache_dir():
    base = os.environ.get('LOCALAPPDATA') or os.path.join(Path.home(), '.cache')
    return Path(base) / 'StereoVisionStudio' / 'models'


class LibraryClient:
    """List, download, verify and cache models from a ModelLibraryServer."""

    def __init__(self, base_url, cache_dir=None, source_ip=None, timeout=6.0):
        url = base_url if '://' in base_url else f'http://{base_url}'
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'http' or not parts.hostname:
            raise ValueError(f'model library must be an http://HOST:PORT address, not {base_url!r}')
        self.host = parts.hostname
        self.port = parts.port or DEFAULT_PORT
        self.base_url = f'http://{self.host}:{self.port}'
        self.source_ip = check_source_ip(source_ip)
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()

    def describe(self):
        return self.base_url + (f' via {self.source_ip}' if self.source_ip else '')

    def _connect(self, timeout=None):
        source = (self.source_ip, 0) if self.source_ip else None
        return http.client.HTTPConnection(self.host, self.port, timeout=timeout or self.timeout,
                                          source_address=source)

    def _hint(self, exc):
        text = f'{type(exc).__name__}: {exc}'
        if isinstance(exc, OSError) and getattr(exc, 'errno', None) in (10049, 99):
            return text + f' (is {self.source_ip} an address of this PC?)'
        return text

    def fetch_index(self):
        """Library listing from the server; also saved for offline use."""
        connection = self._connect()
        try:
            connection.request('GET', '/api/models')
            response = connection.getresponse()
            body = response.read()
            if response.status != 200:
                raise LibraryError(f'{self.base_url} answered HTTP {response.status}')
            data = json.loads(body.decode('utf-8'))
        except LibraryError:
            raise
        except (OSError, http.client.HTTPException, ValueError) as exc:
            raise LibraryError(f'Cannot reach the model library at {self.describe()}: '
                               f'{self._hint(exc)}') from exc
        finally:
            connection.close()
        entries = []
        for item in data.get('models', []):
            try:
                entries.append(ModelEntry.from_json(item))
            except (TypeError, ValueError):
                continue
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {'source': self.base_url, 'saved': _now_text(),
                       'models': [e.to_json() for e in entries]}
            (self.cache_dir / INDEX_NAME).write_text(json.dumps(payload, indent=2), encoding='utf-8')
        except OSError:
            pass
        return entries

    def cached_index(self):
        """The last listing saved by fetch_index (offline fallback), or []."""
        try:
            data = json.loads((self.cache_dir / INDEX_NAME).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return []
        entries = []
        for item in data.get('models', []):
            try:
                entries.append(ModelEntry.from_json(item))
            except (TypeError, ValueError):
                continue
        return entries

    def cached_path(self, entry):
        path = self.cache_dir / entry.sha256 / entry.file
        try:
            if path.is_file() and path.stat().st_size == entry.size:
                return path
        except OSError:
            pass
        return None

    def download(self, entry, progress=None, cancel=None):
        """Download (or reuse) one model; returns the verified local path."""
        cached = self.cached_path(entry)
        if cached is not None:
            if progress:
                progress(entry.size, entry.size)
            return cached
        folder = self.cache_dir / entry.sha256
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / entry.file
        partial = folder / (entry.file + '.part')
        digest = hashlib.sha256()
        done = 0
        connection = self._connect(timeout=max(self.timeout, 15.0))
        try:
            connection.request('GET', f'/api/models/{entry.sha256}')
            response = connection.getresponse()
            if response.status != 200:
                raise LibraryError(f'{entry.name}: the library answered HTTP {response.status}')
            total = int(response.getheader('Content-Length') or entry.size)
            with open(partial, 'wb') as handle:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise LibraryError('download cancelled')
                    block = response.read(CHUNK)
                    if not block:
                        break
                    handle.write(block)
                    digest.update(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
        except LibraryError:
            partial.unlink(missing_ok=True)
            raise
        except (OSError, http.client.HTTPException) as exc:
            partial.unlink(missing_ok=True)
            raise LibraryError(f'Downloading {entry.name} from {self.describe()} failed: '
                               f'{self._hint(exc)}') from exc
        finally:
            connection.close()
        if digest.hexdigest() != entry.sha256 or done != entry.size:
            partial.unlink(missing_ok=True)
            raise LibraryError(f'{entry.name}: download did not match the library checksum; '
                               'try again')
        os.replace(partial, target)
        return target

    def resolve(self, name):
        """(entry, from_cache_only) for a model name; uses the saved listing when offline."""
        try:
            entries, offline = self.fetch_index(), False
        except LibraryError:
            entries, offline = self.cached_index(), True
            if not entries:
                raise
        for entry in entries:
            if entry.name.lower() == name.lower():
                if offline and self.cached_path(entry) is None:
                    raise LibraryError(f'{name} is not downloaded yet and the library at '
                                       f'{self.describe()} is unreachable')
                return entry, offline
        raise LibraryError(f'No model named {name!r} in the library at {self.describe()}')


@dataclass
class DownloadJob:
    """Background resolve + download, polled by the GUI (no Qt in this module)."""
    client: LibraryClient
    name: str = ''
    entry: ModelEntry | None = None
    done: int = 0
    total: int = 0
    path: Path | None = None
    error: str | None = None
    finished: bool = False
    cancel: threading.Event = field(default_factory=threading.Event)

    def start(self):
        threading.Thread(target=self._run, name='model-download', daemon=True).start()
        return self

    def _progress(self, done, total):
        self.done, self.total = done, total

    def _run(self):
        try:
            if self.entry is None:
                self.entry, _ = self.client.resolve(self.name)
            self.name = self.entry.name
            self.total = self.entry.size
            self.path = self.client.download(self.entry, self._progress, self.cancel)
        except LibraryError as exc:
            self.error = str(exc)
        except Exception as exc:  # pragma: no cover - unexpected, but never kill the GUI
            self.error = f'{type(exc).__name__}: {exc}'
        finally:
            self.finished = True

    @property
    def fraction(self):
        return (self.done / self.total) if self.total else 0.0

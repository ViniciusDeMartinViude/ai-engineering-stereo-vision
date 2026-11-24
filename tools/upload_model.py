#!/usr/bin/env python3
"""Upload a trained YOLO model to the camera server's model library.

Run it on any computer (a training PC, a laptop) that can reach the camera PC
over Wi-Fi. One file, standard library only, Python 3.8+: copy it anywhere.

  python upload_model.py runs\\detect\\train\\weights\\best.pt --name belt-v4 --description "Nov 24, 120 epochs"
  python upload_model.py --list

Settings (options, or environment variables so you set them once):
  --server URL    STEREO_LIBRARY_URL    camera PC library, e.g. http://192.168.0.102:8765
  --token TOKEN   STEREO_LIBRARY_TOKEN  upload token from: python tools/model_library.py token
  --via IP        STEREO_LIBRARY_VIA    this computer's Wi-Fi IP (needed when Wi-Fi and
                                        Ethernet share one subnet)

  setx STEREO_LIBRARY_URL http://192.168.0.102:8765
  setx STEREO_LIBRARY_TOKEN <token>

Uploading a name that already exists replaces that model; clients see the
new version next time they open the Model list.
"""
import argparse
import hashlib
import http.client
import ipaddress
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

FORMATS = ('.pt', '.onnx', '.torchscript')
CHUNK = 256 * 1024


class UploadError(RuntimeError):
    pass


def human(count):
    count = float(count)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if count < 1024 or unit == 'GB':
            return f'{count:.0f} {unit}' if unit == 'B' else f'{count:.1f} {unit}'
        count /= 1024.0


def sha256_of(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def class_names(path):
    """Class names from the model when Ultralytics is installed here; else None."""
    try:
        from ultralytics import YOLO
        names = YOLO(str(path)).names
        if isinstance(names, dict):
            names = [names[k] for k in sorted(names)]
        return [str(n) for n in names]
    except Exception:
        return None


class Library:
    def __init__(self, url, token=None, via=None, timeout=15.0):
        url = url if '://' in url else 'http://' + url
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != 'http' or not parts.hostname:
            raise UploadError(f'--server must look like http://IP:8765, not {url!r}')
        self.host, self.port = parts.hostname, parts.port or 8765
        via = (via or '').strip() or None
        if via:
            try:
                via = str(ipaddress.IPv4Address(via))
            except ValueError:
                raise UploadError(f"--via {via!r} is not an IPv4 address. Give this computer's own Wi-Fi "
                                  'IP from ipconfig (like 192.168.0.12), or leave it out') from None
        self.token, self.via, self.timeout = token, via, timeout

    def describe(self):
        return f'http://{self.host}:{self.port}' + (f' via {self.via}' if self.via else '')

    def _connection(self):
        source = (self.via, 0) if self.via else None
        return http.client.HTTPConnection(self.host, self.port, timeout=self.timeout, source_address=source)

    def _reach_error(self, exc):
        hint = ''
        if getattr(exc, 'errno', None) in (10049, 99):
            hint = f' ({self.via} is not an address of this computer; check ipconfig)'
        elif isinstance(exc, (TimeoutError, ConnectionRefusedError)) or 'timed out' in str(exc):
            hint = (' (is the server running, TCP 8765 allowed in its firewall, and are you on '
                    'the same Wi-Fi? With Wi-Fi and Ethernet on one subnet, pass --via YOUR_WIFI_IP)')
        return UploadError(f'Cannot reach the library at {self.describe()}: {exc}{hint}')

    def request(self, method, path, headers=None, body=None, progress=None, length=None):
        connection = self._connection()
        try:
            connection.putrequest(method, path)
            for key, value in (headers or {}).items():
                connection.putheader(key, value)
            if self.token:
                connection.putheader('Authorization', f'Bearer {self.token}')
            if body is not None:
                connection.putheader('Content-Length', str(length))
            connection.endheaders()
            if body is not None:
                sent = 0
                for block in iter(lambda: body.read(CHUNK), b''):
                    connection.send(block)
                    sent += len(block)
                    if progress:
                        progress(sent, length)
            response = connection.getresponse()
            raw = response.read()
        except OSError as exc:
            raise self._reach_error(exc) from exc
        finally:
            connection.close()
        try:
            payload = json.loads(raw.decode('utf-8')) if raw else {}
        except ValueError:
            payload = {'error': raw[:200].decode('utf-8', 'replace')}
        if response.status >= 400:
            raise UploadError(f'Server refused ({response.status}): {payload.get("error", "no details")}')
        return payload

    def models(self):
        return self.request('GET', '/api/models').get('models', [])


def progress_bar(done, total, started=[0.0]):  # noqa: B006 - start time kept between calls
    if not started[0]:
        started[0] = time.monotonic()
    elapsed = max(1e-6, time.monotonic() - started[0])
    width = 30
    filled = int(width * done / max(1, total))
    rate = done * 8 / elapsed / 1e6
    sys.stderr.write(f'\r  [{"#" * filled}{"." * (width - filled)}] {done * 100 // max(1, total):3d} %  '
                     f'{human(done)} / {human(total)}  {rate:.1f} Mbit/s ')
    sys.stderr.flush()
    if done >= total:
        sys.stderr.write('\n')


def show(models):
    if not models:
        print('  (the library is empty)')
    for m in models:
        classes = ', '.join(m.get('classes') or []) or 'classes unknown'
        print(f"  {m['name']:<24} {human(m['size']):>9}  {m.get('added', ''):<16}  {classes}")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('file', nargs='?', help='model to upload (.pt, .onnx or .torchscript)')
    parser.add_argument('--name', help='name the students see (default: the file name)')
    parser.add_argument('--description', default='', help='short note shown in the Model list')
    parser.add_argument('--classes', help='comma-separated class names (default: read from the model '
                                          'when Ultralytics is installed here)')
    parser.add_argument('--imgsz', type=int, help='training image size (informational)')
    parser.add_argument('--server', default=os.environ.get('STEREO_LIBRARY_URL'),
                        help='library URL, e.g. http://192.168.0.102:8765 (env STEREO_LIBRARY_URL)')
    parser.add_argument('--token', default=os.environ.get('STEREO_LIBRARY_TOKEN'),
                        help='upload token (env STEREO_LIBRARY_TOKEN)')
    parser.add_argument('--token-file', help='read the token from this file instead')
    parser.add_argument('--via', default=os.environ.get('STEREO_LIBRARY_VIA'),
                        help="this computer's Wi-Fi IP, to send over Wi-Fi (env STEREO_LIBRARY_VIA)")
    parser.add_argument('--list', action='store_true', help='list the library and exit')
    args = parser.parse_args()

    if not args.server:
        parser.error('--server is required (or set STEREO_LIBRARY_URL), e.g. http://192.168.0.102:8765')
    token = args.token
    if args.token_file:
        token = Path(args.token_file).read_text(encoding='utf-8').strip()
    try:
        library = Library(args.server, token, args.via)
        if args.list:
            print(f'Library at {library.describe()}:')
            show(library.models())
            return 0
        if not args.file:
            parser.error('give the model file to upload, or --list')
        path = Path(args.file)
        if not path.is_file():
            raise UploadError(f'{path} does not exist')
        if path.suffix.lower() not in FORMATS:
            raise UploadError(f'{path.suffix or "This file type"} is not accepted; use {", ".join(FORMATS)}. '
                              'TensorRT .engine files only run on the GPU they were built on.')
        if not token:
            raise UploadError('An upload token is required: --token, --token-file or STEREO_LIBRARY_TOKEN. '
                              'Get it on the camera PC with: python tools/model_library.py token')
        name = args.name or path.stem
        size = path.stat().st_size
        print(f'Checking {library.describe()} ...')
        limits = library.request('GET', '/api/upload')
        if size > limits.get('max_bytes', size):
            raise UploadError(f'{human(size)} is over the server limit of {human(limits["max_bytes"])}')
        classes = [c.strip() for c in args.classes.split(',')] if args.classes else class_names(path)
        sha = sha256_of(path)
        quote = urllib.parse.quote
        headers = {'Content-Type': 'application/octet-stream', 'X-Model-File': quote(path.name),
                   'X-Model-SHA256': sha, 'X-Model-Description': quote(args.description)}
        if classes:
            headers['X-Model-Classes'] = quote(','.join(classes))
        if args.imgsz:
            headers['X-Model-Imgsz'] = str(args.imgsz)
        print(f'Uploading {path.name} as "{name}" ({human(size)}, sha256 {sha[:12]})')
        with open(path, 'rb') as body:
            result = library.request('PUT', '/api/models/' + quote(name, safe=''), headers, body,
                                     progress=progress_bar, length=size)
        model = result['model']
        if model['sha256'] != sha:
            raise UploadError('The server stored a different file than was sent; try again')
        listed = {m['name']: m['sha256'] for m in library.models()}
        if listed.get(model['name']) != sha:
            raise UploadError('Upload finished but the model is not listed; check the server log')
        verb = 'Replaced' if result.get('replaced') else 'Added'
        print(f'{verb} "{model["name"]}" in the library. Students can pick it with the Model button.')
        if model.get('classes'):
            print('  classes: ' + ', '.join(model['classes']))
        return 0
    except UploadError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('\nCancelled; nothing was changed on the server.', file=sys.stderr)
        return 130


if __name__ == '__main__':
    raise SystemExit(main())

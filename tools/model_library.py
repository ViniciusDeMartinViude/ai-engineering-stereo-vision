#!/usr/bin/env python3
"""Manage the camera server's YOLO model library (run on the camera PC).

  python tools/model_library.py add runs/detect/train/weights/best.pt --name belt-v3 --description "Belt boxes, Nov 24"
  python tools/model_library.py list
  python tools/model_library.py remove belt-v2
  python tools/model_library.py check http://192.168.0.102:8765 --via 192.168.0.12

The server (stereo_vision_server_multiclient.py) serves this folder read-only
to the clients; it picks up changes without a restart. Files copied straight
into the folder are indexed automatically under their file name.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'common'))

from model_library import (  # noqa: E402
    DEFAULT_PORT, LibraryClient, LibraryError, LibraryStore, format_size,
)

DEFAULT_FOLDER = ROOT / 'models'


def show(entries):
    if not entries:
        print('The library is empty. Add a model with: python tools/model_library.py add best.pt --name NAME')
        return
    for e in entries:
        classes = ', '.join(e.classes[:8]) + (' ...' if len(e.classes) > 8 else '') if e.classes else 'classes unknown'
        print(f'{e.name:<24} {format_size(e.size):>9}  {e.added:<16}  sha256 {e.sha256[:12]}  {e.file}')
        print(f'{"":<24} {classes}' + (f'  -  {e.description}' if e.description else ''))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--folder', default=str(DEFAULT_FOLDER),
                        help=f'library folder (default {DEFAULT_FOLDER}); same as the server --model-library')
    sub = parser.add_subparsers(dest='command', required=True)
    add = sub.add_parser('add', help='copy a trained model into the library (replaces the same name)')
    add.add_argument('file')
    add.add_argument('--name', help='name the students see (default: file name)')
    add.add_argument('--description', default='')
    add.add_argument('--classes', help='comma-separated class names, if Ultralytics is not installed here')
    add.add_argument('--imgsz', type=int, help='image size the model was trained at (informational)')
    sub.add_parser('list', help='list the models in the library')
    remove = sub.add_parser('remove', help='delete a model from the library')
    remove.add_argument('name')
    check = sub.add_parser('check', help='list a running library from another PC, as a client would')
    check.add_argument('url', help=f'e.g. http://SERVER_IP:{DEFAULT_PORT}')
    check.add_argument('--via', help='local IP to connect from (pins the network adapter)')
    args = parser.parse_args()

    try:
        if args.command == 'check':
            client = LibraryClient(args.url, source_ip=args.via)
            print(f'Library at {client.describe()}:')
            show(client.fetch_index())
            return 0
        store = LibraryStore(args.folder)
        if args.command == 'add':
            classes = [c.strip() for c in args.classes.split(',')] if args.classes else None
            entry = store.add(args.file, args.name, args.description, classes=classes, imgsz=args.imgsz)
            print(f'Added {entry.name} ({format_size(entry.size)}) to {store.root}')
            if not entry.classes:
                print('  Class names unknown: install ultralytics here or pass --classes "a,b,c".')
            show(store.entries())
        elif args.command == 'list':
            print(f'Library folder: {store.root}')
            show(store.entries())
        elif args.command == 'remove':
            entry = store.remove(args.name)
            print(f'Removed {entry.name} ({entry.file})')
    except (LibraryError, ValueError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

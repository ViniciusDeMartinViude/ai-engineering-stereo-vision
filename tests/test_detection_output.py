"""Detection output: message format, UDP publisher and the example receiver."""
import json
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'common'), str(ROOT / 'client')]

from detection_output import (  # noqa: E402
    MAX_OBJECTS_PER_CAMERA, DetectionPublisher, detection_message, parse_target, status_message,
)
from stamp_protocol import Stamp  # noqa: E402
from yolo_detector import Detection  # noqa: E402

CAPTURE_MS = 1_795_517_000_123


def frame_meta(stamp=True, seq=7, recv_ms=CAPTURE_MS + 250):
    return {'stamp': Stamp(CAPTURE_MS, seq, -21_600) if stamp else None,
            'recv_ns': int(recv_ms * 1_000_000),
            'sizes': {'LEFT': (1920, 1080), 'RIGHT': (1920, 1080)},
            'angles': {'LEFT': 4.99, 'RIGHT': 0.0},
            'rois': {'LEFT': (96, 168, 1351, 959), 'RIGHT': None}}


def galaxy(x, side='LEFT', conf=0.94):
    return Detection(side, 'Galaxy', conf, (x - 50.0, 400.0, x + 50.0, 500.0), 2)


class UdpReceiver:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.settimeout(2)
        self.target = f'127.0.0.1:{self.sock.getsockname()[1]}'

    def receive(self):
        return json.loads(self.sock.recv(65535).decode('utf-8'))

    def close(self):
        self.sock.close()


class MessageTest(unittest.TestCase):
    def test_targets(self):
        self.assertEqual(parse_target('127.0.0.1:5600'), ('127.0.0.1', 5600))
        self.assertEqual(parse_target(' udp://localhost:7000 '), ('127.0.0.1', 7000))
        for bad in ('5600', '127.0.0.1', 'host:abc', '127.0.0.1:0', 'robot-pc:5600',
                    '239.10.10.10:5600', 'CLIENT_IP:5600'):
            with self.assertRaises(ValueError, msg=bad):
                parse_target(bad)

    def test_detection_message(self):
        detections = [galaxy(600), galaxy(900, conf=0.5), galaxy(700, side='RIGHT')]
        message = detection_message('PC-11', frame_meta(), detections, 31.25, 'belt-v3',
                                    clock_offset_ms=2.0, clock_synced=True,
                                    now_ns=int((CAPTURE_MS + 300) * 1_000_000))
        self.assertEqual((message['v'], message['type'], message['source']), (1, 'detections', 'PC-11'))
        self.assertEqual(message['seq'], 7)
        self.assertEqual(message['capture_utc_ms'], {'left': CAPTURE_MS, 'right': CAPTURE_MS - 21.6})
        self.assertEqual(message['received_utc_ms'], CAPTURE_MS + 248.0, 'in the server clock')
        self.assertEqual(message['detected_utc_ms'], CAPTURE_MS + 298.0)
        left, right = message['cameras']['left'], message['cameras']['right']
        self.assertEqual(left['size'], [1920, 1080])
        self.assertEqual(left['level_deg'], 4.99)
        self.assertEqual(left['roi'], [96, 168, 1351, 959])
        self.assertEqual([o['conf'] for o in left['objects']], [0.94, 0.5], 'most confident first')
        self.assertEqual(left['objects'][0], {'class_id': 2, 'label': 'Galaxy', 'conf': 0.94,
                                              'box': [550.0, 400.0, 650.0, 500.0], 'center': [600.0, 450.0]})
        self.assertIsNone(right['roi'])
        self.assertEqual(len(right['objects']), 1)
        json.dumps(message)  # plain JSON types only

    def test_unreadable_timestamp_and_empty_frames(self):
        message = detection_message('PC-11', frame_meta(stamp=False), [], 30.0, None, 0.0, False)
        self.assertFalse(message['stamp_ok'])
        self.assertIsNone(message['seq'])
        self.assertIsNone(message['capture_utc_ms'])
        self.assertEqual(message['cameras']['left']['objects'], [])
        status = status_message('PC-11', 'off', False, None, 0.0, False)
        self.assertEqual((status['type'], status['detection'], status['stream_live']), ('status', 'off', False))

    def test_object_cap(self):
        many = [galaxy(10 + i) for i in range(MAX_OBJECTS_PER_CAMERA + 20)]
        message = detection_message('PC', frame_meta(), many, 1.0, 'm', 0.0, True)
        left = message['cameras']['left']
        self.assertEqual(len(left['objects']), MAX_OBJECTS_PER_CAMERA)
        self.assertEqual(left['truncated'], MAX_OBJECTS_PER_CAMERA + 20)


class PublisherTest(unittest.TestCase):
    def setUp(self):
        self.receiver = UdpReceiver()

    def tearDown(self):
        self.receiver.close()

    def test_delivers_counts_and_switches_off(self):
        publisher = DetectionPublisher(self.receiver.target, enabled=True, source='PC-T')
        message = detection_message('PC-T', frame_meta(), [galaxy(600)], 30.0, 'm', 0.0, True)
        self.assertTrue(publisher.publish(message))
        self.assertEqual(self.receiver.receive(), json.loads(json.dumps(message)))
        status = publisher.status()
        self.assertEqual((status['sent'], status['errors'], status['last_type']), (1, 0, 'detections'))
        self.assertFalse(status['no_listener'])
        self.assertFalse(publisher.heartbeat_due())
        publisher.set_enabled(False)
        self.assertFalse(publisher.publish(message))
        self.assertEqual(publisher.status()['sent'], 1)
        publisher.close()

    def test_reports_when_nothing_listens(self):
        target = self.receiver.target
        self.receiver.close()  # the port is now closed
        publisher = DetectionPublisher(target, enabled=True)
        for _ in range(3):
            publisher.publish(status_message('PC', 'off', True, None, 0.0, True))
            time.sleep(0.05)
        status = publisher.status()
        if sys.platform == 'win32':  # Windows reports ICMP "port unreachable" on the socket
            self.assertTrue(status['no_listener'])
        self.assertEqual(status['sent'], 3)
        self.assertEqual(status['errors'], 0, 'a missing listener is not a send error')
        publisher.close()


class ReceiverScriptTest(unittest.TestCase):
    def test_example_receiver_prints_objects_and_speed(self):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(('127.0.0.1', 0))
        port = probe.getsockname()[1]
        probe.close()
        script = ROOT / 'tools' / 'receive_detections.py'
        process = subprocess.Popen([sys.executable, '-u', '-I', str(script), '--port', str(port)],
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        try:
            self.assertIn('Listening', process.stdout.readline())
            publisher = DetectionPublisher(f'127.0.0.1:{port}', enabled=True)
            for i, x in enumerate((600.0, 650.0)):  # moves 50 px in 100 ms: 500 px/s
                meta = frame_meta(seq=7 + i)
                meta['stamp'] = Stamp(CAPTURE_MS + 100 * i, 7 + i, 0)
                publisher.publish(detection_message('PC', meta, [galaxy(x)], 30.0, 'm', 0.0, True))
                time.sleep(0.1)
            publisher.publish(status_message('PC', 'off', True, None, 0.0, True))
            lines = [process.stdout.readline() for _ in range(7)]
            publisher.close()
        finally:
            process.kill()
            process.wait(5)
            process.stdout.close()
        text = ''.join(lines)
        self.assertIn('Galaxy 94% @(600,450)', text)
        self.assertIn('v=(+500,+0) px/s', text)
        self.assertIn('status from PC: detection off', text)


if __name__ == '__main__':
    unittest.main()

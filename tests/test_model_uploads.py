"""Token-protected uploads to the model library, including the standalone uploader script."""
import hashlib
import http.client
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'common'))

from model_library import LibraryStore, ModelLibraryServer, load_upload_token, save_upload_token  # noqa: E402

TOKEN = 'test-token-not-a-secret'


def put(port, name, data, token=TOKEN, sha=None, filename='best.pt', host='127.0.0.1', extra=None):
    connection = http.client.HTTPConnection(host, port, timeout=10)
    headers = {'X-Model-File': filename, 'X-Model-SHA256': sha or hashlib.sha256(data).hexdigest(),
               'Content-Length': str(len(data))}
    if token:
        headers['Authorization'] = f'Bearer {token}'
    headers.update(extra or {})
    try:
        connection.request('PUT', f'/api/models/{name}', body=data, headers=headers)
        response = connection.getresponse()
        return response.status, json.loads(response.read() or b'{}')
    finally:
        connection.close()


class UploadTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.store = LibraryStore(self.base / 'library')
        self.logs = []
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.stop()
        self.tmp.cleanup()

    def serve(self, token=TOKEN, upload_on=None, max_bytes=1024 * 1024):
        self.server = ModelLibraryServer(self.store, '127.0.0.1', 0, 0, log=self.logs.append,
                                         upload_token=token, upload_on=upload_on, upload_max_bytes=max_bytes)
        self.server.start()
        self.server.port = self.server.httpd.server_address[1]
        return self.server.port

    def test_uploads_off_without_token(self):
        port = self.serve(token=None)
        status, body = put(port, 'belt', b'x' * 100)
        self.assertEqual(status, 403)
        self.assertIn('Uploads are off', body['error'])
        self.assertEqual(self.store.entries(), [])

    def test_wrong_or_missing_token(self):
        port = self.serve()
        self.assertEqual(put(port, 'belt', b'x' * 100, token='nope')[0], 401)
        self.assertEqual(put(port, 'belt', b'x' * 100, token=None)[0], 401)
        self.assertEqual(self.store.entries(), [])
        self.assertEqual(self.server.status()['rejected_uploads'], 2)

    def test_upload_replace_and_metadata(self):
        port = self.serve()
        status, body = put(port, 'belt%20v4', b'a' * 1000,
                           extra={'X-Model-Classes': 'Ajwa,Galaxy', 'X-Model-Description': 'Nov%2024'})
        self.assertEqual(status, 201)
        self.assertFalse(body['replaced'])
        entry = self.store.find('belt-v4')
        self.assertEqual(entry.classes, ('Ajwa', 'Galaxy'))
        self.assertEqual(entry.description, 'Nov 24')
        self.assertEqual((self.store.root / 'belt-v4.pt').read_bytes(), b'a' * 1000)
        status, body = put(port, 'belt-v4', b'b' * 1000)
        self.assertEqual(status, 201)
        self.assertTrue(body['replaced'])
        self.assertEqual(len(self.store.entries()), 1)
        self.assertEqual(self.store.find('belt-v4').description, 'Nov 24', 'kept on replace')
        self.assertFalse(any((self.store.root / '.incoming').iterdir()), 'no temporary files left')

    def test_corrupt_wrong_type_and_too_big_are_refused(self):
        port = self.serve(max_bytes=2000)
        self.assertEqual(put(port, 'belt', b'a' * 100, sha='0' * 64)[0], 422)
        self.assertEqual(put(port, 'belt', b'a' * 100, filename='model.engine')[0], 415)
        self.assertEqual(put(port, 'belt', b'a' * 3000)[0], 413)
        self.assertEqual(self.store.entries(), [])

    def test_upload_on_restricts_the_local_address(self):
        port = self.serve(upload_on=['192.0.2.1'])
        status, body = put(port, 'belt', b'a' * 100)
        self.assertEqual(status, 403)
        self.assertIn('only accepted on 192.0.2.1', body['error'])
        self.server.stop()
        port = self.serve(upload_on=['127.0.0.1'])
        self.assertEqual(put(port, 'belt', b'a' * 100)[0], 201)

    def test_token_file_round_trip(self):
        path = self.base / 'config' / 'library-upload.token'
        self.assertIsNone(load_upload_token(path))
        save_upload_token(path, TOKEN)
        self.assertEqual(load_upload_token(path), TOKEN)

    def test_standalone_uploader_script(self):
        port = self.serve()
        model = self.base / 'best.pt'
        model.write_bytes(bytes(range(256)) * 400)
        script = ROOT / 'tools' / 'upload_model.py'
        url = f'http://127.0.0.1:{port}'

        def run(*args, token=TOKEN, via='127.0.0.1'):
            env = {'PATH': '', 'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')}
            if token:
                env['STEREO_LIBRARY_TOKEN'] = token
            return subprocess.run([sys.executable, '-I', str(script), *args, '--server', url, '--via', via],
                                  capture_output=True, text=True, env=env, timeout=60)

        result = run(str(model), '--name', 'belt-v5', '--classes', 'Ajwa,Medjool', '--description', 'from test')
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('Added "belt-v5"', result.stdout)
        self.assertEqual(self.store.find('belt-v5').classes, ('Ajwa', 'Medjool'))
        result = run(str(model), '--name', 'belt-v5', '--classes', 'Ajwa,Medjool')
        self.assertIn('Replaced "belt-v5"', result.stdout)
        result = run('--list')
        self.assertIn('belt-v5', result.stdout)
        result = run(str(model), '--name', 'x', token='wrong')
        self.assertEqual(result.returncode, 1)
        self.assertIn('Wrong or missing upload token', result.stderr)
        result = run('--list', via='CLIENT_WIFI_IP')  # a placeholder left in the command
        self.assertEqual(result.returncode, 1)
        self.assertIn("'CLIENT_WIFI_IP' is not an IPv4 address", result.stderr)


if __name__ == '__main__':
    unittest.main()

"""Model library: indexing, read-only HTTP service, verified download and cache."""
import http.client
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))

from model_library import (  # noqa: E402
    DownloadJob, LibraryClient, LibraryError, LibraryStore, ModelLibraryServer, check_source_ip, sha256_file,
)


def make_model(folder, name, size=300_000, seed=1):
    path = Path(folder) / name
    path.write_bytes(bytes((i * seed + i // 7) % 251 for i in range(size)))
    return path


class LibraryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.library = base / 'library'
        self.cache = base / 'cache'
        self.sources = base / 'sources'
        self.sources.mkdir()
        self.store = LibraryStore(self.library)
        self.logs = []
        self.server = None

    def tearDown(self):
        if self.server:
            self.server.stop()
        self.tmp.cleanup()

    def counter(self, read, expected, timeout=2.0):
        """Server counters are updated just after the last byte leaves; wait for them."""
        deadline = time.monotonic() + timeout
        value = read(self.server.status())
        while value != expected and time.monotonic() < deadline:
            time.sleep(0.01)
            value = read(self.server.status())
        return value

    def serve(self, rate_mbps=0):
        self.server = ModelLibraryServer(self.store, '127.0.0.1', 0, rate_mbps, log=self.logs.append)
        self.server.start()
        self.server.port = self.server.httpd.server_address[1]
        return LibraryClient(f'http://127.0.0.1:{self.server.port}', cache_dir=self.cache)

    def test_add_list_replace_remove(self):
        src = make_model(self.sources, 'best.pt')
        entry = self.store.add(src, 'belt v3', 'boxes', classes=['box', 'can'])
        self.assertEqual(entry.name, 'belt-v3')
        self.assertEqual(entry.sha256, sha256_file(src))
        self.assertEqual(entry.classes, ('box', 'can'))
        self.assertTrue((self.library / 'belt-v3.pt').is_file())
        newer = make_model(self.sources, 'best2.pt', seed=3)
        replaced = self.store.add(newer, 'belt-v3')
        self.assertNotEqual(replaced.sha256, entry.sha256)
        self.assertEqual(replaced.description, 'boxes', 'description kept on replace')
        self.assertEqual([e.name for e in self.store.entries()], ['belt-v3'])
        self.store.remove('belt-v3')
        self.assertEqual(self.store.entries(), [])
        self.assertFalse((self.library / 'belt-v3.pt').exists())

    def test_dropped_files_are_indexed_and_engine_files_refused(self):
        self.library.mkdir()
        make_model(self.library, 'dropped.pt')
        (self.library / 'notes.txt').write_text('ignored')
        self.assertEqual([e.name for e in self.store.entries()], ['dropped'])
        engine = make_model(self.sources, 'model.engine')
        with self.assertRaises(LibraryError):
            self.store.add(engine)

    def test_download_verify_cache_and_offline(self):
        entry = self.store.add(make_model(self.sources, 'best.pt'), 'belt')
        client = self.serve()
        listed = client.fetch_index()
        self.assertEqual([e.sha256 for e in listed], [entry.sha256])
        seen = []
        path = client.download(listed[0], progress=lambda d, t: seen.append((d, t)))
        self.assertEqual(sha256_file(path), entry.sha256)
        self.assertEqual(path.name, 'belt.pt')
        self.assertEqual(seen[-1], (entry.size, entry.size))
        self.assertEqual(self.counter(lambda s: s['downloads'], 1), 1)
        self.server.stop()
        self.server = None
        again, offline = client.resolve('BELT')
        self.assertTrue(offline, 'falls back to the saved listing')
        self.assertEqual(client.download(again), path, 'cached copy reused without the server')

    def test_corrupt_download_is_rejected(self):
        entry = self.store.add(make_model(self.sources, 'best.pt'), 'belt')
        client = self.serve()
        (self.library / entry.file).write_bytes(b'tampered' * 10)  # changes after listing
        os.utime(self.library / entry.file, None)
        listed = entry
        with self.assertRaises(LibraryError):
            client.download(listed)
        self.assertIsNone(client.cached_path(listed))

    def test_only_listed_hashes_are_served(self):
        self.store.add(make_model(self.sources, 'best.pt'), 'belt')
        self.serve()
        for path in ('/api/models/' + '0' * 64, '/../index.json', '/api/models/..%2Findex.json', '/x'):
            connection = http.client.HTTPConnection('127.0.0.1', self.server.port, timeout=5)
            connection.request('GET', path)
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 404, path)

    def test_speed_cap_and_source_ip(self):
        entry = self.store.add(make_model(self.sources, 'best.pt', size=250_000), 'belt')
        client = self.serve(rate_mbps=4.0)  # 250 kB at 0.5 MB/s: ~0.39 s (the last chunk is not waited)
        client = LibraryClient(client.base_url, cache_dir=self.cache, source_ip='127.0.0.1')
        start = time.monotonic()
        client.download(entry)
        self.assertGreater(time.monotonic() - start, 0.3)
        uncapped = LibraryClient(client.base_url, cache_dir=self.cache / 'b')
        self.server.pacer = type(self.server.pacer)(0)
        start = time.monotonic()
        uncapped.download(entry)
        self.assertLess(time.monotonic() - start, 0.3, 'without the cap it is much faster')
        bad = LibraryClient(client.base_url, cache_dir=self.cache / 'x', source_ip='203.0.113.7')
        with self.assertRaises(LibraryError):
            bad.fetch_index()

    def test_source_ip_must_be_an_address(self):
        self.assertIsNone(check_source_ip(None))
        self.assertIsNone(check_source_ip('  '))
        self.assertEqual(check_source_ip(' 192.168.0.12 '), '192.168.0.12')
        for bad in ('CLIENT_WIFI_IP', "<this computer's Wi-Fi IP>", 'laptop.local', '192.168.0', '::1'):
            with self.assertRaises(ValueError) as caught:
                LibraryClient('http://127.0.0.1:8765', cache_dir=self.cache, source_ip=bad)
            self.assertIn('is not an IPv4 address', str(caught.exception))
            self.assertIn('ipconfig', str(caught.exception))

    def test_download_job_reports_progress(self):
        self.store.add(make_model(self.sources, 'best.pt'), 'belt')
        client = self.serve()
        job = DownloadJob(client, name='belt').start()
        deadline = time.monotonic() + 10
        while not job.finished and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertIsNone(job.error)
        self.assertEqual(job.fraction, 1.0)
        missing = DownloadJob(client, name='nope').start()
        while not missing.finished:
            time.sleep(0.02)
        self.assertIn('No model named', missing.error)

    def test_parallel_downloads(self):
        entry = self.store.add(make_model(self.sources, 'best.pt'), 'belt')
        self.serve()
        results = []

        def fetch(i):
            c = LibraryClient(f'http://127.0.0.1:{self.server.port}', cache_dir=self.cache / str(i))
            results.append(sha256_file(c.download(entry)))
        threads = [threading.Thread(target=fetch, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(10)
        self.assertEqual(results, [entry.sha256] * 4)
        self.assertEqual(self.counter(lambda s: s['per_model'].get(entry.sha256, 0), 4), 4)


if __name__ == '__main__':
    unittest.main()

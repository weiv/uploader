import http.client
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

import server


class SanitizeNameTests(unittest.TestCase):
    def test_plain_name_passes_through(self):
        self.assertEqual(server.sanitize_name("report.zip"), "report.zip")

    def test_strips_directory_components(self):
        self.assertEqual(server.sanitize_name("/etc/passwd"), "passwd")
        self.assertEqual(server.sanitize_name("sub/dir/file.txt"), "file.txt")

    def test_salvages_traversal_to_basename(self):
        # A leading "../" is stripped to the basename, by the same rule that
        # turns "/etc/passwd" into "passwd". The result stays inside UPLOAD_DIR,
        # so it is salvaged rather than rejected (see design spec).
        self.assertEqual(server.sanitize_name("../x"), "x")

    def test_rejects_traversal_and_empties(self):
        # These all reduce to an empty basename or a bare "."/".." after
        # stripping directory components — nothing usable remains.
        for bad in ["", ".", "..", "a/../b/", "   ", "/"]:
            with self.assertRaises(ValueError):
                server.sanitize_name(bad)


class UniquePathTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_returns_name_when_free(self):
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a.txt")

    def test_suffixes_on_collision(self):
        open(os.path.join(self.dir, "a.txt"), "w").close()
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a(1).txt")

    def test_increments_until_free(self):
        open(os.path.join(self.dir, "a.txt"), "w").close()
        open(os.path.join(self.dir, "a(1).txt"), "w").close()
        p = server.unique_path(self.dir, "a.txt")
        self.assertEqual(os.path.basename(p), "a(2).txt")

    def test_name_without_extension(self):
        open(os.path.join(self.dir, "README"), "w").close()
        p = server.unique_path(self.dir, "README")
        self.assertEqual(os.path.basename(p), "README(1)")

    def test_raises_when_exhausted(self):
        # Mock so every candidate "exists" — exercises the bound without
        # creating 1000 files (this repo lives on an iCloud-synced path).
        from unittest import mock
        with mock.patch("os.path.exists", return_value=True):
            with self.assertRaises(RuntimeError):
                server.unique_path(self.dir, "a.txt")


class ServerTestCase(unittest.TestCase):
    """Base: starts server.Handler against a temp dir on an ephemeral port."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self._orig_dir = server.UPLOAD_DIR
        server.UPLOAD_DIR = self.dir
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server.UPLOAD_DIR = self._orig_dir

    def conn(self):
        return http.client.HTTPConnection("127.0.0.1", self.port)

    def request(self, method, path, body=None, headers=None):
        c = self.conn()
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data


class ListingTests(ServerTestCase):
    def test_empty_listing(self):
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data), [])

    def test_lists_files_newest_first_excluding_part(self):
        # Create two real files plus a .part temp that must be hidden.
        import time
        with open(os.path.join(self.dir, "old.txt"), "w") as f:
            f.write("old")
        time.sleep(0.01)
        with open(os.path.join(self.dir, "new.txt"), "w") as f:
            f.write("newer")
        open(os.path.join(self.dir, ".abc.part"), "w").close()
        status, data = self.request("GET", "/api/files")
        self.assertEqual(status, 200)
        names = [e["name"] for e in json.loads(data)]
        self.assertEqual(names, ["new.txt", "old.txt"])
        sizes = {e["name"]: e["size"] for e in json.loads(data)}
        self.assertEqual(sizes["new.txt"], 5)


class UploadTests(ServerTestCase):
    def _put(self, name, payload):
        c = self.conn()
        headers = {"Content-Length": str(len(payload))}
        c.request("PUT", "/upload?name=" + name, body=payload, headers=headers)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, data

    def test_round_trips_bytes(self):
        status, data = self._put("hello.txt", b"hello world")
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["name"], "hello.txt")
        with open(os.path.join(self.dir, "hello.txt"), "rb") as f:
            self.assertEqual(f.read(), b"hello world")

    def test_multi_chunk_payload(self):
        payload = os.urandom(server.CHUNK * 3 + 123)
        status, data = self._put("big.bin", payload)
        self.assertEqual(status, 201)
        with open(os.path.join(self.dir, "big.bin"), "rb") as f:
            self.assertEqual(f.read(), payload)

    def test_collision_autorenames_and_reports(self):
        self._put("a.txt", b"first")
        status, data = self._put("a.txt", b"second")
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(data)["name"], "a(1).txt")
        with open(os.path.join(self.dir, "a(1).txt"), "rb") as f:
            self.assertEqual(f.read(), b"second")

    def test_no_part_files_left_behind(self):
        self._put("a.txt", b"x")
        leftovers = [n for n in os.listdir(self.dir) if n.endswith(".part")]
        self.assertEqual(leftovers, [])

    def test_rejects_bad_name(self):
        status, _ = self._put("..", b"x")
        self.assertEqual(status, 400)

    def test_rejects_non_integer_content_length(self):
        # Send an explicit non-integer Content-Length. This deterministically
        # hits the 400 branch (int() raises) regardless of http.client version
        # quirks around auto-injecting Content-Length for bodyless requests.
        c = self.conn()
        c.putrequest("PUT", "/upload?name=x.txt")
        c.putheader("Content-Length", "notanumber")
        c.endheaders()
        r = c.getresponse()
        status = r.status
        r.read()
        c.close()
        self.assertEqual(status, 400)


from urllib.parse import quote


class PageTests(ServerTestCase):
    def test_root_serves_html(self):
        c = self.conn()
        c.request("GET", "/")
        r = c.getresponse()
        ctype = r.getheader("Content-Type")
        body = r.read().decode("utf-8")
        c.close()
        self.assertEqual(r.status, 200)
        self.assertIn("text/html", ctype)
        # Sanity: the page references the upload + listing endpoints.
        self.assertIn("/upload", body)
        self.assertIn("/api/files", body)


class DownloadTests(ServerTestCase):
    def test_downloads_exact_content(self):
        payload = os.urandom(server.CHUNK * 2 + 7)
        with open(os.path.join(self.dir, "f.bin"), "wb") as f:
            f.write(payload)
        status, data = self.request("GET", "/download/f.bin")
        self.assertEqual(status, 200)
        self.assertEqual(data, payload)

    def test_missing_file_404(self):
        status, _ = self.request("GET", "/download/nope.txt")
        self.assertEqual(status, 404)

    def test_rejects_traversal(self):
        status, _ = self.request("GET", "/download/" + quote("../server.py"))
        self.assertIn(status, (400, 404))

    def test_sets_attachment_header(self):
        with open(os.path.join(self.dir, "f.txt"), "w") as f:
            f.write("hi")
        c = self.conn()
        c.request("GET", "/download/f.txt")
        r = c.getresponse()
        disp = r.getheader("Content-Disposition")
        r.read()
        c.close()
        self.assertIn("attachment", disp)
        self.assertIn("f.txt", disp)


import io


class StreamBodyTests(unittest.TestCase):
    def test_copies_exact_bytes(self):
        dst = io.BytesIO()
        server.stream_body(io.BytesIO(b"abcdef"), dst, 6)
        self.assertEqual(dst.getvalue(), b"abcdef")

    def test_short_read_raises_incomplete(self):
        # Source ends early (client disconnected): must raise, not silently
        # write a truncated file.
        dst = io.BytesIO()
        with self.assertRaises(server.IncompleteUpload):
            server.stream_body(io.BytesIO(b"abc"), dst, 6)


if __name__ == "__main__":
    unittest.main()

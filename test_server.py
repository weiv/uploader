import os
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()

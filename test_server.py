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


if __name__ == "__main__":
    unittest.main()

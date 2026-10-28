"""Exercise the real installer with local release fixtures, without network/model downloads."""
import hashlib
import io
import os
from pathlib import Path
import platform
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
ASSET = "transcribe-darwin-arm64.tar.gz"


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "arm64", "Apple Silicon installer")
class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="transcribe installer ")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.archive = self.directory / ASSET
        self.checksums = self.directory / "SHA256SUMS"
        self.env = dict(os.environ, TRANSCRIBE_ARCHIVE=str(self.archive),
                        TRANSCRIBE_CHECKSUMS=str(self.checksums),
                        TRANSCRIBE_INSTALL_ROOT=str(self.directory / "releases"),
                        TRANSCRIBE_BIN_DIR=str(self.directory / "bin"))
        self.make_archive()

    def make_archive(self, extra=None):
        entries = {"transcribe": b"#!/bin/sh\nprintf 'fixture\\n'\n", "python/worker.py": b"# worker\n",
                   "model-pins.json": b"{}", "pyproject.toml": b"", "uv.lock": b""}
        if extra:
            entries.update(extra)
        with tarfile.open(self.archive, "w:gz") as archive:
            for name, contents in entries.items():
                info = tarfile.TarInfo(name)
                info.size = len(contents)
                archive.addfile(info, io.BytesIO(contents))
        digest = hashlib.sha256(self.archive.read_bytes()).hexdigest()
        self.checksums.write_text(f"{digest}  {ASSET}\n")

    def install(self):
        return subprocess.run(["sh", str(ROOT / "install.sh")], env=self.env,
                              capture_output=True, text=True)

    def test_install_repeat_and_upgrade_preserve_previous_release(self):
        first = self.install()
        self.assertEqual(first.returncode, 0, first.stderr)
        link = self.directory / "bin/transcribe"
        old = link.resolve()
        self.assertEqual(subprocess.check_output([str(link)], text=True), "fixture\n")
        self.assertEqual(self.install().returncode, 0)
        self.make_archive({"pyproject.toml": b"# upgraded\n"})
        self.assertEqual(self.install().returncode, 0)
        self.assertNotEqual(link.resolve(), old)
        self.assertTrue(old.is_file())

    def test_corrupt_download_does_not_replace_installation(self):
        self.assertEqual(self.install().returncode, 0)
        link = self.directory / "bin/transcribe"
        old = link.resolve()
        self.archive.write_bytes(b"corrupt download")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("checksum mismatch", result.stderr)
        self.assertEqual(link.resolve(), old)

    def test_unexpected_archive_path_is_rejected(self):
        self.make_archive({"../outside": b"bad"})
        self.assertNotEqual(self.install().returncode, 0)
        self.assertFalse((self.directory / "outside").exists())

    def test_existing_executable_is_preserved(self):
        (self.directory / "bin").mkdir()
        target = self.directory / "bin/transcribe"
        target.write_text("keep me")
        self.assertNotEqual(self.install().returncode, 0)
        self.assertEqual(target.read_text(), "keep me")

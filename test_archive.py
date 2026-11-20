import base64
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import archive


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg is required")
class ArchiveIntegrationTest(unittest.TestCase):
    def test_import_index_search_edit_and_stream(self):
        with tempfile.TemporaryDirectory() as sandbox:
            root = Path(sandbox)
            source = root / "incoming"
            source.mkdir()
            clip = source / "ACT-021 - Motor test.mp4"
            subprocess.run([
                "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=s=32x32",
                "-f", "lavfi", "-i", "sine=frequency=400", "-t", "1",
                "-c:v", "mpeg4", "-c:a", "aac",
                "-metadata", "creation_time=2024-07-15T13:45:00Z", str(clip),
            ], check=True)
            data = root / "data"
            with patch.multiple(archive, ROOT=data, MEDIA=data / "videos"), patch.dict(
                os.environ, ARCHIVE_PASSWORD="test-only",
                ARCHIVE_PUBLIC_ORIGIN="https://archive.example.test",
            ):
                archive.initialize()
                self.assertEqual(archive.import_folder(source), 0)
                self.assertEqual(archive.import_folder(source), 0)
                self.assertEqual(archive.scan("base", metadata_only=True), 0)
                client = archive.app.test_client()
                credentials = base64.b64encode(b"archive:test-only").decode()
                headers = {"Authorization": f"Basic {credentials}"}
                self.assertEqual(client.get("/media/1").status_code, 401)
                homepage = client.get("/", headers=headers)
                self.assertEqual(homepage.status_code, 200)
                self.assertIn(b"Recording library", homepage.data)
                self.assertNotIn(b"{% extends", homepage.data)
                for url in ("/static/archive.css", "/static/archive.js"):
                    self.assertIn(url.encode(), homepage.data)
                    with client.get(url, headers=headers) as asset:
                        self.assertEqual(asset.status_code, 200)
                self.assertEqual(client.get("/templates/index.html", headers=headers).status_code, 404)
                self.assertIn(b"Transcription is pending", client.get("/videos/1", headers=headers).data)
                self.assertIn(b"ACT-021", client.get("/?from=2024-07-15&to=2024-07-15", headers=headers).data)
                self.assertIn(b"No recordings", client.get("/?from=2024-07-16", headers=headers).data)
                fields = {"code": "ACT-021-B", "description": "Checkout demo"}
                self.assertEqual(client.post("/videos/1", data=fields, headers=headers).status_code, 403)
                origin = {**headers, "Origin": "https://archive.example.test"}
                self.assertEqual(client.post("/videos/1", data=fields, headers=origin).status_code, 303)

                transcript = [SimpleNamespace(start=0.4, text="The motor test is complete.")]
                with patch("faster_whisper.WhisperModel") as model:
                    model.return_value.transcribe.return_value = (iter(transcript), None)
                    self.assertEqual(archive.scan("base", metadata_only=False), 0)
                    self.assertEqual(archive.scan("base", metadata_only=False), 0)
                    model.return_value.transcribe.assert_called_once()
                result = client.get("/?q=test+is+complete", headers=headers)
                self.assertIn(b"ACT-021-B", result.data)
                self.assertIn(b"#t=0.4", result.data)
                self.assertIn(b"Checkout demo", result.data)
                self.assertIn(b"ACT-021-B", client.get("/?status=Ready&sort=name", headers=headers).data)
                self.assertIn(b"No recordings found", client.get("/?status=Pending", headers=headers).data)
                edit = client.post("/videos/1?q=motor+test&sort=name", data=fields, headers=origin)
                self.assertEqual(edit.status_code, 303)
                self.assertIn("q=motor+test", edit.headers["Location"])
                self.assertIn("sort=name", edit.headers["Location"])
                self.assertIn(b"Details saved.", client.get(edit.headers["Location"], headers=headers).data)
                with closing(archive.connect()) as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM captions").fetchone()[0], 1)
                media = client.get("/media/1", headers={**headers, "Range": "bytes=0-31"})
                with media:
                    self.assertEqual(media.status_code, 206)
                    self.assertEqual(media.data, clip.read_bytes()[:32])
                    self.assertIn("no-store", media.headers["Cache-Control"])


if __name__ == "__main__":
    unittest.main()

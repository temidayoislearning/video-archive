# Video Archive

A single-server application that copies a video collection to server storage,
indexes embedded creation dates and spoken audio, and makes the recordings
searchable from a browser.

## Install and run

Use Python 3.10+ with SQLite FTS5 and install FFmpeg so both `ffmpeg` and
`ffprobe` are on PATH. On Windows, use PowerShell in this project folder.
On macOS or Linux, use `python3` if your system uses that name.

```text
python -m venv .venv
```

Activate the environment with `.\.venv\Scripts\Activate.ps1` in PowerShell
or `source .venv/bin/activate` in a POSIX shell, then run:

```text
python -m pip install -r requirements.txt
python archive.py serve
```

Open http://127.0.0.1:8000. In a second terminal with the same environment
activated, import your existing video folder or SD card:

```text
python archive.py import "E:\DCIM"
```

Use the actual path to your source folder. You can also copy files directly
into `archive-data/videos` and run `python archive.py scan` afterwards.
Run both terminals from the same working directory or set `ARCHIVE_DATA`
to the same absolute path in both. The original files and SQLite database
are kept under that directory. The source folder can be removed once its
files have copied.

The first full scan may spend time downloading the Whisper `base` model,
then transcribes locally on CPU without sending audio to an API. To index
dates and filenames immediately, add `--metadata-only` to the import.
Run `python archive.py scan` later to transcribe pending videos or retry
failures. To use a local model directory, pass `--model /path/to/model`
to the import or scan command. Unchanged successful files are skipped.

## Filenames, dates, and search

- `ACT-021.mp4` becomes code name `ACT-021`, with a blank description.
- `ACT-021 - Motor test.mp4` and `ACT-021__Motor test.mp4` become code
  name `ACT-021` and description `Motor test`.
- The recording page lets you correct both fields. Edits survive rescans.
  The original filename and its relative folder are visible on each recording.
- The archive preserves the source folder structure. If a different file
  already occupies the same relative path, rename the incoming file and retry.
- The dashboard shows the embedded date and the tag it came from.
  Dates are unknown when a supported tag is missing or unreadable.
- Search matches substrings in code names, descriptions, and paths or a
  phrase within a single transcript segment. Click a timestamp to seek
  to the matching segment. The browser determines which formats can play
  inline. The original remains downloadable for playback in a local player.

## Run on a server

Run the commands on the server and place `ARCHIVE_DATA` on persistent storage
with room for the original videos and temporary audio during transcription.
Waitress serves the application. For remote use, set a strong
`ARCHIVE_PASSWORD` and expose the app through an HTTPS reverse proxy.
The login username is `archive`. Preserve the public Host header and set
the forwarded protocol to HTTPS. Run Waitress on loopback behind the proxy.
For a proxy that terminates HTTPS, set `ARCHIVE_PUBLIC_ORIGIN` to the
public origin (for example, `https://archive.example.org`) so dashboard
edits can validate their origin. Back up both the videos and database
using a consistent snapshot or while the application and scan are stopped.

import argparse
import hmac
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from flask import Flask, abort, redirect, render_template, request, send_file, url_for

ROOT = Path(os.environ.get("ARCHIVE_DATA", "./archive-data")).resolve()
MEDIA = ROOT / "videos"
EXTENSIONS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".mpg", ".mpeg", ".wmv", ".3gp"}
app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16384

SCHEMA = """
CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, size INTEGER NOT NULL,
    mtime INTEGER NOT NULL, code TEXT NOT NULL, description TEXT NOT NULL,
    created TEXT, date_source TEXT, duration REAL, status TEXT NOT NULL, error TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS captions USING fts5(
    video_id UNINDEXED, start UNINDEXED, words
);
"""


def connect():
    db = sqlite3.connect(ROOT / "catalog.sqlite3", timeout=15)
    db.row_factory = sqlite3.Row
    return db


def initialize():
    MEDIA.mkdir(parents=True, exist_ok=True)
    with closing(connect()) as db, db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript(SCHEMA)


def filename_fields(path):
    parts = re.split(r"\s+-\s+|__", path.stem, maxsplit=1)
    return parts[0].strip() or path.stem, parts[1].strip() if len(parts) == 2 else ""


def probe(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)],
        check=True, capture_output=True, timeout=120,
    )
    metadata = json.loads(result.stdout)
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and
                  not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ValueError("No video stream found")
    container = metadata.get("format", {})
    created = source = None
    for label, item in (("Container", container), ("Video stream", video)):
        tags = {key.lower(): value for key, value in item.get("tags", {}).items()}
        for tag in ("creation_time", "com.apple.quicktime.creationdate", "date"):
            try:
                created = datetime.fromisoformat(tags[tag].strip().replace("Z", "+00:00")).isoformat()
            except (KeyError, ValueError):
                continue
            source = f"{label} · {tag}"
            break
        if created:
            break
    duration = container.get("duration") or video.get("duration")
    return created, source, float(duration) if duration and duration != "N/A" else None, any(
        stream.get("codec_type") == "audio" for stream in streams
    )


def signature(path):
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def video_files(folder):
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in EXTENSIONS and path.is_file() and not path.is_symlink():
            if path.resolve().is_relative_to(folder):
                yield path


def same_contents(left, right):
    if left.stat().st_size != right.stat().st_size:
        return False
    with left.open("rb") as src, right.open("rb") as dest:
        while block := src.read(1024 * 1024):
            if block != dest.read(1024 * 1024):
                return False
    return True


def import_folder(source):
    if not source.is_dir() or source.is_relative_to(MEDIA) or MEDIA.is_relative_to(source):
        raise ValueError("Choose an existing source folder separate from archive storage.")
    conflicts = 0
    for path in video_files(source):
        relative = path.relative_to(source)
        dest = MEDIA / relative
        if not dest.resolve().is_relative_to(MEDIA):
            raise ValueError(f"Destination is outside the archive for {relative}")
        if dest.exists():
            if not same_contents(path, dest):
                conflicts += 1
                print(f"Skipped filename conflict: {relative}. Rename the incoming file and retry.")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        before = signature(path)
        with tempfile.NamedTemporaryFile(dir=dest.parent, delete=False) as temp:
            staged = Path(temp.name)
        try:
            shutil.copy2(path, staged)
            if signature(path) != before:
                raise ValueError(f"Source changed during import: {relative}")
            staged.rename(dest)
        finally:
            staged.unlink(missing_ok=True)
        print(f"Copied {relative}", flush=True)
    return conflicts


def scan(model_name, metadata_only):
    model = None
    failures = 0
    with closing(connect()) as db:
        for path in video_files(MEDIA):
            relative = path.relative_to(MEDIA).as_posix()
            size, mtime = signature(path)
            row = db.execute("SELECT * FROM videos WHERE path = ?", (relative,)).fetchone()
            unchanged = row is not None and (row["size"], row["mtime"]) == (size, mtime)
            if unchanged and (row["status"] in {"Ready", "No audio"} or
                              (metadata_only and row["status"] == "Pending")):
                continue
            code, description = filename_fields(path)
            created = date_source = duration = error = None
            segments = []
            status = "Failed"
            try:
                created, date_source, duration, has_audio = probe(path)
                status = "Pending" if has_audio else "No audio"
                if has_audio and not metadata_only:
                    if model is None:
                        from faster_whisper import WhisperModel
                        model = WhisperModel(model_name, device="cpu", compute_type="int8")
                    with tempfile.TemporaryDirectory() as temp:
                        audio = Path(temp) / "audio.wav"
                        subprocess.run(
                            ["ffmpeg", "-nostdin", "-v", "error", "-i", str(path), "-map", "0:a:0",
                             "-ac", "1", "-ar", "16000", str(audio)],
                            check=True, capture_output=True,
                        )
                        transcript, _ = model.transcribe(str(audio))
                        segments = [(float(segment.start), segment.text.strip())
                                    for segment in transcript if segment.text.strip()]
                    status = "Ready"
            except Exception as exc:
                status, error = "Failed", str(exc)[:500]
                failures += 1
            if signature(path) != (size, mtime):
                print(f"File changed during indexing. Retry: {relative}")
                failures += 1
                continue
            with db:
                db.execute("""
                    INSERT INTO videos (path, size, mtime, code, description, created, date_source,
                                        duration, status, error) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET size=excluded.size, mtime=excluded.mtime,
                      created=excluded.created, date_source=excluded.date_source,
                      duration=excluded.duration, status=excluded.status, error=excluded.error
                """, (relative, size, mtime, code, description, created, date_source, duration, status, error))
                video_id = db.execute("SELECT id FROM videos WHERE path = ?", (relative,)).fetchone()[0]
                db.execute("DELETE FROM captions WHERE video_id = ?", (video_id,))
                db.executemany("INSERT INTO captions (video_id, start, words) VALUES (?, ?, ?)",
                               ((video_id, start, words) for start, words in segments))
            print(f"{status}: {relative}" + (f" ({error})" if error else ""), flush=True)
    return failures


@app.before_request
def authorize():
    password = os.environ.get("ARCHIVE_PASSWORD")
    if password:
        auth = request.authorization
        if not (auth and auth.username == "archive" and
                hmac.compare_digest((auth.password or "").encode(), password.encode())):
            return "Sign in to the archive.", 401, {"WWW-Authenticate": 'Basic realm="Video Archive"'}
    if request.method == "POST":
        # Browser mutations must originate from the dashboard.
        if request.headers.get("Origin") != os.environ.get("ARCHIVE_PUBLIC_ORIGIN", request.host_url.rstrip("/")):
            abort(403)


@app.after_request
def protect_archive_responses(response):
    if request.endpoint != "static":
        response.headers["Cache-Control"] = "private, no-store"
    return response


@app.template_filter("timestamp")
def timestamp(seconds):
    minutes, seconds = divmod(int(max(0, seconds or 0)), 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02}:{seconds:02}" if hours else f"{minutes}:{seconds:02}"


@app.get("/")
def index():
    query = request.args.get("q", "").strip()[:250]
    start = request.args.get("from", "")[:10]
    end = request.args.get("to", "")[:10]
    try:
        start = datetime.strptime(start, "%Y-%m-%d").date().isoformat() if start else ""
        end = datetime.strptime(end, "%Y-%m-%d").date().isoformat() if end else ""
    except ValueError:
        abort(400, "Invalid date filter")
    status = request.args.get("status", "")
    if status not in {"Ready", "Pending", "Failed", "No audio"}:
        status = ""
    orders = {
        "newest": "created IS NULL, created DESC, path",
        "oldest": "created IS NULL, created ASC, path",
        "name": "code COLLATE NOCASE, path",
    }
    sort = request.args.get("sort", "newest")
    if sort not in orders:
        sort = "newest"
    page = request.args.get("page", 1, type=int)
    page = min(max(page, 1), 1000000)
    phrase = '"' + query.replace('"', '""') + '"' if any(c.isalnum() for c in query) else ""
    with closing(connect()) as db:
        rows = db.execute(f"""
            SELECT v.*, count(*) OVER () AS total,
              (SELECT start FROM captions WHERE captions MATCH :phrase AND video_id = v.id
               ORDER BY start LIMIT 1) AS match_start,
              (SELECT words FROM captions WHERE captions MATCH :phrase AND video_id = v.id
               ORDER BY start LIMIT 1) AS match_words
            FROM videos v
            WHERE (:status = '' OR status = :status)
              AND (:start = '' OR substr(created, 1, 10) >= :start)
              AND (:end = '' OR substr(created, 1, 10) <= :end)
              AND (:query = '' OR instr(lower(path), lower(:query))
                OR instr(lower(code), lower(:query)) OR instr(lower(description), lower(:query))
                OR id IN (SELECT video_id FROM captions WHERE captions MATCH :phrase))
            ORDER BY {orders[sort]}
            LIMIT 24 OFFSET :offset
        """, dict(query=query, phrase=phrase or '""', start=start, end=end, status=status, offset=(page - 1) * 24)).fetchall()
        counts = dict(db.execute("SELECT status, count(*) FROM videos GROUP BY status").fetchall())
    filters = {"q": query, "from": start, "to": end, "sort": sort, "status": status}
    return render_template("index.html", rows=rows, counts=counts, filters=filters, page=page)


def get_video(db, video_id):
    row = db.execute("SELECT * FROM videos WHERE id = ?", (video_id,)).fetchone()
    if row is None:
        abort(404)
    return row


@app.route("/videos/<int:video_id>", methods=["GET", "POST"])
def detail(video_id):
    return_filters = {key: request.args[key] for key in ("q", "from", "to", "sort", "status", "page")
                      if request.args.get(key)}
    with closing(connect()) as db:
        row = get_video(db, video_id)
        if request.method == "POST":
            code = request.form.get("code", "").strip()
            description = request.form.get("description", "").strip()
            if not code or len(code) > 250 or len(description) > 4000:
                abort(400, "Enter a code name and keep the description under 4,001 characters.")
            with db:
                db.execute("UPDATE videos SET code=?, description=? WHERE id=?", (code, description, video_id))
            return redirect(url_for("detail", video_id=video_id, saved=1, **return_filters), code=303)
        segments = db.execute("SELECT start, words FROM captions WHERE video_id = ? ORDER BY start",
                              (video_id,)).fetchall()
    return render_template("detail.html", row=row, segments=segments, return_filters=return_filters, saved=request.args.get("saved") == "1")


@app.get("/media/<int:video_id>")
def media(video_id):
    with closing(connect()) as db:
        row = get_video(db, video_id)
    path = (MEDIA / row["path"]).resolve()
    if not path.is_relative_to(MEDIA) or not path.is_file():
        abort(404, "The original file is missing from archive storage.")
    download = request.args.get("download") == "1"
    response = send_file(path, conditional=True, as_attachment=download, download_name=path.name)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def main():
    parser = argparse.ArgumentParser(description="Index and serve a searchable video archive.")
    commands = parser.add_subparsers(dest="command", required=True)
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    for name in ("scan", "import"):
        command = commands.add_parser(name)
        command.add_argument("--metadata-only", action="store_true")
        command.add_argument("--model", default="base")
        if name == "import":
            command.add_argument("folder", type=Path)
    args = parser.parse_args()
    initialize()
    if args.command == "serve":
        if args.host not in ("127.0.0.1", "::1", "localhost") and not os.environ.get("ARCHIVE_PASSWORD"):
            parser.error("Set ARCHIVE_PASSWORD before listening on a network interface.")
        from waitress import serve as run_server
        run_server(app, host=args.host, port=args.port)
        return 0
    if not shutil.which("ffprobe") or (not args.metadata_only and not shutil.which("ffmpeg")):
        parser.error("Install FFmpeg and add ffprobe and ffmpeg to your PATH.")
    # Hold a separate database lock so indexing processes serialize while the dashboard stays writable.
    with closing(sqlite3.connect(ROOT / "scan-lock.sqlite3", timeout=1)) as lock:
        try:
            lock.execute("BEGIN EXCLUSIVE")
        except sqlite3.OperationalError as exc:
            parser.error(f"Could not acquire the archive scan lock: {exc}")
        conflicts = import_folder(args.folder.resolve()) if args.command == "import" else 0
        failures = scan(args.model, args.metadata_only)
        return int(bool(conflicts or failures))


if __name__ == "__main__":
    raise SystemExit(main())

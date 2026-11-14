#!/usr/bin/env python3
"""GoogleUnlimited local-only WebUI. Python stdlib; does not import MI3MountMac."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import subprocess
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent
DEFAULT_STATE = STATIC / "state"
MEDIA = frozenset(("jpg jpeg png heic heif webp gif bmp tiff tif avif dng raw cr2 cr3 nef arw orf rw2 pef srw raf mp4 mov mkv avi m4v 3gp 3g2 wmv webm mpg mpeg mts m2ts ts flv vob").split())
MIN_SIZE = 10240
MAX_SCAN_FILES = 10000
MAX_REQUEST_BYTES = 16384
POLL_INTERVAL = 30
PINNED_MAC_CLI_SHA256 = "d697e23c648881cf5e7db7b93f815f6d3dcbd9d7718a40d0edffee90456b0557"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def contained(path: Path, root: Path):
    try:
        return os.path.commonpath((str(path), str(root))) == str(root)
    except ValueError:
        return False


class UserError(Exception):
    pass


class Store:
    """Independent database, directory configuration and uploader worker."""

    def __init__(self, state_dir=None, *, allow_upload=False, cli=None, worker=True):
        self.state_dir = Path(state_dir or DEFAULT_STATE).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.db = self.state_dir / "queue.sqlite3"
        self.credentials = self.state_dir / "gotohp.config"
        self.cli = Path(cli or ROOT / "gotohp-cli-macos-universal")
        self.allow_upload = allow_upload
        self.upload_enabled = False  # NEVER persist auto-upload across restart
        self.monitor_enabled = False  # NEVER watch or import user's folders by default
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.scan_running = False
        self.last_scan = None
        self.last_scan_error = None
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._initialize()
        self.thread = None
        if worker:
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="gu-webui-worker")
            self.thread.start()

    @contextmanager
    def connection(self):
        with self.lock:
            conn = sqlite3.connect(str(self.db), timeout=10)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            finally:
                conn.close()

    def _initialize(self):
        with self.connection() as con:
            con.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL CHECK(kind IN ('directory','file')),
                enabled INTEGER NOT NULL DEFAULT 1,
                added_at TEXT NOT NULL
              );
              CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                bytes INTEGER NOT NULL,
                digest TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                retry_at REAL NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                media_key TEXT NOT NULL DEFAULT '',
                duplicate_of INTEGER,
                updated_at TEXT NOT NULL
              );
              CREATE INDEX IF NOT EXISTS idx_jobs_work ON jobs(status,retry_at);
              CREATE INDEX IF NOT EXISTS idx_jobs_digest ON jobs(digest,status);
            """)
            if "mtime_ns" not in {x["name"] for x in con.execute("PRAGMA table_info(jobs)")}:
                con.execute("ALTER TABLE jobs ADD COLUMN mtime_ns INTEGER NOT NULL DEFAULT 0")
        self.db.chmod(0o600)

    def _path(self, raw, *, kind=None):
        if not isinstance(raw, str) or len(raw) > 4096:
            raise UserError("请输入有效的本地路径")
        item = Path(raw.strip()).expanduser()
        if not item.is_absolute() or item.is_symlink():
            raise UserError("必须选择 Mac 的绝对路径，不能选择符号链接")
        item = item.resolve(strict=False)
        roots = [Path.home().resolve(), Path("/Volumes").resolve()]
        if not any(contained(item, root) for root in roots):
            raise UserError("只允许浏览 Mac 用户目录和 /Volumes 外接磁盘")
        protected = Path.home() / "Library" / "Application Support" / "MI3MountMac"
        if contained(item, protected):
            raise UserError("MI3MountMac 的运行目录受到保护，不能作为上传源")
        if kind == "directory" and not item.is_dir():
            raise UserError("文件夹不存在或不可访问")
        if kind == "file":
            if not item.is_file() or not self._eligible(item):
                raise UserError("请选择已存在且至少 10 KB 的照片或视频文件")
        return item

    @staticmethod
    def _eligible(file):
        try:
            return (not file.is_symlink() and file.is_file()
                    and file.suffix.lstrip(".").lower() in MEDIA
                    and file.stat().st_size >= MIN_SIZE)
        except OSError:
            return False

    @staticmethod
    def _digest(file):
        hasher = hashlib.sha256()
        with file.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                hasher.update(chunk)
        return hasher.hexdigest()

    def browse(self, raw=None):
        directory = self._path(raw or str(Path.home()), kind="directory")
        folders, files = [], []
        try:
            children = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
        except OSError as exc:
            raise UserError("无法读取目录") from exc
        for item in children[:600]:
            if item.name.startswith(".") or item.is_symlink():
                continue
            if item.is_dir() and not item.name.endswith((".app", ".photoslibrary")):
                folders.append({"name": item.name, "path": str(item)})
            elif self._eligible(item):
                files.append({"name": item.name, "path": str(item), "bytes": item.stat().st_size})
        parent = directory.parent if directory != Path.home() else None
        if parent and not (contained(parent, Path.home()) or contained(parent, Path("/Volumes"))):
            parent = None
        return {"path": str(directory), "parent": str(parent) if parent else None,
                "folders": folders[:300], "files": files[:300]}

    def sources(self):
        with self.connection() as con:
            return [dict(r) for r in con.execute("SELECT * FROM sources ORDER BY id DESC")]

    def jobs(self, limit=120):
        with self.connection() as con:
            rows = con.execute("""SELECT id,path,name,source_id,bytes,digest,status,attempts,
                                    retry_at,error,duplicate_of,updated_at
                                    FROM jobs ORDER BY id DESC LIMIT ?""", (min(500, max(1, int(limit))),))
            return [dict(row) for row in rows]

    def status(self):
        with self.connection() as con:
            rows = con.execute("SELECT status,COUNT(*) n,COALESCE(SUM(bytes),0) size FROM jobs GROUP BY status")
            counts = {r["status"]: {"count": r["n"], "bytes": r["size"]} for r in rows}
        credentials_present = self.credentials.is_file() and self.credentials.stat().st_mode & 0o077 == 0
        cli_present = self.cli.is_file()
        return {"monitor_enabled": self.monitor_enabled, "upload_enabled": self.upload_enabled,
                "allow_real_upload": self.allow_upload, "credentials_present": credentials_present,
                "cli_present": cli_present, "scan_running": self.scan_running,
                "last_scan": self.last_scan, "last_scan_error": self.last_scan_error,
                "sources": self.sources(), "counts": counts,
                "cloud_verification": "not_available",
                "progress_source": "API status only; no confirmed byte-level upload %"}

    def add_source(self, raw, kind):
        if kind not in ("directory", "file"):
            raise UserError("来源类型无效")
        path = str(self._path(raw, kind=kind))
        with self.connection() as con:
            con.execute("""INSERT INTO sources(path,kind,enabled,added_at) VALUES(?,?,1,?)
                           ON CONFLICT(path) DO UPDATE SET kind=excluded.kind,enabled=1""",
                        (path, kind, now()))
        self.request_scan()
        return self.sources()

    def _set_source(self, source_id, enabled=None, remove=False):
        with self.connection() as con:
            row = con.execute("SELECT id FROM sources WHERE id=?", (source_id,)).fetchone()
            if not row:
                raise UserError("该来源不存在")
            if remove:
                con.execute("DELETE FROM sources WHERE id=?", (source_id,))
                # Preserve job history; stop uploading queued work for a removed source.
                con.execute("""UPDATE jobs SET status='paused',updated_at=?
                               WHERE source_id=? AND status IN ('queued','retrying')""", (now(), source_id))
            else:
                con.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id))
                if not enabled:
                    con.execute("""UPDATE jobs SET status='paused',updated_at=?
                                   WHERE source_id=? AND status IN ('queued','retrying')""", (now(), source_id))
                else:
                    con.execute("""UPDATE jobs SET status='queued',updated_at=?
                                   WHERE source_id=? AND status='paused'""", (now(), source_id))
        if enabled:
            self.request_scan()

    def _add_job(self, source_id, file):
        if not self._eligible(file):
            return
        try:
            before = file.stat()
            size = before.st_size
            mtime_ns = before.st_mtime_ns
            with self.connection() as con:
                cached = con.execute("SELECT bytes,mtime_ns FROM jobs WHERE path=?", (str(file),)).fetchone()
                if cached and cached["bytes"] == size and cached["mtime_ns"] == mtime_ns:
                    return  # Keep recurring scans cheap even with multi-GB media.
            digest = self._digest(file)
            after = file.stat()
            if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
                return  # Never enqueue an incomplete/changing file.
        except OSError:
            return
        path = str(file)
        with self.connection() as con:
            previous = con.execute("SELECT id,bytes,digest,status FROM jobs WHERE path=?", (path,)).fetchone()
            if previous and previous["bytes"] == size and previous["digest"] == digest:
                return
            accepted = con.execute("""SELECT id FROM jobs WHERE digest=? AND status='api_accepted'
                                      ORDER BY id LIMIT 1""", (digest,)).fetchone()
            state = "duplicate" if accepted else "queued"
            if previous:
                con.execute("""UPDATE jobs SET source_id=?,name=?,bytes=?,digest=?,mtime_ns=?,status=?,attempts=0,
                                retry_at=0,error='',media_key='',duplicate_of=?,updated_at=? WHERE id=?""",
                            (source_id, file.name, size, digest, mtime_ns, state,
                             accepted["id"] if accepted else None, now(), previous["id"]))
            else:
                con.execute("""INSERT INTO jobs
                               (path,name,source_id,bytes,digest,mtime_ns,status,duplicate_of,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?)""",
                            (path, file.name, source_id, size, digest, mtime_ns, state,
                             accepted["id"] if accepted else None, now()))

    def scan(self):
        if not self.scan_lock.acquire(blocking=False):
            return False
        self.scan_running = True
        self.last_scan_error = None
        try:
            seen = 0
            for source in self.sources():
                if not source["enabled"]:
                    continue
                path = Path(source["path"])
                if not path.exists():
                    self.last_scan_error = "部分来源暂不可用（可能是已卸载的外置磁盘）"
                    continue
                if source["kind"] == "file":
                    self._add_job(source["id"], path)
                    seen += 1
                    continue
                for root, dirs, files in os.walk(path, followlinks=False):
                    dirs[:] = [d for d in dirs if not d.startswith(".")
                               and not (Path(root) / d).is_symlink()
                               and not d.endswith((".app", ".photoslibrary"))]
                    for name in files:
                        if name.startswith("."):
                            continue
                        file = Path(root) / name
                        if file.suffix.lstrip(".").lower() in MEDIA:
                            self._add_job(source["id"], file)
                        seen += 1
                        if seen >= MAX_SCAN_FILES:
                            self.last_scan_error = f"扫描上限 {MAX_SCAN_FILES} 项，请缩小监控范围"
                            break
                    if seen >= MAX_SCAN_FILES:
                        break
                if seen >= MAX_SCAN_FILES:
                    break
            self.last_scan = now()
            return True
        except Exception as exc:
            self.last_scan_error = "扫描错误：" + type(exc).__name__
            return False
        finally:
            self.scan_running = False
            self.scan_lock.release()

    def request_scan(self):
        if not self.scan_running:
            threading.Thread(target=self.scan, daemon=True, name="gu-scan").start()

    def set_monitor(self, enabled):
        self.monitor_enabled = bool(enabled)
        if enabled:
            self.request_scan()
        self.wake_event.set()

    def _auth_ready(self):
        return (self.allow_upload and self.cli.is_file() and self.credentials.is_file()
                and self.credentials.stat().st_mode & 0o077 == 0)

    def set_upload(self, enabled):
        if enabled and not self._auth_ready():
            raise UserError("真实上传未开放：需显式启用服务器上传权限，并在 WebUI 独立状态目录完成账号授权")
        if enabled:
            if self.cli.resolve() != (ROOT / "gotohp-cli-macos-universal").resolve():
                raise UserError("仅允许使用已确认的本机 gotohp 上传引擎路径")
            if self._digest(self.cli) != PINNED_MAC_CLI_SHA256:
                raise UserError("gotohp 官方发布文件 SHA-256 校验失败，拒绝上传")
        self.upload_enabled = bool(enabled)
        self.wake_event.set()

    def retry(self, job_id):
        with self.connection() as con:
            row = con.execute("SELECT source_id,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in ("failed", "retrying"):
                raise UserError("只有失败或等待重试的任务可重新入队")
            active = con.execute("SELECT enabled FROM sources WHERE id=?", (row["source_id"],)).fetchone()
            if not active or not active["enabled"]:
                raise UserError("请先恢复此任务的来源目录")
            con.execute("""UPDATE jobs SET status='queued',attempts=0,retry_at=0,error='',
                           updated_at=? WHERE id=?""", (now(), job_id))
        self.wake_event.set()

    def _next_job(self):
        with self.connection() as con:
            return con.execute("""SELECT j.* FROM jobs j
                 JOIN sources s ON s.id=j.source_id AND s.enabled=1
                 WHERE j.status='queued' OR (j.status='retrying' AND j.retry_at<=?)
                 ORDER BY j.id ASC LIMIT 1""", (time.time(),)).fetchone()

    def _run_upload(self, job):
        """Only called when explicit credentials, env gate and UI enable all hold."""
        path = Path(job["path"])
        if not self._eligible(path):
            return False, "", "源文件消失或无效"
        if self._digest(path) != job["digest"]:
            return False, "", "源文件内容变化，请重新扫描"
        timeout = min(21600, max(600, job["bytes"] // 51200))
        cmd = [str(self.cli), "--config", str(self.credentials),
               "upload", "--no-tui", "--saver", str(path)]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, "", "上传进程异常：" + type(exc).__name__
        if result.returncode != 0:
            return False, "", f"上传进程退出码 {result.returncode}（详情请在本机 CLI 排查）"
        # Do not expose raw stdout (could hold private provider metadata).
        output = result.stdout
        if re.search(r'"(?:error|message)"\s*:\s*"(?!")', output):
            return False, "", "API 返回错误"
        key = re.search(r'"mediaKey"\s*:\s*"([^"]{1,500})"', output)
        if not key:
            return False, "", "缺少可核对的 mediaKey，未登记为成功"
        return True, key.group(1), ""

    def run_one(self):
        if not (self.upload_enabled and self._auth_ready()):
            return False
        job = self._next_job()
        if job is None:
            return False
        with self.connection() as con:
            dupe = con.execute("""SELECT id FROM jobs WHERE digest=? AND status='api_accepted'
                                  AND id!=? LIMIT 1""", (job["digest"], job["id"])).fetchone()
            if dupe:
                con.execute("UPDATE jobs SET status='duplicate',duplicate_of=?,updated_at=? WHERE id=?",
                            (dupe["id"], now(), job["id"]))
                return True
            con.execute("UPDATE jobs SET status='uploading',updated_at=? WHERE id=?", (now(), job["id"]))
        ok, media_key, error = self._run_upload(job)
        with self.connection() as con:
            if ok:
                con.execute("""UPDATE jobs SET status='api_accepted',media_key=?,error='',updated_at=?
                               WHERE id=?""", (media_key, now(), job["id"]))
                con.execute("""UPDATE jobs SET status='duplicate',duplicate_of=?,updated_at=?
                               WHERE digest=? AND status IN ('queued','retrying')
                               AND id!=?""", (job["id"], now(), job["digest"], job["id"]))
            else:
                attempts = job["attempts"] + 1
                status = "failed" if attempts >= 3 else "retrying"
                retry_at = time.time() + min(3600, 30 * 2 ** attempts)
                con.execute("""UPDATE jobs SET status=?,attempts=?,retry_at=?,error=?,updated_at=?
                               WHERE id=?""", (status, attempts, retry_at, error, now(), job["id"]))
        return True

    def _worker_loop(self):
        next_poll = 0
        while not self.stop_event.is_set():
            if self.monitor_enabled and time.time() >= next_poll:
                self.request_scan()
                next_poll = time.time() + POLL_INTERVAL
            if self.upload_enabled and self._auth_ready():
                if self.run_one():
                    continue
            self.wake_event.wait(timeout=2)
            self.wake_event.clear()

    def close(self):
        self.stop_event.set()
        self.wake_event.set()
        if self.thread is not None:
            self.thread.join(timeout=3)


def make_handler(store):
    csrf = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        server_version = "GoogleUnlimited-Local/1.0"

        def _allowed_host(self):
            host = self.headers.get("Host", "").split(":")[0].lower()
            return host in ("127.0.0.1", "localhost")

        def _send(self, code, payload, *, mime="application/json; charset=utf-8"):
            data = (json.dumps(payload, ensure_ascii=False).encode("utf-8")
                    if mime.startswith("application/json") else payload)
            self.send_response(code)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self'; style-src 'self';"
                             " connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(data)

        def _json(self, code, value):
            self._send(code, value)

        def do_GET(self):
            if not self._allowed_host():
                return self._json(403, {"error": "invalid host"})
            uri = urlsplit(self.path)
            if uri.path in ("/", "/index.html", "/app.js", "/style.css"):
                name = "index.html" if uri.path == "/" else uri.path.lstrip("/")
                types = {".html": "text/html; charset=utf-8",
                         ".js": "application/javascript; charset=utf-8",
                         ".css": "text/css; charset=utf-8"}
                file = STATIC / name
                if not file.is_file():
                    return self._json(404, {"error": "not found"})
                return self._send(200, file.read_bytes(), mime=types[file.suffix])
            if uri.path == "/api/status":
                return self._json(200, {**store.status(), "csrf": csrf})
            if uri.path == "/api/jobs":
                return self._json(200, {"jobs": store.jobs()})
            if uri.path == "/api/browse":
                try:
                    path = parse_qs(uri.query).get("path", [None])[0]
                    return self._json(200, store.browse(path))
                except UserError as exc:
                    return self._json(400, {"error": str(exc)})
            return self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed_host():
                return self._json(403, {"error": "invalid host"})
            origin = self.headers.get("Origin")
            if origin and origin != f"http://{self.headers.get('Host')}":
                return self._json(403, {"error": "origin mismatch"})
            if not secrets.compare_digest(self.headers.get("X-GU-CSRF", ""), csrf):
                return self._json(403, {"error": "CSRF token required"})
            try:
                n = int(self.headers.get("Content-Length", "0"))
                if n <= 0 or n > MAX_REQUEST_BYTES:
                    return self._json(413, {"error": "invalid request length"})
                payload = json.loads(self.rfile.read(n))
                if not isinstance(payload, dict):
                    raise UserError("请求格式无效")
                uri = urlsplit(self.path).path
                if uri == "/api/sources":
                    result = {"sources": store.add_source(payload.get("path"), payload.get("kind"))}
                elif uri == "/api/source/toggle":
                    store._set_source(int(payload["id"]), enabled=payload.get("enabled") is True)
                    result = {"sources": store.sources()}
                elif uri == "/api/source/remove":
                    store._set_source(int(payload["id"]), remove=True)
                    result = {"sources": store.sources()}
                elif uri == "/api/monitor":
                    store.set_monitor(payload.get("enabled") is True)
                    result = {"monitor_enabled": store.monitor_enabled}
                elif uri == "/api/upload":
                    store.set_upload(payload.get("enabled") is True)
                    result = {"upload_enabled": store.upload_enabled}
                elif uri == "/api/scan":
                    store.request_scan()
                    result = {"scan_queued": True}
                elif uri == "/api/retry":
                    store.retry(int(payload["id"]))
                    result = {"retry_queued": True}
                else:
                    return self._json(404, {"error": "not found"})
                return self._json(200, result)
            except (UserError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                return self._json(400, {"error": str(exc)})
            except Exception as exc:
                print(f"WebUI error: {type(exc).__name__}", flush=True)
                return self._json(500, {"error": "内部错误，请查看本地日志"})

        def log_message(self, fmt, *args):
            # Never log full URL paths (they may contain personal filenames).
            if args and isinstance(args[0], str):
                print(f"WebUI HTTP {args[1] if len(args)>1 else ''}", flush=True)

    return Handler


def main():
    parser = argparse.ArgumentParser(description="GoogleUnlimited local-only dashboard")
    parser.add_argument("--port", type=int, default=8796)
    parser.add_argument("--state-dir", default=str(DEFAULT_STATE))
    parser.add_argument("--allow-real-upload", action="store_true",
                        help="Enable manual upload switch after independent Google credentials setup")
    args = parser.parse_args()
    store = Store(args.state_dir, allow_upload=args.allow_real_upload)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(store))
    print(f"GoogleUnlimited WebUI: http://127.0.0.1:{server.server_port}", flush=True)
    print("Upload initially OFF; monitoring initially OFF; independent state only.", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        store.close()


if __name__ == "__main__":
    main()

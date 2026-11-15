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
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from engine.verify_local_build import verify as verify_owned_engine
STATIC = Path(__file__).resolve().parent
DEFAULT_STATE = STATIC / "state"
MEDIA = frozenset(("jpg jpeg png heic heif webp gif bmp tiff tif avif dng raw cr2 cr3 nef arw orf rw2 pef srw raf mp4 mov mkv avi m4v 3gp 3g2 wmv webm mpg mpeg mts m2ts ts flv vob").split())
VIDEO_EXTS = frozenset("mp4 mov mkv avi m4v 3gp 3g2 wmv webm mpg mpeg mts m2ts ts flv vob".split())
IMAGE_EXTS = MEDIA - VIDEO_EXTS
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

    def __init__(self, state_dir=None, *, allow_upload=False, cli=None,
                 engine="release", worker=True):
        if engine not in ("release", "owned"):
            raise UserError("上传引擎必须为 release 或 owned")
        self.state_dir = Path(state_dir or DEFAULT_STATE).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.state_dir.chmod(0o700)
        self.db = self.state_dir / "queue.sqlite3"
        self.credentials = self.state_dir / "gotohp.config"
        self.engine = engine
        self.cli = Path(cli or (
            ROOT / "engine/bin/gotohp-cli-owned-arm64" if engine == "owned"
            else ROOT / "gotohp-cli-macos-universal"))
        self.allow_upload = allow_upload
        self.upload_enabled = False
        self.monitor_enabled = False
        self.auto_sync_requested = False
        self.lock = threading.RLock()
        self.scan_lock = threading.Lock()
        self.scan_request_lock = threading.Lock()
        self.scan_pending = False
        self.scan_thread_running = False
        self.scan_thread = None
        self.scan_running = False
        self.last_scan = None
        self.last_scan_error = None
        self.stop_event = threading.Event()
        self.wake_event = threading.Event()
        self._initialize()
        # Only restore an explicitly enabled, locally authorized sync policy.
        # A user stop remains stopped across service restart and disk remount.
        with self.connection() as con:
            setting = con.execute("SELECT value FROM settings WHERE key='auto_sync'").fetchone()
            self.auto_sync_requested = bool(setting and setting["value"] == "1")
        if self.auto_sync_requested and worker:
            try:
                self._ensure_upload_ready()
            except UserError:
                pass  # Fail closed if the account or pinned engine is unavailable.
            else:
                self.monitor_enabled = True
                self.upload_enabled = True
        # Rebuild historical summaries from SQLite alone (no disk or NAS reads).
        with self.connection() as con:
            source_ids = [row["id"] for row in con.execute("SELECT id FROM sources")]
        for source_id in source_ids:
            self._rebuild_folder_index(source_id)
        self.thread = None
        if worker:
            self.thread = threading.Thread(target=self._worker_loop, daemon=True, name="gu-webui-worker")
            self.thread.start()
            if self.monitor_enabled:
                self.request_scan()

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
                added_at TEXT NOT NULL,
                archived INTEGER NOT NULL DEFAULT 0,
                location_kind TEXT NOT NULL DEFAULT 'local',
                mount_root TEXT NOT NULL DEFAULT '',
                availability TEXT NOT NULL DEFAULT 'unknown',
                last_checked_at TEXT NOT NULL DEFAULT '',
                ui_visibility TEXT NOT NULL DEFAULT 'visible',
                album_name TEXT NOT NULL DEFAULT '',
                album_key TEXT NOT NULL DEFAULT '',
                album_confirmed INTEGER NOT NULL DEFAULT 0,
                album_confirmed_at TEXT NOT NULL DEFAULT '',
                upload_quality TEXT NOT NULL DEFAULT 'storage_saver'
              );
              CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                path TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                source_id INTEGER NOT NULL,
                bytes INTEGER NOT NULL,
                digest TEXT NOT NULL,
                mtime_ns INTEGER NOT NULL DEFAULT 0,
                media_kind TEXT NOT NULL DEFAULT '',
                extension TEXT NOT NULL DEFAULT '',
                first_seen_at TEXT NOT NULL DEFAULT '',
                last_seen_at TEXT NOT NULL DEFAULT '',
                last_scan_token TEXT NOT NULL DEFAULT '',
                file_available INTEGER NOT NULL DEFAULT 1,
                cloud_verified_at TEXT NOT NULL DEFAULT '',
                cloud_verify_method TEXT NOT NULL DEFAULT '',
                ctime_ns INTEGER NOT NULL DEFAULT 0,
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
              CREATE TABLE IF NOT EXISTS folder_index (
                source_id INTEGER NOT NULL,
                path TEXT NOT NULL,
                file_count INTEGER NOT NULL,
                image_count INTEGER NOT NULL,
                video_count INTEGER NOT NULL,
                total_bytes INTEGER NOT NULL,
                accepted_count INTEGER NOT NULL,
                verified_count INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                missing_count INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(source_id,path)
              );
              CREATE TABLE IF NOT EXISTS source_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                created_at TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT ''
              );
              CREATE INDEX IF NOT EXISTS idx_source_events_id
                ON source_events(source_id,id);
              CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
              );
            """)
            source_cols = {
                "archived": "INTEGER NOT NULL DEFAULT 0",
                "location_kind": "TEXT NOT NULL DEFAULT 'local'",
                "mount_root": "TEXT NOT NULL DEFAULT ''",
                "availability": "TEXT NOT NULL DEFAULT 'unknown'",
                "last_checked_at": "TEXT NOT NULL DEFAULT ''",
                "ui_visibility": "TEXT NOT NULL DEFAULT 'visible'",
                "album_name": "TEXT NOT NULL DEFAULT ''",
                "album_key": "TEXT NOT NULL DEFAULT ''",
                "album_confirmed": "INTEGER NOT NULL DEFAULT 0",
                "album_confirmed_at": "TEXT NOT NULL DEFAULT ''",
                "upload_quality": "TEXT NOT NULL DEFAULT 'storage_saver'",
            }
            job_cols = {
                "mtime_ns": "INTEGER NOT NULL DEFAULT 0",
                "media_kind": "TEXT NOT NULL DEFAULT ''",
                "extension": "TEXT NOT NULL DEFAULT ''",
                "first_seen_at": "TEXT NOT NULL DEFAULT ''",
                "last_seen_at": "TEXT NOT NULL DEFAULT ''",
                "last_scan_token": "TEXT NOT NULL DEFAULT ''",
                "file_available": "INTEGER NOT NULL DEFAULT 1",
                "cloud_verified_at": "TEXT NOT NULL DEFAULT ''",
                "cloud_verify_method": "TEXT NOT NULL DEFAULT ''",
                "ctime_ns": "INTEGER NOT NULL DEFAULT 0",
            }
            for table, columns in (("sources", source_cols), ("jobs", job_cols)):
                existing = {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}
                for column, definition in columns.items():
                    if column not in existing:
                        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
            # Existing sources receive a suggested album name without authorizing
            # any upload. Preserve a previously confirmed custom album name.
            for row in con.execute("SELECT id,path,album_name FROM sources WHERE album_name=''"):
                con.execute("UPDATE sources SET album_name=? WHERE id=?",
                            (Path(row["path"]).name, row["id"]))
            # Migrate pre-WebUI-upgrade jobs in place. Never erase their status.
            older = con.execute("""SELECT id,name,updated_at FROM jobs
                                   WHERE media_kind='' OR first_seen_at=''""").fetchall()
            for job in older:
                ext = Path(job["name"]).suffix.lstrip(".").lower()
                kind = "video" if ext in VIDEO_EXTS else "image" if ext in IMAGE_EXTS else "unknown"
                con.execute("""UPDATE jobs SET media_kind=?,extension=?,
                               first_seen_at=CASE WHEN first_seen_at='' THEN ? ELSE first_seen_at END,
                               last_seen_at=CASE WHEN last_seen_at='' THEN ? ELSE last_seen_at END
                               WHERE id=?""",
                            (kind, ext, job["updated_at"], job["updated_at"], job["id"]))
            # Crash after an API request may have already uploaded the file.
            # Require an explicit human decision before retrying such jobs.
            con.execute("""UPDATE jobs SET status='interrupted',
                           error='上次上传进程中断，云端结果未知。请人工核对后决定是否重试',
                           updated_at=? WHERE status='uploading'""", (now(),))
            # Old versions physically deleted source rows while retaining jobs.
            # Restore the *source metadata only* as an archived, disabled source.
            # This preserves the original source IDs, media ledger and progress
            # without reading files, re-scanning, or starting any upload.
            orphans = con.execute("""SELECT DISTINCT j.source_id FROM jobs j
                        LEFT JOIN sources s ON s.id=j.source_id
                        WHERE s.id IS NULL ORDER BY j.source_id""").fetchall()
            for orphan in orphans:
                source_id = orphan["source_id"]
                paths = [row["path"] for row in con.execute(
                    "SELECT path FROM jobs WHERE source_id=?", (source_id,))]
                if not paths:
                    continue
                if len(paths) == 1:
                    recovered_path = str(Path(paths[0]))
                    kind = "file"
                else:
                    recovered_path = os.path.commonpath(paths)
                    kind = "directory"
                try:
                    con.execute("""INSERT INTO sources
                           (id,path,kind,enabled,added_at,archived,location_kind,
                            mount_root,availability,last_checked_at)
                           VALUES(?,?,?,0,?,1,'local','','archived',?)""",
                                (source_id, recovered_path, kind, now(), now()))
                except sqlite3.IntegrityError:
                    # A collision with another active source must not overwrite
                    # either source or change historic backup results.
                    print("WebUI: overlapping legacy source path needs manual review",
                          flush=True)
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
    def _mounted_volumes():
        """Read macOS mount table only. Never mount volumes or read credentials."""
        volumes = []
        if sys.platform != "darwin":
            return volumes
        try:
            result = subprocess.run(["/sbin/mount"], capture_output=True, text=True,
                                    timeout=5, check=False)
            if result.returncode:
                return volumes
        except (OSError, subprocess.TimeoutExpired):
            return volumes
        network_fs = {"smbfs", "nfs", "afpfs", "webdav", "fuse.sshfs", "macfuse"}
        for line in result.stdout.splitlines():
            match = re.match(r"^.*? on (.+?) \(([^,)]+)", line)
            if not match:
                continue
            mountpoint = Path(match.group(1))
            if not mountpoint.is_absolute() or mountpoint.is_symlink():
                continue
            fs_type = match.group(2).strip().lower()
            if not (contained(mountpoint, Path("/Volumes")) or
                    contained(mountpoint, Path.home())):
                continue
            if fs_type not in network_fs:
                continue
            volumes.append({"name": mountpoint.name, "path": str(mountpoint),
                            "fs_type": fs_type, "kind": "network", "mounted": True})
        return volumes

    def volumes(self):
        network = self._mounted_volumes()
        network_paths = {v["path"] for v in network}
        local = []
        root = Path("/Volumes")
        try:
            for item in root.iterdir():
                if (item.name.startswith(".") or item.is_symlink() or
                        str(item) in network_paths or not item.is_dir()):
                    continue
                local.append({"name": item.name, "path": str(item),
                              "fs_type": "local/other", "kind": "local", "mounted": True})
        except OSError:
            pass
        return {"network": network, "local": sorted(local, key=lambda v: v["name"].lower())}

    def connect_share(self, raw):
        """Ask macOS Finder to mount SMB; macOS owns all authentication dialogs."""
        if sys.platform != "darwin":
            raise UserError("连接共享需在 Mac 上运行；Linux 可先在系统中挂载")
        if not isinstance(raw, str) or len(raw) > 2048:
            raise UserError("请输入有效的 SMB 共享地址")
        try:
            uri = urlsplit(raw.strip())
            if (uri.scheme.lower() != "smb" or not uri.hostname or uri.username or
                    uri.password or uri.query or uri.fragment or "@" in uri.netloc):
                raise UserError("仅支持不含账号密码的 smb://服务器/共享目录 地址")
        except ValueError as exc:
            raise UserError("SMB 地址格式不正确") from exc
        try:
            subprocess.Popen(["/usr/bin/open", "-a", "Finder", raw.strip()],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, close_fds=True)
        except OSError as exc:
            raise UserError("无法请求 Finder 连接共享，请在 Finder 中手动连接服务器") from exc
        return {"requested": True, "message": "已请求 Finder 连接。请在 macOS 窗口完成授权，然后刷新网络磁盘列表；尚未确认挂载成功"}

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

    def sources(self, include_hidden=True):
        with self.connection() as con:
            rows = [dict(r) for r in con.execute("""SELECT s.*,
                       (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id) AS indexed_files,
                       (SELECT COUNT(*) FROM folder_index f WHERE f.source_id=s.id) AS indexed_folders,
                       (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id AND j.media_kind='image') AS images,
                       (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id AND j.media_kind='video') AS videos,
                       (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id AND j.status='api_accepted') AS api_accepted,
                       (SELECT COUNT(*) FROM jobs j WHERE j.source_id=s.id AND j.cloud_verified_at!='') AS cloud_verified
                       FROM sources s WHERE (?=1 OR s.ui_visibility='visible')
                       ORDER BY s.archived,s.id DESC""", (int(include_hidden),))]
        return rows

    def source_events(self, source_id):
        with self.connection() as con:
            source = con.execute("SELECT id FROM sources WHERE id=?", (int(source_id),)).fetchone()
            if not source:
                raise UserError("历史来源不存在")
            return [dict(row) for row in con.execute(
                """SELECT id,source_id,action,created_at,note FROM source_events
                   WHERE source_id=? ORDER BY id DESC LIMIT 80""", (int(source_id),))]

    @staticmethod
    def _event(con, source_id, action, note=""):
        con.execute("""INSERT INTO source_events(source_id,action,created_at,note)
                       VALUES(?,?,?,?)""", (source_id, action, now(), note))

    def set_album(self, source_id, title):
        """Set and explicitly confirm one album for this source; never auto-upload."""
        if (not isinstance(title, str) or not 1 <= len(title.strip()) <= 120 or
                title != title.strip() or any(ord(c) < 32 or ord(c) == 127 for c in title)):
            raise UserError("相册名称必须为 1–120 个非控制字符，不能有首尾空格")
        if self.upload_enabled:
            raise UserError("请先关闭真实上传开关，然后再调整相册名称")
        with self.connection() as con:
            source = con.execute("SELECT id,album_name,album_key FROM sources WHERE id=?",
                                 (int(source_id),)).fetchone()
            if not source:
                raise UserError("来源不存在")
            if con.execute("""SELECT 1 FROM jobs WHERE source_id=? AND status='uploading'
                              LIMIT 1""", (int(source_id),)).fetchone():
                raise UserError("来源有进行中的上传，请在结束后修改相册")
            if source["album_key"] and source["album_name"] != title:
                raise UserError("已绑定云端相册 ID，不能仅改本地名称而创建重复相册")
            con.execute("""UPDATE sources SET album_name=?,album_confirmed=1,
                           album_confirmed_at=? WHERE id=?""",
                        (title, now(), int(source_id)))
            self._event(con, int(source_id), "album_confirmed",
                        "用户确认相册名：" + title)
        return self.sources()

    @staticmethod
    def _album_key(value):
        """Accept a Google Photos existing album URL or key, never an album title."""
        if not isinstance(value, str):
            raise UserError("请输入已有相册的 Google Photos 链接或 ID")
        value = value.strip()
        if value.startswith("https://photos.google.com/album/"):
            parsed = urlsplit(value)
            value = parsed.path.removeprefix("/album/")
        if not re.fullmatch(r"AF1Qip[A-Za-z0-9_-]{8,500}", value):
            raise UserError("相册 ID 无效，请在 Google Photos 打开目标相册复制链接")
        return value

    def bind_album(self, source_id, album_ref):
        """Explicitly reuse an existing album. Never moves or deletes cloud media."""
        key = self._album_key(album_ref)
        if self.upload_enabled:
            raise UserError("请先停止自动同步，再绑定相册")
        with self.connection() as con:
            source = con.execute(
                "SELECT id,album_name,album_key FROM sources WHERE id=?",
                (int(source_id),)).fetchone()
            if not source or not source["album_name"]:
                raise UserError("请先设置相册名称")
            if con.execute("""SELECT 1 FROM jobs WHERE source_id=? AND status='uploading'
                              LIMIT 1""", (int(source_id),)).fetchone():
                raise UserError("等待当前文件上传完成后再绑定相册")
            if source["album_key"] and source["album_key"] != key:
                raise UserError("已绑定另一个相册，不能直接覆盖，避免文件分散")
            con.execute("""UPDATE sources SET album_key=?,album_confirmed=1,
                           album_confirmed_at=? WHERE id=?""",
                        (key, now(), int(source_id)))
            self._event(con, int(source_id), "album_bound", "已绑定现有 Google Photos 相册")
        self.wake_event.set()
        return self.sources()

    def set_upload_quality(self, source_id, quality):
        """Change the upload quality for future media only."""
        if quality not in ("storage_saver", "original"):
            raise UserError("仅支持 Storage saver 或 Original quality")
        if self.upload_enabled:
            raise UserError("请先停止自动同步，再调整画质")
        with self.connection() as con:
            source = con.execute("SELECT upload_quality FROM sources WHERE id=?",
                                 (int(source_id),)).fetchone()
            if not source:
                raise UserError("来源不存在")
            if con.execute("""SELECT 1 FROM jobs WHERE source_id=? AND status='uploading'
                              LIMIT 1""", (int(source_id),)).fetchone():
                raise UserError("当前文件正在上传，请上传结束后再修改画质")
            if source["upload_quality"] != quality:
                con.execute("UPDATE sources SET upload_quality=? WHERE id=?",
                            (quality, int(source_id)))
                self._event(con, int(source_id), "upload_quality", "后续上传画质：" + quality)
        return self.sources()

    def set_display(self, source_id, mode):
        if mode not in ("visible", "hidden", "removed"):
            raise UserError("无效的显示操作")
        with self.connection() as con:
            row = con.execute("SELECT id,ui_visibility FROM sources WHERE id=?",
                              (int(source_id),)).fetchone()
            if not row:
                raise UserError("来源不存在")
            if row["ui_visibility"] == mode:
                pass
            elif mode == "removed":
                # Detach source without touching any job's progress or its files.
                con.execute("""UPDATE sources SET ui_visibility='removed',archived=1,
                               enabled=0,availability='archived',last_checked_at=? WHERE id=?""",
                            (now(), int(source_id)))
            else:
                con.execute("UPDATE sources SET ui_visibility=? WHERE id=?",
                            (mode, int(source_id)))
            if row["ui_visibility"] != mode:
                self._event(con, int(source_id), mode, "界面操作；文件及任务历史未删除")
        return self.sources()

    @staticmethod
    def _jobs_filter(source_id=None, kind=None, status=None, include_hidden=True):
        query = " FROM jobs WHERE 1=1"
        params = []
        if not include_hidden:
            query += """ AND source_id IN
                     (SELECT id FROM sources WHERE ui_visibility='visible')"""
        if source_id is not None:
            query += " AND source_id=?"
            params.append(int(source_id))
        if kind in ("image", "video"):
            query += " AND media_kind=?"
            params.append(kind)
        if status in ("queued", "uploading", "retrying", "failed", "interrupted", "paused",
                      "api_accepted", "duplicate"):
            query += " AND status=?"
            params.append(status)
        return query, params

    def jobs_total(self, source_id=None, kind=None, status=None, include_hidden=True):
        clause, params = self._jobs_filter(source_id, kind, status, include_hidden)
        with self.connection() as con:
            return con.execute("SELECT COUNT(*)" + clause, params).fetchone()[0]

    def jobs(self, limit=120, source_id=None, kind=None, status=None,
             offset=0, include_hidden=True):
        query = """SELECT id,path,name,source_id,bytes,digest,status,attempts,
                   media_kind,extension,first_seen_at,last_seen_at,
                   file_available,cloud_verified_at,cloud_verify_method,
                   retry_at,error,duplicate_of,updated_at"""
        clause, params = self._jobs_filter(source_id, kind, status, include_hidden)
        query += clause + " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend((min(500, max(1, int(limit))), min(1000000, max(0, int(offset)))))
        with self.connection() as con:
            rows = con.execute(query, params)
            return [dict(row) for row in rows]

    def folders(self, source_id):
        with self.connection() as con:
            return [dict(row) for row in con.execute("""SELECT * FROM folder_index
                       WHERE source_id=? ORDER BY path LIMIT 500""", (int(source_id),))]

    def status(self, include_hidden=True):
        with self.connection() as con:
            scope = ("" if include_hidden else
                     " WHERE source_id IN (SELECT id FROM sources WHERE ui_visibility='visible')")
            rows = con.execute("""SELECT status,COUNT(*) n,COALESCE(SUM(bytes),0) size
                                  FROM jobs""" + scope + " GROUP BY status")
            counts = {r["status"]: {"count": r["n"], "bytes": r["size"]} for r in rows}
            indexed = con.execute("""SELECT COUNT(*) files,
                            SUM(CASE WHEN media_kind='image' THEN 1 ELSE 0 END) images,
                            SUM(CASE WHEN media_kind='video' THEN 1 ELSE 0 END) videos,
                            SUM(CASE WHEN cloud_verified_at!='' THEN 1 ELSE 0 END) manually_verified,
                            COALESCE(SUM(bytes),0) bytes
                            FROM jobs""" + scope).fetchone()
            hidden_sources = con.execute("""SELECT COUNT(*) FROM sources
                                 WHERE ui_visibility!='visible'""").fetchone()[0]
            stored_files = con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0]
        credentials_present = self.credentials.is_file() and self.credentials.stat().st_mode & 0o077 == 0
        cli_present = self.cli.is_file()
        owned_verified = False
        if self.engine == "owned" and cli_present:
            owned_verified, _ = verify_owned_engine(ROOT)
        return {"monitor_enabled": self.monitor_enabled, "upload_enabled": self.upload_enabled,
                "auto_sync_enabled": self.monitor_enabled and self.upload_enabled,
                "auto_sync_requested": self.auto_sync_requested,
                "allow_real_upload": self.allow_upload, "credentials_present": credentials_present,
                "cli_present": cli_present, "upload_engine": self.engine,
                "owned_build_verified": owned_verified,
                "scan_running": self.scan_running,
                "last_scan": self.last_scan, "last_scan_error": self.last_scan_error,
                "sources": self.sources(include_hidden=include_hidden),
                "hidden_sources": hidden_sources,
                "stored_files": stored_files,
                "counts": counts,
                "indexed": {k: indexed[k] or 0 for k in indexed.keys()},
                "supported_formats": {"image": sorted(IMAGE_EXTS), "video": sorted(VIDEO_EXTS)},
                "cloud_verification": "not_available",
                "progress_source": "API status only; no confirmed byte-level upload %"}

    def add_source(self, raw, kind):
        if kind not in ("directory", "file"):
            raise UserError("来源类型无效")
        path = str(self._path(raw, kind=kind))
        if path in (str(Path.home()), "/Volumes"):
            raise UserError("请选一个具体文件夹，而不是整个用户目录或磁盘根目录")
        network_matches = [v for v in self._mounted_volumes()
                           if contained(Path(path), Path(v["path"]))]
        matched = max(network_matches, key=lambda v: len(v["path"])) if network_matches else None
        with self.connection() as con:
            prior = con.execute("SELECT id,archived,ui_visibility FROM sources WHERE path=?",
                                (path,)).fetchone()
            con.execute("""INSERT INTO sources
                           (path,kind,enabled,added_at,archived,location_kind,mount_root,availability,last_checked_at)
                           VALUES(?,?,1,?,0,?,?, 'online',?)
                           ON CONFLICT(path) DO UPDATE SET
                           kind=excluded.kind,enabled=1,archived=0,ui_visibility='visible',
                           location_kind=excluded.location_kind,mount_root=excluded.mount_root,
                           availability='unknown',last_checked_at=excluded.last_checked_at""",
                        (path, kind, now(), "network" if matched else "local",
                         matched["path"] if matched else "", now()))
            source = con.execute("SELECT id FROM sources WHERE path=?", (path,)).fetchone()
            con.execute("""UPDATE sources SET album_name=? WHERE id=? AND album_name=''""",
                        (Path(path).name, source["id"]))
            # The folder name is a usable album name. New or re-added sources
            # never require a second confirmation click just to start syncing.
            con.execute("""UPDATE sources SET album_confirmed=1,album_confirmed_at=?
                           WHERE id=? AND album_confirmed=0""", (now(), source["id"]))
            # Explicit re-addition resumes saved paused tasks, preserving
            # accepted, duplicate, failed and manually-verified history.
            if prior:
                con.execute("""UPDATE jobs SET status='queued',updated_at=?
                               WHERE source_id=? AND status='paused'""",
                            (now(), source["id"]))
            self._event(con, source["id"],
                        "reload" if prior else "added",
                        "复用原索引与所有备份状态" if prior else "新增来源")
        # A re-addition refreshes the known path. Unchanged files are handled
        # by the cached size/mtime/ctime branch without rehashing the media.
        self.request_scan()
        self.wake_event.set()
        return self.sources()

    def _set_source(self, source_id, enabled=None, remove=False):
        with self.connection() as con:
            row = con.execute("SELECT id,archived FROM sources WHERE id=?", (source_id,)).fetchone()
            if not row:
                raise UserError("该来源不存在")
            if remove:
                # Backwards-compatible 'remove' means remove only from WebUI.
                con.execute("""UPDATE sources SET archived=1,enabled=0,
                               ui_visibility='removed',availability='archived',
                               last_checked_at=? WHERE id=?""",
                            (now(), source_id))
                self._event(con, source_id, "removed", "仅移除 WebUI 显示；保留全部原任务状态")
            else:
                if row["archived"]:
                    raise UserError("此来源已归档，请先恢复历史来源")
                con.execute("UPDATE sources SET enabled=? WHERE id=?", (int(enabled), source_id))
                if not enabled:
                    con.execute("""UPDATE jobs SET status='paused',updated_at=?
                                   WHERE source_id=? AND status IN ('queued','retrying')""", (now(), source_id))
                else:
                    con.execute("""UPDATE sources SET album_confirmed=1,album_confirmed_at=?
                                   WHERE id=? AND album_confirmed=0 AND album_name!=''""",
                                (now(), source_id))
                    con.execute("""UPDATE jobs SET status='queued',updated_at=?
                                   WHERE source_id=? AND status='paused'""", (now(), source_id))
        if enabled:
            self.request_scan()
            self.wake_event.set()

    def restore_source(self, source_id):
        with self.connection() as con:
            row = con.execute("SELECT path,kind FROM sources WHERE id=?", (int(source_id),)).fetchone()
            if not row:
                raise UserError("历史来源不存在")
        # Restore saved metadata immediately. Don't hash a huge 80+ GB volume
        # merely because the user opened its historical index.
        self._path(row["path"], kind=row["kind"])
        with self.connection() as con:
            con.execute("""UPDATE sources SET archived=0,enabled=1,ui_visibility='visible',
                           availability='unknown',
                           last_checked_at=? WHERE id=?""", (now(), source_id))
            con.execute("""UPDATE sources SET album_confirmed=1,album_confirmed_at=?
                           WHERE id=? AND album_confirmed=0 AND album_name!=''""",
                        (now(), source_id))
            con.execute("""UPDATE jobs SET status='queued',updated_at=?
                           WHERE source_id=? AND status='paused'""", (now(), source_id))
            self._event(con, source_id, "restored", "保留既有媒体索引和上传状态")
        self.request_scan()
        self.wake_event.set()
        return self.sources()

    def set_cloud_verified(self, job_id, verified):
        with self.connection() as con:
            row = con.execute("SELECT status FROM jobs WHERE id=?", (int(job_id),)).fetchone()
            if not row or row["status"] != "api_accepted":
                raise UserError("只有 API 已接收的任务可以在人工核对后标记云端确认")
            con.execute("""UPDATE jobs SET cloud_verified_at=?,cloud_verify_method=?,updated_at=?
                           WHERE id=?""",
                        (now() if verified else "", "manual" if verified else "", now(), job_id))
            source_id = con.execute("SELECT source_id FROM jobs WHERE id=?", (job_id,)).fetchone()[0]
        self._rebuild_folder_index(source_id)

    def _add_job(self, source_id, file, scan_token=""):
        if not self._eligible(file):
            return
        try:
            before = file.stat()
            size = before.st_size
            mtime_ns = before.st_mtime_ns
            ctime_ns = before.st_ctime_ns
            extension = file.suffix.lstrip(".").lower()
            media_kind = "video" if extension in VIDEO_EXTS else "image"
            with self.connection() as con:
                cached = con.execute("SELECT bytes,mtime_ns,ctime_ns FROM jobs WHERE path=?", (str(file),)).fetchone()
                if (cached and cached["bytes"] == size and cached["mtime_ns"] == mtime_ns
                        and cached["ctime_ns"] == ctime_ns):
                    con.execute("""UPDATE jobs SET source_id=?,last_seen_at=?,last_scan_token=?,
                                   file_available=1,media_kind=?,extension=? WHERE path=?""",
                                (source_id, now(), scan_token, media_kind, extension, str(file)))
                    return  # Keep recurring scans cheap even with multi-GB media.
            digest = self._digest(file)
            after = file.stat()
            if (before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                    or before.st_ctime_ns != after.st_ctime_ns):
                return  # Never enqueue an incomplete/changing file.
        except OSError:
            return
        path = str(file)
        with self.connection() as con:
            previous = con.execute("SELECT id,bytes,digest,status FROM jobs WHERE path=?", (path,)).fetchone()
            if previous and previous["bytes"] == size and previous["digest"] == digest:
                con.execute("""UPDATE jobs SET source_id=?,mtime_ns=?,ctime_ns=?,media_kind=?,extension=?,
                               last_seen_at=?,last_scan_token=?,file_available=1 WHERE id=?""",
                            (source_id, mtime_ns, ctime_ns, media_kind, extension,
                             now(), scan_token, previous["id"]))
                return
            accepted = con.execute("""SELECT id FROM jobs WHERE digest=? AND status='api_accepted'
                                      ORDER BY id LIMIT 1""", (digest,)).fetchone()
            state = "duplicate" if accepted else "queued"
            if previous:
                con.execute("""UPDATE jobs SET source_id=?,name=?,bytes=?,digest=?,mtime_ns=?,ctime_ns=?,
                                media_kind=?,extension=?,last_seen_at=?,last_scan_token=?,file_available=1,
                                cloud_verified_at='',cloud_verify_method='',status=?,attempts=0,
                                retry_at=0,error='',media_key='',duplicate_of=?,updated_at=? WHERE id=?""",
                            (source_id, file.name, size, digest, mtime_ns, ctime_ns, media_kind, extension,
                             now(), scan_token, state,
                             accepted["id"] if accepted else None, now(), previous["id"]))
            else:
                con.execute("""INSERT INTO jobs
                               (path,name,source_id,bytes,digest,mtime_ns,ctime_ns,media_kind,extension,
                                first_seen_at,last_seen_at,last_scan_token,file_available,
                                status,duplicate_of,updated_at)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,1,?,?,?)""",
                            (path, file.name, source_id, size, digest, mtime_ns, ctime_ns, media_kind,
                             extension, now(), now(), scan_token, state,
                             accepted["id"] if accepted else None, now()))

    def _rebuild_folder_index(self, source_id):
        """Persist per-directory historical counts without accessing source media."""
        with self.connection() as con:
            source = con.execute("SELECT path,kind FROM sources WHERE id=?",
                                 (source_id,)).fetchone()
            if not source:
                return
            root = Path(source["path"]) if source["kind"] == "directory" else Path(source["path"]).parent
            totals = {}
            entries = con.execute("""SELECT path,media_kind,bytes,status,cloud_verified_at,
                                    file_available FROM jobs WHERE source_id=?""", (source_id,))
            for entry in entries:
                parent = Path(entry["path"]).parent
                while contained(parent, root):
                    if str(parent) not in totals:
                        totals[str(parent)] = [0] * 8
                    a = totals[str(parent)]
                    a[0] += 1
                    a[1] += int(entry["media_kind"] == "image")
                    a[2] += int(entry["media_kind"] == "video")
                    a[3] += entry["bytes"]
                    a[4] += int(entry["status"] == "api_accepted")
                    a[5] += int(bool(entry["cloud_verified_at"]))
                    a[6] += int(entry["status"] in ("failed", "retrying"))
                    a[7] += int(not entry["file_available"])
                    if parent == root:
                        break
                    parent = parent.parent
            con.execute("DELETE FROM folder_index WHERE source_id=?", (source_id,))
            con.executemany("""INSERT INTO folder_index
                           (source_id,path,file_count,image_count,video_count,total_bytes,
                            accepted_count,verified_count,failed_count,missing_count,updated_at)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                            [(source_id, folder, *values, now()) for folder, values in totals.items()])

    def scan(self):
        # Explicit scans wait their turn; do not silently skip a disconnected
        # network share check because another scan happens to be running.
        self.scan_lock.acquire()
        self.scan_running = True
        self.last_scan_error = None
        try:
            seen = 0
            mounted = {v["path"] for v in self._mounted_volumes()}
            for source in self.sources():
                if not source["enabled"] or source["archived"]:
                    continue
                path = Path(source["path"])
                source_id = source["id"]
                remote_offline = (source["location_kind"] == "network" and
                                  source["mount_root"] not in mounted)
                if remote_offline or not path.exists():
                    self.last_scan_error = "部分来源暂不可用（网络共享或外置磁盘已断开），保留历史任务"
                    with self.connection() as con:
                        con.execute("""UPDATE sources SET availability='offline',last_checked_at=?
                                       WHERE id=?""", (now(), source_id))
                        con.execute("UPDATE jobs SET file_available=0 WHERE source_id=?", (source_id,))
                    self._rebuild_folder_index(source_id)
                    continue
                token = secrets.token_hex(12)
                complete = True
                def record_error(_error):
                    nonlocal complete
                    complete = False
                if source["kind"] == "file":
                    self._add_job(source_id, path, token)
                    seen += 1
                else:
                    for root, dirs, files in os.walk(path, followlinks=False, onerror=record_error):
                        dirs[:] = [d for d in dirs if not d.startswith(".")
                                   and not (Path(root) / d).is_symlink()
                                   and not d.endswith((".app", ".photoslibrary"))]
                        for name in files:
                            if name.startswith("."):
                                continue
                            file = Path(root) / name
                            if file.suffix.lstrip(".").lower() in MEDIA:
                                self._add_job(source_id, file, token)
                            seen += 1
                            if seen >= MAX_SCAN_FILES:
                                complete = False
                                self.last_scan_error = f"扫描上限 {MAX_SCAN_FILES} 项，请缩小监控范围"
                                break
                        if seen >= MAX_SCAN_FILES:
                            break
                if source["location_kind"] == "network" and source["mount_root"] not in {
                        v["path"] for v in self._mounted_volumes()}:
                    complete = False
                with self.connection() as con:
                    con.execute("""UPDATE sources SET availability=?,last_checked_at=? WHERE id=?""",
                                ("online" if complete else "interrupted", now(), source_id))
                    if complete:
                        con.execute("""UPDATE jobs SET file_available=0 WHERE source_id=?
                                       AND last_scan_token!=?""", (source_id, token))
                if not complete and not self.last_scan_error:
                    self.last_scan_error = "来源扫描中断，已保留此前索引记录"
                self._rebuild_folder_index(source_id)
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
        with self.scan_request_lock:
            if self.stop_event.is_set():
                return
            self.scan_pending = True
            if self.scan_thread_running:
                return
            self.scan_thread_running = True
            self.scan_thread = threading.Thread(target=self._scan_runner, daemon=True,
                                                name="gu-scan")
            self.scan_thread.start()

    def _scan_runner(self):
        while not self.stop_event.is_set():
            with self.scan_request_lock:
                if not self.scan_pending:
                    self.scan_thread_running = False
                    return
                self.scan_pending = False
            self.scan()
        with self.scan_request_lock:
            self.scan_thread_running = False

    def set_monitor(self, enabled):
        self.monitor_enabled = bool(enabled)
        if enabled:
            self.request_scan()
        self.wake_event.set()

    def set_auto_sync(self, enabled):
        """Persist one start/stop action for scanning and uploading registered sources."""
        enabled = bool(enabled)
        if enabled:
            self._ensure_upload_ready()
        with self.connection() as con:
            con.execute("""INSERT INTO settings(key,value) VALUES('auto_sync',?)
                           ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                        ("1" if enabled else "0",))
            if enabled:
                # Assign a sensible default to old active sources; archived and
                # manually paused mappings stay disabled until explicitly resumed.
                con.execute("""UPDATE sources SET album_confirmed=1,
                               album_confirmed_at=?
                               WHERE enabled=1 AND archived=0 AND
                               album_confirmed=0 AND album_name!=''""", (now(),))
        self.auto_sync_requested = enabled
        self.monitor_enabled = enabled
        self.upload_enabled = enabled
        if enabled:
            self.request_scan()
        # A stop prevents the NEXT file; an in-flight file may finish safely.
        self.wake_event.set()

    def _auth_ready(self):
        return (self.allow_upload and self.cli.is_file() and self.credentials.is_file()
                and self.credentials.stat().st_mode & 0o077 == 0)

    def set_upload(self, enabled):
        if enabled:
            self._ensure_upload_ready()
        self.upload_enabled = bool(enabled)
        self.wake_event.set()

    def _ensure_upload_ready(self):
        if not self._auth_ready():
            raise UserError("真实上传未开放：请先在 Mac 本机完成独立账号授权，并用 --allow-real-upload 启动")
        if self.engine == "owned":
            expected = ROOT / "engine/bin/gotohp-cli-owned-arm64"
            if self.cli.resolve() != expected.resolve():
                raise UserError("只允许使用本项目自行编译的上传引擎")
            ok, reason = verify_owned_engine(ROOT)
            if not ok:
                raise UserError("本地上传引擎验证失败：" + reason)
        else:
            if self.cli.resolve() != (ROOT / "gotohp-cli-macos-universal").resolve():
                raise UserError("仅允许使用已确认的发行版上传引擎路径")
            if self._digest(self.cli) != PINNED_MAC_CLI_SHA256:
                raise UserError("发行版上传引擎 SHA-256 校验失败，拒绝上传")

    def retry(self, job_id):
        with self.connection() as con:
            row = con.execute("SELECT source_id,status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if not row or row["status"] not in ("failed", "retrying", "interrupted"):
                raise UserError("只有失败、重试或中断待核对的任务可重新入队")
            active = con.execute("SELECT enabled FROM sources WHERE id=?", (row["source_id"],)).fetchone()
            if not active or not active["enabled"]:
                raise UserError("请先恢复此任务的来源目录")
            con.execute("""UPDATE jobs SET status='queued',attempts=0,retry_at=0,error='',
                           updated_at=? WHERE id=?""", (now(), job_id))
        self.wake_event.set()

    def _next_job(self, *, source_id=None, image_only=False):
        args = [time.time()]
        condition = ""
        if source_id is not None:
            condition += " AND j.source_id=?"
            args.append(int(source_id))
        if image_only:
            condition += " AND j.media_kind='image'"
        ordering = "j.bytes ASC,j.id ASC" if image_only else "j.id ASC"
        with self.connection() as con:
            return con.execute("""SELECT j.* FROM jobs j
                 JOIN sources s ON s.id=j.source_id AND s.enabled=1
                 AND s.archived=0 AND s.availability='online'
                 AND s.album_confirmed=1 AND s.album_name!=''
                 AND (s.album_key!='' OR NOT EXISTS (
                      SELECT 1 FROM jobs old WHERE old.source_id=s.id
                      AND old.status='api_accepted'))
                 WHERE (j.status='queued' OR (j.status='retrying' AND j.retry_at<=?))
                   AND j.file_available=1 """ + condition +
                 " ORDER BY " + ordering + " LIMIT 1", args).fetchone()

    def _run_upload(self, job):
        """Only called when explicit credentials, env gate and UI enable all hold."""
        path = Path(job["path"])
        if not self._eligible(path):
            return False, "", "源文件消失或无效", ""
        if self._digest(path) != job["digest"]:
            return False, "", "源文件内容变化，请重新扫描", ""
        with self.connection() as con:
            source = con.execute("""SELECT album_name,album_key,album_confirmed,enabled,archived,upload_quality
                                    FROM sources WHERE id=?""", (job["source_id"],)).fetchone()
        if not source or not source["album_confirmed"] or not source["album_name"]:
            return False, "", "来源相册尚未确认，禁止上传", ""
        if not source["enabled"] or source["archived"]:
            return False, "", "来源未启用，禁止上传", ""
        if source["upload_quality"] not in ("storage_saver", "original"):
            return False, "", "未知上传画质，拒绝上传", ""
        if not source["album_key"]:
            with self.connection() as con:
                prior = con.execute("""SELECT 1 FROM jobs WHERE source_id=?
                        AND status='api_accepted' LIMIT 1""", (job["source_id"],)).fetchone()
            if prior:
                return False, "", "旧相册 ID 未绑定，请先确认目标相册后再继续", ""
        timeout = min(21600, max(600, job["bytes"] // 51200))
        cmd = [str(self.cli), "--config", str(self.credentials), "upload", "--no-tui"]
        if source["upload_quality"] == "storage_saver":
            cmd.append("--saver")
        cmd.extend(["--album", source["album_key"] or source["album_name"], str(path)])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True,
                                    timeout=timeout, check=False)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return False, "", "上传进程异常：" + type(exc).__name__, ""
        if result.returncode != 0:
            return False, "", f"上传进程退出码 {result.returncode}（详情请在本机 CLI 排查）", ""
        # Do not expose raw stdout (could hold private provider metadata).
        output = result.stdout
        if re.search(r'"(?:error|message)"\s*:\s*"(?!")', output):
            return False, "", "API 返回错误", ""
        key = re.search(r'"mediaKey"\s*:\s*"([^"]{1,500})"', output)
        if not key:
            return False, "", "缺少可核对的 mediaKey，未登记为成功", ""
        album = re.search(r'"albumKeys"\s*:\s*\[\s*"([^"]{1,550})"', output)
        album_key = album.group(1) if album else ""
        if album_key and not re.fullmatch(r"AF1Qip[A-Za-z0-9_-]{8,500}", album_key):
            album_key = ""
        # If the CLI succeeded but omitted albumKeys, retain the accepted
        # mediaKey. The next job is blocked until an album ID is bound.
        return True, key.group(1), "", album_key

    def run_one(self, *, source_id=None, image_only=False, manual=False):
        if not ((manual or self.upload_enabled) and self._auth_ready()):
            return False
        # Refuse a replaced executable and stop the bulk worker safely.
        try:
            self._ensure_upload_ready()
        except UserError:
            self.upload_enabled = False
            return False
        job = self._next_job(source_id=source_id, image_only=image_only)
        if job is None:
            return False
        with self.connection() as con:
            source = con.execute("SELECT location_kind,mount_root FROM sources WHERE id=?",
                                 (job["source_id"],)).fetchone()
        if source and source["location_kind"] == "network" and source["mount_root"] not in {
                v["path"] for v in self._mounted_volumes()}:
            with self.connection() as con:
                con.execute("""UPDATE sources SET availability='offline',last_checked_at=?
                               WHERE id=?""", (now(), job["source_id"]))
                con.execute("UPDATE jobs SET file_available=0 WHERE source_id=?", (job["source_id"],))
            self._rebuild_folder_index(job["source_id"])
            return False
        is_duplicate = False
        with self.connection() as con:
            dupe = con.execute("""SELECT id FROM jobs WHERE digest=? AND status='api_accepted'
                                  AND id!=? LIMIT 1""", (job["digest"], job["id"])).fetchone()
            if dupe:
                con.execute("UPDATE jobs SET status='duplicate',duplicate_of=?,updated_at=? WHERE id=?",
                            (dupe["id"], now(), job["id"]))
                is_duplicate = True
            else:
                con.execute("UPDATE jobs SET status='uploading',updated_at=? WHERE id=?", (now(), job["id"]))
        if is_duplicate:
            self._rebuild_folder_index(job["source_id"])
            return True
        outcome = self._run_upload(job)
        ok, media_key, error = outcome[:3]
        album_key = outcome[3] if len(outcome) > 3 else ""
        with self.connection() as con:
            if ok:
                if album_key:
                    con.execute("""UPDATE sources SET album_key=?
                                   WHERE id=? AND album_key=''""",
                                (album_key, job["source_id"]))
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
        self._rebuild_folder_index(job["source_id"])
        return True

    def test_one_image(self, source_id):
        """Explicit one-photo upload; the global bulk-upload switch remains OFF."""
        if self.upload_enabled:
            raise UserError("请先关闭批量上传开关，再测试单张图片")
        self._ensure_upload_ready()
        with self.connection() as con:
            source = con.execute("""SELECT id,album_name,album_confirmed FROM sources
                                    WHERE id=? AND enabled=1 AND archived=0""",
                                 (int(source_id),)).fetchone()
        if not source:
            raise UserError("来源不存在或已经暂停")
        if not source["album_confirmed"]:
            raise UserError("请先确认相册名称，再进行单张测试")
        job = self._next_job(source_id=source_id, image_only=True)
        if not job:
            raise UserError("没有可用的未上传照片可测试；请检查磁盘和队列状态")
        # Single synchronous attempt only; never launches a 217-item batch.
        self.run_one(source_id=source_id, image_only=True, manual=True)
        with self.connection() as con:
            done = con.execute("""SELECT id,status,error,name FROM jobs WHERE id=?""",
                               (job["id"],)).fetchone()
        return {"id": done["id"], "status": done["status"],
                "name": done["name"], "error": done["error"],
                "album": source["album_name"]}

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
        with self.scan_request_lock:
            self.scan_pending = False
        if self.thread is not None:
            self.thread.join(timeout=3)
        if self.scan_thread is not None and self.scan_thread.is_alive():
            self.scan_thread.join(timeout=10)


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
                q = parse_qs(uri.query)
                show_all = q.get("include_hidden", ["0"])[0] == "1"
                return self._json(200, {**store.status(include_hidden=show_all), "csrf": csrf})
            if uri.path == "/api/jobs":
                try:
                    q = parse_qs(uri.query)
                    source_id = int(q["source_id"][0]) if "source_id" in q else None
                    kind = q.get("kind", [None])[0]
                    status = q.get("status", [None])[0]
                    limit = min(500, max(1, int(q.get("limit", [120])[0])))
                    offset = min(1000000, max(0, int(q.get("offset", [0])[0])))
                    show_all = q.get("include_hidden", ["0"])[0] == "1"
                    return self._json(200, {"jobs": store.jobs(
                        limit=limit,offset=offset,source_id=source_id,kind=kind,status=status,
                        include_hidden=show_all),
                        "total": store.jobs_total(source_id=source_id,kind=kind,status=status,
                                                   include_hidden=show_all),
                        "limit": limit,"offset": offset})
                except (ValueError, TypeError):
                    return self._json(400, {"error": "无效的查询参数"})
            if uri.path == "/api/folders":
                try:
                    q = parse_qs(uri.query)
                    source_id = int(q["source_id"][0])
                    if q.get("include_hidden", ["0"])[0] != "1" and not any(
                            s["id"] == source_id for s in store.sources(include_hidden=False)):
                        return self._json(404, {"error": "此来源已隐藏，请先显示隐藏记录"})
                    return self._json(200, {"folders": store.folders(source_id)})
                except (ValueError, KeyError, IndexError):
                    return self._json(400, {"error": "缺少有效的来源 ID"})
            if uri.path == "/api/volumes":
                return self._json(200, store.volumes())
            if uri.path == "/api/source/events":
                try:
                    source_id = int(parse_qs(uri.query)["source_id"][0])
                    return self._json(200, {"events": store.source_events(source_id)})
                except (KeyError, IndexError, ValueError):
                    return self._json(400, {"error": "请输入来源 ID"})
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
                elif uri == "/api/source/display":
                    result = {"sources": store.set_display(
                        int(payload["id"]), payload.get("mode"))}
                elif uri == "/api/source/album":
                    result = {"sources": store.set_album(
                        int(payload["id"]), payload.get("album_name"))}
                elif uri == "/api/source/album/bind":
                    result = {"sources": store.bind_album(
                        int(payload["id"]), payload.get("album_ref"))}
                elif uri == "/api/source/quality":
                    result = {"sources": store.set_upload_quality(
                        int(payload["id"]), payload.get("quality"))}
                elif uri == "/api/source/test-upload":
                    result = {"test": store.test_one_image(int(payload["id"]))}
                elif uri == "/api/source/restore":
                    result = {"sources": store.restore_source(int(payload["id"]))}
                elif uri == "/api/cloud/verify":
                    store.set_cloud_verified(int(payload["id"]), payload.get("verified") is True)
                    result = {"jobs": store.jobs()}
                elif uri == "/api/share/connect":
                    result = store.connect_share(payload.get("url"))
                elif uri == "/api/monitor":
                    store.set_monitor(payload.get("enabled") is True)
                    result = {"monitor_enabled": store.monitor_enabled}
                elif uri == "/api/sync":
                    store.set_auto_sync(payload.get("enabled") is True)
                    result = {"auto_sync_enabled": store.monitor_enabled and store.upload_enabled}
                elif uri == "/api/upload":
                    store.set_auto_sync(payload.get("enabled") is True)
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
    parser.add_argument("--engine", choices=("release", "owned"), default="release",
                        help="Select verified release CLI or self-compiled local CLI")
    parser.add_argument("--allow-real-upload", action="store_true",
                        help="Enable manual upload switch after independent Google credentials setup")
    args = parser.parse_args()
    store = Store(args.state_dir, allow_upload=args.allow_real_upload,
                  engine=args.engine)
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

"""Verified RAR directory imports with private, destination-side recovery receipts.

rarfile is metadata-only. CRC32 detects accidental damage, not malicious collisions.
Matching loose files does not test compressed archive payload readability. Interrupted
solid extraction may decompress the set again. Receipts are private to their creating
uid; unknown destinations and unowned staging are never adopted or deleted.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import uuid
import warnings
import zlib
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from unittest.mock import patch

import rarfile

from xbox_media_utils.constants import MEDIA_EXTENSIONS
from xbox_media_utils.core import PLEX_GROUP, PLEX_USER
from xbox_media_utils.ffmpeg import validate_output
from xbox_media_utils.files import set_ownership
from xbox_media_utils.media import is_sample_file


def safe_relative(value: str) -> Path:
    """Reject paths whose interpretation could differ between tools."""
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise RuntimeError(f"Unsafe relative path: {value!r}")
    if any(ord(char) < 32 or char == "\ufffd" for char in value):
        raise RuntimeError(f"Unsupported filename: {value!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts) or PurePosixPath(value).is_absolute():
        raise RuntimeError(f"Unsafe relative path: {value!r}")
    return Path(*parts)


def check_path(path: Path, directory: bool = False, missing: bool = False) -> None:
    """Check every ancestor without resolving away symlinks."""
    for parent in reversed((path, *path.parents)):
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            if missing:
                continue
            raise
        expected_dir = parent != path or directory
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) if expected_dir else stat.S_ISREG(mode)):
            raise RuntimeError(f"Unsafe import path: {parent}")


def digest(path: Path) -> dict:
    """Hash bounded chunks and detect replacement or ordinary concurrent changes."""
    check_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise RuntimeError(f"Not a regular file: {path}")
        sha = hashlib.sha256()
        crc = 0
        size = 0
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(block)
            crc = zlib.crc32(block, crc)
            size += len(block)
        after = os.fstat(stream.fileno())
    current = path.lstat()
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if any(getattr(before, key) != getattr(after, key) for key in fields) or any(
        getattr(after, key) != getattr(current, key) for key in fields
    ):
        raise RuntimeError(f"File changed while hashing: {path}")
    return {"sha256": sha.hexdigest(), "size": size, "crc": crc & 0xFFFFFFFF}


def matches(path: Path, member: dict) -> bool:
    if not path.exists() and not path.is_symlink():
        return False
    value = digest(path)
    return value["size"] == member["size"] and value["crc"] == member["crc"]


def vendor_unrar() -> str:
    """Require the existing RARLAB executable, never an automatic backend fallback."""
    executable = shutil.which("unrar")
    if executable is None:
        raise RuntimeError("RAR imports require host-provided RARLAB unrar with -ol- support")
    result = subprocess.run(
        [executable, "-cfg-", "-p-"], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, errors="replace", check=False, timeout=10,
    )
    banner = result.stdout + result.stderr
    version = re.search(r"\bUNRAR\s+(\d+)\.(\d+)\s+freeware\b", banner, re.IGNORECASE)
    if (
        version is None or int(version[1]) < 7
        or not re.search(r"Alexander\s+Roshal|RARLAB", banner, re.IGNORECASE)
        or re.search(r"unrar[- ]free|bsdtar|7-zip|the unarchiver", banner, re.IGNORECASE)
    ):
        raise RuntimeError("Require RARLAB UNRAR 7+; unrar-free/unar/7z/bsdtar are unsupported")
    return executable


def inventory(source: Path, archives: list[Path]) -> tuple[dict, dict]:
    """Read strict metadata, complete volume hashes, and all loose-media hashes."""
    # Reject links/special files before rarfile can follow a continuation volume.
    for current, dirs, files in os.walk(source, followlinks=False):
        for name in dirs:
            check_path(Path(current) / name, directory=True)
        for name in files:
            check_path(Path(current) / name)
    sets = []
    members = {}
    logical_files = set()
    logical_dirs = set()
    for archive in archives:
        check_path(archive)
        first_hash = digest(archive)
        split_name = None

        def inspect_header(header):
            nonlocal split_name
            # rarfile checks headers, but a seek past a truncated final payload can
            # otherwise look like EOF. Check extents without decoding any payload.
            volume = Path(header.volume_file)
            check_path(volume)
            volume.relative_to(source)
            if header.data_offset + header.add_size > volume.stat().st_size:
                raise RuntimeError(f"Truncated RAR volume: {volume}")
            if header.type != rarfile.RAR_BLOCK_FILE:
                return
            if header.flags & rarfile.RAR_FILE_VERSION:
                raise RuntimeError("Versioned RAR members unsupported")
            if header.flags & rarfile.RAR_FILE_SPLIT_BEFORE:
                if split_name != header.filename:
                    raise RuntimeError("Broken multipart member chain")
            elif split_name is not None:
                raise RuntimeError("Incomplete multipart member")
            split_name = header.filename if header.flags & rarfile.RAR_FILE_SPLIT_AFTER else None
        # 4.2 can decompress compressed archive comments during construction.
        # Refuse that route, too: no rarfile backend selection may ever run.
        with warnings.catch_warnings(), patch.object(
            rarfile, "tool_setup", side_effect=RuntimeError("Compressed RAR comments unsupported")
        ):
            warnings.simplefilter("error", rarfile.UnsupportedWarning)
            with rarfile.RarFile(str(archive), errors="strict", info_callback=inspect_header) as metadata:
                if metadata.needs_password() or metadata.strerror():
                    raise RuntimeError(f"Encrypted or incomplete RAR: {archive}")
                infos = metadata.infolist()
                volumes = [Path(name) for name in metadata.volumelist()]
        if split_name is not None:
            raise RuntimeError("Incomplete multipart member")
        if not volumes or volumes[0] != archive:
            raise RuntimeError(f"Invalid RAR volume inventory: {archive}")
        volume_hashes = {}
        for volume in volumes:
            check_path(volume)
            volume.relative_to(source)
            if volume.parent != archive.parent:
                raise RuntimeError(f"Unexpected RAR volume location: {volume}")
            volume_hashes[str(volume.relative_to(source))] = digest(volume)
        if digest(archive) != first_hash:
            raise RuntimeError(f"Archive changed during metadata inspection: {archive}")
        media_count = 0
        for info in infos:
            name = info.filename.rstrip("/") if info.is_dir() else info.filename
            relative = archive.parent.relative_to(source) / safe_relative(name)
            logical = relative.as_posix()
            if info.needs_password() or info.is_symlink() or info.file_redir is not None:
                raise RuntimeError(f"Encrypted or redirected RAR member: {name}")
            if info.is_dir():
                if logical in logical_dirs or logical in logical_files:
                    raise RuntimeError(f"Duplicate RAR path: {logical}")
                logical_dirs.add(logical)
                continue
            if (
                not info.is_file() or type(info.file_size) is not int or info.file_size < 0
                or type(info.CRC) is not int or not 0 <= info.CRC <= 0xFFFFFFFF
                or (info.host_os == rarfile.RAR_OS_UNIX and stat.S_IFMT(info.mode or 0)
                    not in {0, stat.S_IFREG})
                or getattr(info, "file_version", None)
                or info.flags & rarfile.RAR_FILE_VERSION
            ):
                raise RuntimeError(f"Unsupported RAR member (requires plain CRC32 + size): {name}")
            if logical in logical_files or logical in logical_dirs:
                raise RuntimeError(f"Duplicate RAR path: {logical}")
            logical_files.add(logical)
            if relative.suffix.lower() in MEDIA_EXTENSIONS and not is_sample_file(relative):
                media_count += 1
                members[logical] = {
                    "size": info.file_size, "crc": info.CRC,
                    "archive": str(archive.relative_to(source)), "member": name,
                }
        if not media_count:
            raise RuntimeError(f"No media files found in archive; source retained: {archive}")
        sets.append({"archive": str(archive.relative_to(source)), "volumes": volume_hashes})
    for name in logical_files:
        if any(parent.as_posix() in logical_files for parent in Path(name).parents if str(parent) != "."):
            raise RuntimeError(f"RAR file/directory collision: {name}")
    loose = {}
    for path in sorted(source.rglob("*")):
        if path.suffix.lower() not in MEDIA_EXTENSIONS or is_sample_file(path):
            continue
        relative = str(safe_relative(path.relative_to(source).as_posix()))
        loose[relative] = digest(path)
        if relative not in members:
            members[relative] = {**loose[relative], "archive": None, "member": relative}
    if not members:
        raise RuntimeError("No media files found")
    return {"sets": sets, "loose": loose, "members": members}, members


def private_dir(path: Path) -> None:
    check_path(path, directory=True, missing=True)
    try:
        path.mkdir(mode=0o700)
    except FileExistsError:
        pass
    value = path.lstat()
    if not stat.S_ISDIR(value.st_mode) or value.st_uid != os.geteuid() or value.st_mode & 0o077:
        raise RuntimeError(f"Import state must be a private directory owned by this uid: {path}")


def private_file(path: Path) -> None:
    check_path(path)
    value = path.lstat()
    if value.st_uid != os.geteuid() or value.st_mode & 0o077 or value.st_nlink != 1:
        raise RuntimeError(f"Unsafe import state file: {path}")


def output_ownership(path: Path) -> None:
    success, error = set_ownership(path, PLEX_USER, PLEX_GROUP)
    if not success:
        raise RuntimeError(f"Cannot set Plex ownership on {path}: {error}")


def sync_tree(path: Path) -> None:
    for current, _, _ in os.walk(path, topdown=False):
        sync_dir(Path(current))


def sync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextmanager
def destination_lock(state: Path):
    private_dir(state.parent)
    private_dir(state)
    path = state / "lock"
    check_path(path, missing=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        private_file(path)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    except BlockingIOError as error:
        raise RuntimeError("Another import owns this destination; retry later") from error
    finally:
        os.close(fd)  # Never unlink the stable lock pathname.


def save_receipt(state: Path, receipt: dict) -> None:
    """Durable atomic replacement; failure before PREPARED forbids publication."""
    target = state / "receipt.json"
    if target.exists() or target.is_symlink():
        private_file(target)
    fd, name = tempfile.mkstemp(prefix=".receipt-", dir=state)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(receipt, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        sync_dir(state)
    finally:
        if temporary.exists():
            temporary.unlink()


def token(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None


def load_receipt(state: Path, manifest: dict, members: dict) -> dict:
    path = state / "receipt.json"
    if not path.exists() and not path.is_symlink():
        return {"version": 1, "manifest": manifest, "work": None, "jobs": {}, "extract": {}, "scan": "pending"}
    private_file(path)
    with open(path, encoding="utf-8") as stream:
        receipt = json.load(stream)
    if not isinstance(receipt, dict) or set(receipt) != {"version", "manifest", "work", "jobs", "extract", "scan"}:
        raise RuntimeError("Invalid import receipt schema")
    if receipt["version"] != 1 or receipt["manifest"] != manifest:
        raise RuntimeError("Source/archive/configuration changed or unsupported receipt; refusing existing import")
    if not isinstance(receipt["scan"], str) or receipt["scan"] not in {"pending", "complete"}:
        raise RuntimeError("Invalid scan receipt")
    work = receipt["work"]
    if work is not None:
        if not isinstance(work, dict) or set(work) != {"token", "primary", "backup"} or not token(work["token"]):
            raise RuntimeError("Invalid work receipt")
        for identity in (work["primary"], work["backup"]):
            if identity is not None and (
                not isinstance(identity, dict) or set(identity) != {"dev", "ino"}
                or any(type(value) is not int or value < 0 for value in identity.values())
            ):
                raise RuntimeError("Invalid stage identity")
    if not isinstance(receipt["extract"], dict) or not all(
        key in {item["archive"] for item in manifest["inventory"]["sets"]} and token(value)
        for key, value in receipt["extract"].items()
    ):
        raise RuntimeError("Invalid extraction receipt")
    if not isinstance(receipt["jobs"], dict):
        raise RuntimeError("Invalid jobs receipt")
    for name, job in receipt["jobs"].items():
        if name not in members or not isinstance(job, dict) or set(job) != {"phase", "token", "artifacts"}:
            raise RuntimeError("Invalid member receipt")
        if not isinstance(job["phase"], str) or job["phase"] not in {"working", "prepared", "complete"} or not token(job["token"]):
            raise RuntimeError("Invalid member phase")
        if not isinstance(job["artifacts"], list) or (job["phase"] != "working" and not job["artifacts"]):
            raise RuntimeError("Invalid artifact receipt")
        if job["phase"] == "working" and job["artifacts"]:
            raise RuntimeError("Working receipt cannot authorize publication")
        targets = set()
        for artifact in job["artifacts"]:
            if not isinstance(artifact, dict) or set(artifact) != {"area", "name", "sha256", "size", "dev", "ino"}:
                raise RuntimeError("Invalid artifact schema")
            area = artifact["area"]
            relative = safe_relative(artifact["name"])
            logical = Path(name)
            if area == "primary":
                if relative.parent != logical.parent or not relative.name.startswith(logical.stem + "."):
                    raise RuntimeError("Artifact outside logical media directory")
            elif area == "backup":
                expected = Path(manifest["base_relative"]) / logical.with_suffix(".DV.mkv")
                if relative != expected:
                    raise RuntimeError("Invalid DoVi archive mapping")
            else:
                raise RuntimeError("Invalid artifact area")
            if (area, str(relative)) in targets:
                raise RuntimeError("Duplicate artifact")
            targets.add((area, str(relative)))
            if not isinstance(artifact["sha256"], str) or re.fullmatch(r"[0-9a-f]{64}", artifact["sha256"]) is None:
                raise RuntimeError("Invalid artifact digest")
            if any(type(artifact[key]) is not int or artifact[key] < 0 for key in ("size", "dev", "ino")):
                raise RuntimeError("Invalid artifact identity")
    if (receipt["jobs"] or receipt["extract"]) and work is None:
        raise RuntimeError("Missing operation ownership")
    if any(job["phase"] == "prepared" for job in receipt["jobs"].values()) and work["primary"] is None:
        raise RuntimeError("Missing prepared stage ownership")
    return receipt


def own_tree(path: Path) -> None:
    """Only regular files/directories may exist inside an owned work tree."""
    check_path(path, directory=True)
    for current, dirs, files in os.walk(path, followlinks=False):
        for name in dirs:
            check_path(Path(current) / name, directory=True)
        for name in files:
            check_path(Path(current) / name)


def work_root(state: Path, work: dict) -> Path:
    return state / ("work-" + work["token"])


def owned_work(state: Path, work: dict, area: str, missing: bool = False) -> Path:
    path = work_root(state, work)
    if missing and not path.exists() and not path.is_symlink():
        return path
    private_dir_check = path.lstat()
    identity = work[area]
    if identity != {"dev": private_dir_check.st_dev, "ino": private_dir_check.st_ino}:
        raise RuntimeError(f"Unowned stage retained, refusing recovery/deletion: {path}")
    if private_dir_check.st_uid != os.geteuid() or private_dir_check.st_mode & 0o077:
        raise RuntimeError(f"Unsafe stage ownership: {path}")
    own_tree(path)
    return path


def create_work(state: Path, receipt: dict, area: str, save_state: Path) -> Path:
    work = receipt["work"]
    private_dir(state.parent)
    private_dir(state)
    path = work_root(state, work)
    if path.exists() or path.is_symlink():
        return owned_work(state, work, area)
    path.mkdir(mode=0o700)
    value = path.lstat()
    work[area] = {"dev": value.st_dev, "ino": value.st_ino}
    sync_dir(state)
    save_receipt(save_state, receipt)
    return path


def reserve_outputs(members: dict, dest: Path, backup: Path, base_relative: Path, receipt: dict) -> None:
    """Reserve conservative generated names across the entire batch, before tools run."""
    reserved = {}
    for name in members:
        relative = Path(name)
        if relative.name.endswith(".HDR10.mkv"):
            raise RuntimeError(f"Reserved HDR10 input name: {name}")
        candidates = {dest / relative}
        candidates.update(dest / relative.with_name(relative.stem + suffix) for suffix in (
            ".mkv", ".importing.mkv", ".HDR10.mkv", ".HDR10.tmp.mkv",
        ))
        candidates.add(backup / base_relative / relative.with_suffix(".DV.mkv"))
        for target in candidates:
            check_path(target.parent, directory=True, missing=True)
            for previous, owner in reserved.items():
                if owner != name and (target == previous or target in previous.parents or previous in target.parents):
                    raise RuntimeError(f"Import destination collision: {target}")
            reserved[target] = name
            job = receipt["jobs"].get(name)
            recognized = job is not None and job["phase"] in {"prepared", "complete"}
            authorized = {artifact_target(item, dest, backup) for item in job["artifacts"]} if recognized else set()
            if (target.exists() or target.is_symlink()) and target not in authorized:
                raise RuntimeError(
                    f"Unrecognized destination already exists (including legacy partials): {target}; "
                    "preserved unchanged. Inspect and relocate conflicting artifacts explicitly before retrying."
                )
        parent = dest / relative.parent
        if parent.exists():
            for path in parent.iterdir():
                if (
                    path.name.startswith(relative.stem + ".en.")
                    and path.suffix in {".srt", ".ass", ".sup"}
                    and path not in authorized
                ):
                    raise RuntimeError(
                        f"Unrecognized subtitle destination (legacy partial preserved): {path}; "
                        "inspect and relocate conflicting artifacts explicitly before retrying."
                    )


def staged_artifact(artifact: dict, job: dict, work: Path, backup_work: Path, base_relative: Path) -> Path:
    if artifact["area"] == "primary":
        return work / "jobs" / job["token"] / "primary" / base_relative / artifact["name"]
    return backup_work / "files" / job["token"] / artifact["name"]


def artifact_target(artifact: dict, dest: Path, backup: Path) -> Path:
    return (dest if artifact["area"] == "primary" else backup) / artifact["name"]


def verify_artifact(path: Path, artifact: dict, identity: bool = False) -> None:
    value = digest(path)
    if value["size"] != artifact["size"] or value["sha256"] != artifact["sha256"]:
        raise RuntimeError(f"Imported artifact changed: {path}")
    value_stat = path.lstat()
    if identity and (value_stat.st_dev != artifact["dev"] or value_stat.st_ino != artifact["ino"]):
        raise RuntimeError(f"Destination is not this operation's published inode: {path}")


def publish(job: dict, work: Path, backup_work: Path, dest: Path, backup: Path, base_relative: Path, plex: Path) -> None:
    for artifact in job["artifacts"]:
        staged = staged_artifact(artifact, job, work, backup_work, base_relative)
        verify_artifact(staged, artifact, identity=True)
        target = artifact_target(artifact, dest, backup)
        check_path(target.parent, directory=True, missing=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        current = target.parent
        boundary = plex if artifact["area"] == "primary" else backup
        while current != boundary and current != current.parent:
            output_ownership(current)
            current = current.parent
        try:
            os.link(staged, target, follow_symlinks=False)
        except FileExistsError:
            verify_artifact(target, artifact, identity=True)
        sync_dir(target.parent)
        verify_artifact(target, artifact, identity=True)


def run_archive_import(
    source: Path, dest: Path, plex: Path, backup: Path, archives: list[Path],
    *, import_file, probe_file, scan, dry_run: bool = False,
    no_plex_scan: bool = False, use_hardware: bool = True,
) -> int:
    """Return CLI status; caller reports errors without discarding recovery state."""
    for path in (source, dest, plex, backup):
        check_path(path.absolute(), directory=True, missing=True)
    source = Path(os.path.abspath(source))
    dest = Path(os.path.abspath(dest))
    plex = Path(os.path.abspath(plex))
    backup = Path(os.path.abspath(backup))
    check_path(source, directory=True)
    check_path(dest, directory=True, missing=True)
    check_path(backup, directory=True, missing=True)
    if source == dest or source in dest.parents or dest in source.parents:
        raise RuntimeError("RAR import source and destination must not overlap")
    if source == backup or source in backup.parents or backup in source.parents:
        raise RuntimeError("RAR source and DoVi backup must not overlap")
    if backup == dest or backup in dest.parents or dest in backup.parents:
        raise RuntimeError("DoVi backup and import destination must not overlap")
    base_relative = safe_relative(dest.relative_to(plex).as_posix())
    archives = [Path(os.path.abspath(path)) for path in archives]
    key = hashlib.sha256(os.fsencode(str(dest))).hexdigest()
    state = dest.parent / ".xbox-import-state" / key
    backup_state = backup / ".xbox-import-state" / key

    def plan():
        items, members = inventory(source, archives)
        manifest = {
            "source": str(source), "destination": str(dest), "backup": str(backup),
            "base_relative": str(base_relative), "hardware": use_hardware, "inventory": items,
        }
        return manifest, members

    if dry_run:
        manifest, members = plan()
        if state.exists() or state.is_symlink():
            check_path(state, directory=True)
            for path in (state.parent, state):
                value = path.lstat()
                if value.st_uid != os.geteuid() or value.st_mode & 0o077:
                    raise RuntimeError(f"Unsafe state directory: {path}")
        receipt = load_receipt(state, manifest, members)
        reserve_outputs(members, dest, backup, base_relative, receipt)
        for name, member in members.items():
            job = receipt["jobs"].get(name)
            if job and job["phase"] == "complete":
                for artifact in job["artifacts"]:
                    verify_artifact(artifact_target(artifact, dest, backup), artifact)
                print(f"Would skip verified completed import: {name}")
            elif matches(source / name, member):
                print(f"Would reuse verified loose media and import: {name}")
            else:
                print(f"Would extract RAR to private staging and import: {name}")
        return 0

    executable = vendor_unrar()
    # Create only the library, never source directories. All state stays beside imports.
    check_path(dest.parent, directory=True, missing=True)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with destination_lock(state):
        manifest, members = plan()
        receipt = load_receipt(state, manifest, members)
        reserve_outputs(members, dest, backup, base_relative, receipt)
        # Independently rehash completed artifacts before deciding to skip any work.
        for job in receipt["jobs"].values():
            if job["phase"] == "complete":
                for artifact in job["artifacts"]:
                    verify_artifact(artifact_target(artifact, dest, backup), artifact)
        if receipt["work"] is None:
            receipt["work"] = {"token": uuid.uuid4().hex, "primary": None, "backup": None}
            save_receipt(state, receipt)
        pending = [name for name in members if receipt["jobs"].get(name, {}).get("phase") != "complete"]
        work = work_root(state, receipt["work"])
        backup_work = work_root(backup_state, receipt["work"])
        if pending:
            if receipt["work"]["primary"] is None and work.exists():
                if receipt["jobs"] or receipt["extract"]:
                    raise RuntimeError(f"Unowned stage retained: {work}")
                print(f"Unowned interrupted stage preserved: {work}")
                receipt["work"]["token"] = uuid.uuid4().hex
                save_receipt(state, receipt)
                backup_work = work_root(backup_state, receipt["work"])
            if any(receipt["jobs"].get(name, {}).get("phase") == "prepared" for name in pending):
                owned_work(state, receipt["work"], "primary")
            work = create_work(state, receipt, "primary", state)
            print(f"Archive staging: {work}")
        selected = {}
        # Prepare all extraction sets before importing any new media.
        for name in pending:
            job = receipt["jobs"].get(name)
            if job and job["phase"] == "prepared":
                continue
            member = members[name]
            loose = source / name
            if matches(loose, member):
                selected[name] = loose
                continue
            if member["archive"] is None:
                raise RuntimeError(f"Loose media changed: {loose}")
            archive_name = member["archive"]
            old_token = receipt["extract"].get(archive_name)
            extract_dir = work / "extract" / old_token if old_token else None
            if extract_dir is not None and extract_dir.exists():
                own_tree(extract_dir)
            if extract_dir is None or not matches(extract_dir / member["member"], member):
                extract_token = uuid.uuid4().hex
                receipt["extract"][archive_name] = extract_token
                save_receipt(state, receipt)
                extract_dir = work / "extract" / extract_token
                extract_dir.mkdir(parents=True, mode=0o700)
                completed = subprocess.run(
                    [executable, "x", "-cfg-", "-ol-", "-o-", "-p-", "-idq", "--",
                     str(source / archive_name), str(extract_dir) + os.sep],
                    stdin=subprocess.DEVNULL, capture_output=True, text=True,
                    errors="replace", cwd=extract_dir, check=False,
                )
                own_tree(extract_dir)
                if completed.returncode != 0:
                    detail = (completed.stderr or completed.stdout or "Extraction failed")[-2000:]
                    raise RuntimeError(f"unrar failed (exit {completed.returncode}): {detail}; retained at {work}")
            if not matches(extract_dir / member["member"], member):
                raise RuntimeError(f"Extracted member checksum failed: {name}; retained at {work}")
            selected[name] = extract_dir / member["member"]
        if plan()[0] != manifest:
            raise RuntimeError("Source/archive changed during preparation; stages retained")
        for name in pending:
            job = receipt["jobs"].get(name)
            if not job or job["phase"] != "prepared":
                input_path = selected[name]
                source_hash = digest(input_path)
                info = probe_file(input_path)
                if info.probe_error or info.incompatible_reason:
                    raise RuntimeError(info.probe_error or info.incompatible_reason)
                job = {"phase": "working", "token": uuid.uuid4().hex, "artifacts": []}
                receipt["jobs"][name] = job
                save_receipt(state, receipt)
                job_root = work / "jobs" / job["token"]
                primary_root = job_root / "primary"
                result = import_file(
                    info, primary_root / base_relative / Path(name).parent, primary_root,
                    dovi_backup_root=job_root / "backup", use_hardware=use_hardware,
                )
                if result["status"] != "success":
                    raise RuntimeError(f"Import failed: {result.get('error', result['status'])}; retained at {work}")
                own_tree(job_root)
                if digest(input_path) != source_hash:
                    raise RuntimeError("Import input changed; outputs retained, not published")
                if result["action"] == "copy" and digest(Path(result["destination"])) != source_hash:
                    raise RuntimeError("Copied output does not match source")
                destination = Path(result["destination"])
                if not destination.is_file():
                    raise RuntimeError("Importer did not produce its primary artifact")
                destination.relative_to(primary_root / base_relative)
                if result["action"] != "copy":
                    valid, reason = validate_output(info, destination)
                    if not valid:
                        raise RuntimeError(f"Output validation failed: {reason}")
                allowed_primary = {destination}
                for subtitle in result.get("subtitles_extracted", []):
                    if not subtitle.get("success") or not subtitle.get("output"):
                        raise RuntimeError("Subtitle extraction incomplete; outputs retained")
                    allowed_primary.add(Path(subtitle["output"]))
                expected_backup = job_root / "backup" / base_relative / Path(name).with_suffix(".DV.mkv")
                if info.has_dovi_profile_8 and not expected_backup.is_file():
                    raise RuntimeError("DoVi original backup missing; refusing publication")
                artifacts = []
                for area, root in (("primary", primary_root / base_relative), ("backup", job_root / "backup")):
                    if not root.exists():
                        continue
                    for path in sorted(root.rglob("*")):
                        if path.is_dir():
                            continue
                        relative = path.relative_to(root)
                        if area == "primary" and path not in allowed_primary:
                            raise RuntimeError(f"Unexpected/incomplete primary artifact retained: {path}")
                        if area == "backup" and (path != expected_backup or digest(path) != source_hash):
                            raise RuntimeError("DoVi backup is not the original source at its logical path")
                        if area == "backup":
                            check_path(backup, directory=True, missing=True)
                            backup.mkdir(parents=True, exist_ok=True)
                            backup_work = create_work(backup_state, receipt, "backup", state)
                            copied = backup_work / "files" / job["token"] / relative
                            copied.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(path, copied)
                            if digest(copied) != digest(path):
                                raise RuntimeError("DoVi backup staging copy mismatch")
                            path = copied
                        output_ownership(path)
                        with open(path, "rb") as stream:
                            os.fsync(stream.fileno())
                        value = digest(path)
                        identity = path.stat()
                        artifacts.append({
                            "area": area, "name": str(relative), "sha256": value["sha256"],
                            "size": value["size"], "dev": identity.st_dev, "ino": identity.st_ino,
                        })
                if plan()[0] != manifest:
                    raise RuntimeError("Source/archive changed during import; no publication")
                sync_tree(work)
                if receipt["work"]["backup"] is not None:
                    sync_tree(backup_work)
                job["artifacts"] = artifacts
                job["phase"] = "prepared"
                save_receipt(state, receipt)  # Must succeed before the first link.
                load_receipt(state, manifest, members)  # Validate generated paths as untrusted input.
            owned_work(state, receipt["work"], "primary")
            if any(artifact["area"] == "backup" for artifact in job["artifacts"]):
                owned_work(backup_state, receipt["work"], "backup")
            if plan()[0] != manifest:
                raise RuntimeError("Source/archive changed before publication")
            publish(job, work, backup_work, dest, backup, base_relative, plex)
            job["phase"] = "complete"
            save_receipt(state, receipt)
            print(f"Verified import complete: {name}")
        if plan()[0] != manifest:
            raise RuntimeError("Source/archive changed; scan and cleanup deferred")
        # Completion is durable before any staging cleanup. Retrying cleanup never imports again.
        for area, root in (("backup", backup_state), ("primary", state)):
            if receipt["work"][area] is None:
                continue
            path = owned_work(root, receipt["work"], area, missing=True)
            if path.exists():
                shutil.rmtree(path)
                sync_dir(root)
        # --no-plex-scan leaves a durable pending intent for a later ordinary retry.
        if receipt["scan"] != "complete" and not no_plex_scan:
            if not scan(dest):
                raise RuntimeError("Plex scan failed; imports verified, scan remains pending")
            receipt["scan"] = "complete"
            save_receipt(state, receipt)
        print(f"Source preserved at: {source}")
        return 0

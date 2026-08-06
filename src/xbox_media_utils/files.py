"""File system and ownership utilities."""

import grp
import os
import pwd
from pathlib import Path
from typing import Optional

COMPANION_EXTENSIONS = {
    ".ass",
    ".idx",
    ".jpeg",
    ".jpg",
    ".nfo",
    ".png",
    ".srt",
    ".ssa",
    ".sub",
    ".sup",
    ".vtt",
    ".webp",
    ".xml",
}


def set_ownership(filepath: Path, user: str, group: str) -> tuple[bool, Optional[str]]:
    """Set file ownership to specified user:group."""
    try:
        uid = pwd.getpwnam(user).pw_uid
        gid = grp.getgrnam(group).gr_gid
        os.chown(filepath, uid, gid)
        return True, None
    except Exception as e:
        return False, str(e)


def collect_media_files(source: Path, extensions: set[str]) -> list[Path]:
    """Recursively collect all media files from source."""
    from .media import is_sample_file

    files = []
    if source.is_file():
        if source.suffix.lower() in extensions and not is_sample_file(source):
            files.append(source)
    else:
        for ext in extensions:
            for f in source.rglob(f"*{ext}"):
                if not is_sample_file(f):
                    files.append(f)
            for f in source.rglob(f"*{ext.upper()}"):
                if not is_sample_file(f):
                    files.append(f)
    return sorted(set(files))


def get_root_media_destination(media_path: Path, plex_root: Path) -> Optional[Path]:
    """Return the conforming destination for media directly under a library root."""
    try:
        relative_path = media_path.resolve().relative_to(plex_root.resolve())
    except ValueError:
        return None

    # Plex root layout is <plex-root>/<library>/<media>. Files already below a
    # media directory, or directly in the Plex root itself, need no move.
    if len(relative_path.parts) != 2:
        return None

    return media_path.parent / media_path.stem / media_path.name


def collect_media_companions(media_path: Path, media_extensions: set[str]) -> list[Path]:
    """Collect sidecars attributable to one media file without claiming another's."""
    parent = media_path.parent
    media_files = [
        path
        for path in parent.iterdir()
        if path.is_file() and path.suffix.lower() in media_extensions
    ]
    target_stem = media_path.stem.casefold()

    companions = []
    for path in parent.iterdir():
        if not path.is_file() or path.suffix.lower() not in COMPANION_EXTENSIONS:
            continue

        name = path.name.casefold()
        matching_stems = [
            candidate.stem.casefold()
            for candidate in media_files
            if name.startswith(candidate.stem.casefold() + ".")
            or name.startswith(candidate.stem.casefold() + "-")
        ]
        if not matching_stems:
            continue

        # A sidecar belongs to the most specific matching media stem. This
        # keeps Movie.Part2.en.srt with Movie.Part2.mkv rather than Movie.mkv.
        longest_length = max(len(stem) for stem in matching_stems)
        if len(target_stem) == longest_length and target_stem in matching_stems:
            companions.append(path)

    return sorted(set(companions))


def organize_root_media(
    media_path: Path,
    plex_root: Path,
    media_extensions: set[str],
    dry_run: bool = False,
) -> tuple[bool, str, Path, list[Path]]:
    """Move root-level media and attributable sidecars into a same-named directory."""
    destination = get_root_media_destination(media_path, plex_root)
    if destination is None:
        return True, "Media already has a containing directory", media_path, []
    if not media_path.exists():
        return False, f"Media file does not exist: {media_path}", media_path, []

    same_stem_media = [
        path
        for path in media_path.parent.iterdir()
        if path.is_file()
        and path != media_path
        and path.suffix.lower() in media_extensions
        and path.stem.casefold() == media_path.stem.casefold()
    ]
    if same_stem_media:
        return (
            False,
            f"Multiple media files map to the same directory: {destination.parent}",
            media_path,
            [],
        )

    sources = [media_path, *collect_media_companions(media_path, media_extensions)]
    destinations = [destination.parent / source.name for source in sources]
    destination_dir = destination.parent

    if destination_dir.exists() and not destination_dir.is_dir():
        return False, f"Organization path is not a directory: {destination_dir}", media_path, []

    conflicts = [path for path in destinations if path.exists()]
    if conflicts:
        return False, f"Organization destination already exists: {conflicts[0]}", media_path, []

    if dry_run:
        return True, f"Would organize into: {destination_dir}", destination, destinations

    created_directory = not destination_dir.exists()
    moved: list[tuple[Path, Path]] = []
    try:
        destination_dir.mkdir(parents=False, exist_ok=True)
        for source, target in zip(sources, destinations):
            source.rename(target)
            moved.append((source, target))
    except Exception as e:
        rollback_errors = []
        for source, target in reversed(moved):
            try:
                target.rename(source)
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if created_directory:
            try:
                destination_dir.rmdir()
            except OSError:
                pass
        message = f"Organization failed: {e}"
        if rollback_errors:
            message += f"; rollback failed: {'; '.join(rollback_errors)}"
        return False, message, media_path, []

    return True, f"Organized into: {destination_dir}", destination, destinations

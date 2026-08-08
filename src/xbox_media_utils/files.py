"""File system and ownership utilities."""

import grp
import os
import pwd
import re
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


TV_EPISODE_TOKEN = re.compile(
    r"(?<![A-Z0-9])S(?P<season>\d{2})E\d{2,3}(?:(?:-?E)\d{2,3})*(?![A-Z0-9])",
    re.IGNORECASE,
)


def _root_media_parts(media_path: Path, plex_root: Path) -> Optional[tuple[str, str]]:
    try:
        relative_path = media_path.resolve().relative_to(plex_root.resolve())
    except ValueError:
        return None

    # Plex root layout is <plex-root>/<library>/<media>. Files already below a
    # media directory, or directly in the Plex root itself, need no move.
    if len(relative_path.parts) != 2:
        return None

    return relative_path.parts


def _tv_episode_directory(media_path: Path) -> Optional[Path]:
    match = TV_EPISODE_TOKEN.search(media_path.stem)
    if not match:
        return None

    prefix = media_path.stem[: match.start()].strip(" ._-")
    prefix = re.sub(r"[._]+", " ", prefix)
    prefix = re.sub(r"\s+", " ", prefix).strip()
    if not prefix or not any(character.isalnum() for character in prefix):
        return None

    year_match = re.fullmatch(r"(?P<title>.+?)[ ._-]+(?P<year>(?:19|20)\d{2})", prefix)
    if year_match:
        title = year_match.group("title").strip(" ._-")
        if not title:
            return None
        prefix = f"{title} ({year_match.group('year')})"

    season = int(match.group("season"))
    return Path(prefix) / f"Season {season:02d}"


def get_root_media_destination(media_path: Path, plex_root: Path) -> Optional[Path]:
    """Return the conforming destination for media directly under a library root."""
    root_parts = _root_media_parts(media_path, plex_root)
    if root_parts is None:
        return None

    library_name, _ = root_parts
    if library_name.casefold() == "tv":
        episode_directory = _tv_episode_directory(media_path)
        if episode_directory is None:
            return None
        return media_path.parent / episode_directory / media_path.name

    return media_path.parent / media_path.stem / media_path.name


def get_root_media_organization_skip_reason(media_path: Path, plex_root: Path) -> Optional[str]:
    """Explain why root-level media is intentionally not organized."""
    root_parts = _root_media_parts(media_path, plex_root)
    if root_parts is None or root_parts[0].casefold() != "tv":
        return None
    if get_root_media_destination(media_path, plex_root) is not None:
        return None
    return "skipped: ambiguous TV filename (no confident season/episode show prefix)"


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
    if media_path.is_symlink():
        return False, f"Organization source is a symlink: {media_path}", media_path, []

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
    symlink_sources = [source for source in sources if source.is_symlink()]
    if symlink_sources:
        return False, f"Organization source is a symlink: {symlink_sources[0]}", media_path, []

    destinations = [destination.parent / source.name for source in sources]
    destination_dir = destination.parent
    try:
        destination_parts = destination_dir.relative_to(media_path.parent).parts
    except ValueError:
        return (
            False,
            f"Organization destination escapes library root: {destination}",
            media_path,
            [],
        )

    directory = media_path.parent
    planned_directories = []
    for part in destination_parts:
        directory /= part
        if directory.is_symlink():
            return False, f"Organization path is a symlink: {directory}", media_path, []
        if directory.exists() and not directory.is_dir():
            return False, f"Organization path is not a directory: {directory}", media_path, []
        if not directory.exists():
            planned_directories.append(directory)

    resolved_library_root = media_path.parent.resolve()
    try:
        destination.resolve(strict=False).relative_to(resolved_library_root)
    except ValueError:
        return (
            False,
            f"Organization destination escapes library root: {destination}",
            media_path,
            [],
        )

    conflicts = [path for path in destinations if path.exists()]
    if conflicts:
        return False, f"Organization destination already exists: {conflicts[0]}", media_path, []

    if destination_dir.exists():
        destination_same_stem_media = [
            path
            for path in destination_dir.iterdir()
            if path.is_file()
            and path.suffix.lower() in media_extensions
            and path.stem.casefold() == media_path.stem.casefold()
        ]
        if destination_same_stem_media:
            return (
                False,
                "Media with same stem already exists in organization destination: "
                f"{destination_same_stem_media[0]}",
                media_path,
                [],
            )

    if dry_run:
        return True, f"Would organize into: {destination_dir}", destination, destinations

    moved: list[tuple[Path, Path]] = []
    try:
        destination_dir.mkdir(parents=True, exist_ok=True)
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
        for created_directory in reversed(planned_directories):
            try:
                created_directory.rmdir()
            except OSError:
                pass
        message = f"Organization failed: {e}"
        if rollback_errors:
            message += f"; rollback failed: {'; '.join(rollback_errors)}"
        return False, message, media_path, []

    return True, f"Organized into: {destination_dir}", destination, destinations

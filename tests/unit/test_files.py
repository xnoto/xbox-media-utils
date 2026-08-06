from pathlib import Path

from xbox_media_utils.constants import MEDIA_EXTENSIONS
from xbox_media_utils.files import (
    collect_media_companions,
    get_root_media_destination,
    organize_root_media,
)


def test_get_root_media_destination_only_matches_library_root_files(tmp_path: Path):
    plex_root = tmp_path / "plex"
    root_media = plex_root / "movies" / "Movie.2024.mkv"
    nested_media = plex_root / "movies" / "Movie.2024" / "Movie.2024.mkv"
    outside_media = tmp_path / "downloads" / "Movie.2024.mkv"

    assert get_root_media_destination(root_media, plex_root) == (
        plex_root / "movies" / "Movie.2024" / "Movie.2024.mkv"
    )
    assert get_root_media_destination(nested_media, plex_root) is None
    assert get_root_media_destination(outside_media, plex_root) is None


def test_collect_media_companions_uses_most_specific_media_stem(tmp_path: Path):
    movie = tmp_path / "Movie.mkv"
    movie_part = tmp_path / "Movie.Part2.mkv"
    movie_subtitle = tmp_path / "Movie.en.srt"
    movie_art = tmp_path / "Movie-poster.jpg"
    part_subtitle = tmp_path / "Movie.Part2.en.srt"
    generic_art = tmp_path / "poster.jpg"
    for path in (movie, movie_part, movie_subtitle, movie_art, part_subtitle, generic_art):
        path.write_text(path.name)

    companions = collect_media_companions(movie, MEDIA_EXTENSIONS)

    assert companions == [movie_art, movie_subtitle]


def test_organize_root_media_moves_media_and_attributable_sidecars(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media = library_root / "Movie.2024.mkv"
    subtitle = library_root / "Movie.2024.en.srt"
    artwork = library_root / "Movie.2024-poster.jpg"
    generic_artwork = library_root / "poster.jpg"
    for path in (media, subtitle, artwork, generic_artwork):
        path.write_text(path.name)

    success, message, destination, moved = organize_root_media(media, plex_root, MEDIA_EXTENSIONS)

    destination_dir = library_root / "Movie.2024"
    assert success is True
    assert "Organized into" in message
    assert destination == destination_dir / media.name
    assert moved == [
        destination_dir / media.name,
        destination_dir / artwork.name,
        destination_dir / subtitle.name,
    ]
    assert all(path.exists() for path in moved)
    assert generic_artwork.exists()


def test_organize_root_media_dry_run_does_not_move_files(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media = library_root / "Movie.2024.mkv"
    media.write_text("input")

    success, message, destination, planned = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=True
    )

    assert success is True
    assert "Would organize" in message
    assert destination == library_root / "Movie.2024" / media.name
    assert planned == [destination]
    assert media.exists()
    assert not destination.parent.exists()


def test_organize_root_media_refuses_destination_collision(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    destination_dir = library_root / "Movie.2024"
    destination_dir.mkdir(parents=True)
    media = library_root / "Movie.2024.mkv"
    destination = destination_dir / media.name
    media.write_text("source")
    destination.write_text("existing")

    success, message, returned_path, moved = organize_root_media(media, plex_root, MEDIA_EXTENSIONS)

    assert success is False
    assert "already exists" in message
    assert returned_path == media
    assert moved == []
    assert media.read_text() == "source"
    assert destination.read_text() == "existing"


def test_organize_root_media_refuses_same_stem_media_collision(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    mkv = library_root / "Movie.2024.mkv"
    mp4 = library_root / "Movie.2024.mp4"
    mkv.write_text("mkv")
    mp4.write_text("mp4")

    success, message, returned_path, moved = organize_root_media(mkv, plex_root, MEDIA_EXTENSIONS)

    assert success is False
    assert "Multiple media files" in message
    assert returned_path == mkv
    assert moved == []
    assert mkv.exists()
    assert mp4.exists()


def test_organize_root_media_rolls_back_partial_move(tmp_path: Path, monkeypatch):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    library_root.mkdir(parents=True)
    media = library_root / "Movie.2024.mkv"
    subtitle = library_root / "Movie.2024.en.srt"
    media.write_text("media")
    subtitle.write_text("subtitle")
    real_rename = Path.rename

    def fail_subtitle_move(path, target):
        if path == subtitle:
            raise OSError("simulated subtitle move failure")
        return real_rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_subtitle_move)

    success, message, returned_path, moved = organize_root_media(media, plex_root, MEDIA_EXTENSIONS)

    assert success is False
    assert "simulated subtitle move failure" in message
    assert returned_path == media
    assert moved == []
    assert media.read_text() == "media"
    assert subtitle.read_text() == "subtitle"
    assert not (library_root / "Movie.2024").exists()

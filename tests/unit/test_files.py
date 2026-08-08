from pathlib import Path

import pytest

import xbox_media_utils.files as files_module
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


def test_get_root_media_destination_places_tv_episode_in_show_season(tmp_path: Path):
    plex_root = tmp_path / "plex"
    media = plex_root / "TV" / "The.Show.S01E02.Episode.Title.mkv"

    assert get_root_media_destination(media, plex_root) == (
        plex_root / "TV" / "The Show" / "Season 01" / media.name
    )


def test_get_root_media_destination_supports_tv_specials_and_multi_episode_names(
    tmp_path: Path,
):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    names = [
        "The.Show.S00E01.Special.mkv",
        "The.Show.S01E01-E02.Double.Feature.mkv",
        "The.Show.S01E01E02.Double.Feature.mkv",
    ]

    destinations = [get_root_media_destination(library_root / name, plex_root) for name in names]

    assert destinations == [
        library_root / "The Show" / "Season 00" / names[0],
        library_root / "The Show" / "Season 01" / names[1],
        library_root / "The Show" / "Season 01" / names[2],
    ]


def test_get_root_media_destination_cleans_tv_years(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    bare_year = library_root / "The_Show.2024.S02E03.mkv"
    parenthesized_year = library_root / "The.Show.(2023).S01E01.mkv"

    assert get_root_media_destination(bare_year, plex_root) == (
        library_root / "The Show (2024)" / "Season 02" / bare_year.name
    )
    assert get_root_media_destination(parenthesized_year, plex_root) == (
        library_root / "The Show (2023)" / "Season 01" / parenthesized_year.name
    )


def test_get_root_media_destination_leaves_ambiguous_tv_name_at_root(tmp_path: Path):
    plex_root = tmp_path / "plex"
    media = plex_root / "tv" / "Unsorted Recording.mkv"

    assert get_root_media_destination(media, plex_root) is None


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


def test_organize_root_tv_media_moves_sidecars_but_not_generic_artwork(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    library_root.mkdir(parents=True)
    media = library_root / "The.Show.S01E01.mkv"
    subtitle = library_root / "The.Show.S01E01.en.srt"
    generic_artwork = library_root / "poster.jpg"
    for path in (media, subtitle, generic_artwork):
        path.write_text(path.name)

    success, _, destination, moved = organize_root_media(media, plex_root, MEDIA_EXTENSIONS)

    destination_dir = library_root / "The Show" / "Season 01"
    assert success is True
    assert destination == destination_dir / media.name
    assert moved == [destination, destination_dir / subtitle.name]
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


@pytest.mark.parametrize("dry_run", [True, False])
def test_organize_root_media_refuses_media_symlink(tmp_path: Path, dry_run: bool):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    source_target = tmp_path / "source.mkv"
    library_root.mkdir(parents=True)
    source_target.write_text("target media")
    media = library_root / "Movie.2024.mkv"
    media.symlink_to(source_target)

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=dry_run
    )

    assert success is False
    assert message == f"Organization source is a symlink: {media}"
    assert returned_path == media
    assert moved == []
    assert media.is_symlink()
    assert source_target.read_text() == "target media"
    assert not (library_root / "Movie.2024").exists()


@pytest.mark.parametrize("dry_run", [True, False])
def test_organize_root_media_refuses_companion_symlink(tmp_path: Path, dry_run: bool):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    companion_target = tmp_path / "subtitle.srt"
    library_root.mkdir(parents=True)
    companion_target.write_text("target subtitle")
    media = library_root / "Movie.2024.mkv"
    media.write_text("source media")
    companion = library_root / "Movie.2024.en.srt"
    companion.symlink_to(companion_target)

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=dry_run
    )

    assert success is False
    assert message == f"Organization source is a symlink: {companion}"
    assert returned_path == media
    assert moved == []
    assert media.read_text() == "source media"
    assert companion.is_symlink()
    assert companion_target.read_text() == "target subtitle"
    assert not (library_root / "Movie.2024").exists()


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


def test_organize_root_media_dry_run_refuses_movie_destination_symlink(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    outside = tmp_path / "outside"
    library_root.mkdir(parents=True)
    outside.mkdir()
    media = library_root / "Movie.2024.mkv"
    media.write_text("source")
    (library_root / media.stem).symlink_to(outside, target_is_directory=True)

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=True
    )

    assert success is False
    assert "path is a symlink" in message
    assert returned_path == media
    assert moved == []
    assert media.exists()
    assert list(outside.iterdir()) == []


def test_organize_root_media_dry_run_refuses_nested_tv_destination_symlink(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    show_directory = library_root / "The Show"
    outside = tmp_path / "outside"
    show_directory.mkdir(parents=True)
    outside.mkdir()
    media = library_root / "The.Show.S01E01.mkv"
    media.write_text("source")
    (show_directory / "Season 01").symlink_to(outside, target_is_directory=True)

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=True
    )

    assert success is False
    assert "path is a symlink" in message
    assert returned_path == media
    assert moved == []
    assert media.exists()
    assert list(outside.iterdir()) == []


def test_organize_root_media_refuses_resolved_destination_outside_library(
    tmp_path: Path, monkeypatch
):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "movies"
    outside = tmp_path / "outside"
    library_root.mkdir(parents=True)
    outside.mkdir()
    media = library_root / "Movie.2024.mkv"
    media.write_text("source")
    escaped_destination = outside / media.name
    monkeypatch.setattr(
        files_module,
        "get_root_media_destination",
        lambda media_path, root: escaped_destination,
    )

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=True
    )

    assert success is False
    assert "destination escapes library root" in message
    assert returned_path == media
    assert moved == []
    assert media.exists()


@pytest.mark.parametrize("dry_run", [True, False])
def test_organize_root_media_refuses_destination_same_stem_media(tmp_path: Path, dry_run: bool):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    destination_dir = library_root / "The Show" / "Season 01"
    destination_dir.mkdir(parents=True)
    media = library_root / "The.Show.S01E01.mkv"
    existing_media = destination_dir / "the.show.s01e01.MP4"
    media.write_text("source")
    existing_media.write_text("existing")

    success, message, returned_path, moved = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=dry_run
    )

    assert success is False
    assert "same stem already exists" in message
    assert returned_path == media
    assert moved == []
    assert media.read_text() == "source"
    assert existing_media.read_text() == "existing"


def test_organize_root_media_accepts_existing_season_with_unrelated_episode(tmp_path: Path):
    plex_root = tmp_path / "plex"
    library_root = plex_root / "tv"
    destination_dir = library_root / "The Show" / "Season 01"
    destination_dir.mkdir(parents=True)
    media = library_root / "The.Show.S01E02.mkv"
    existing_episode = destination_dir / "The.Show.S01E01.mkv"
    media.write_text("source")
    existing_episode.write_text("existing")

    success, message, destination, planned = organize_root_media(
        media, plex_root, MEDIA_EXTENSIONS, dry_run=True
    )

    assert success is True
    assert "Would organize" in message
    assert destination == destination_dir / media.name
    assert planned == [destination]
    assert media.exists()
    assert existing_episode.exists()


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

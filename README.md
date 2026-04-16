# xbox-media-utils

CLI tools to make media playable via **Direct Play** on Xbox Series X through Plex.
Handles transcoding, audio normalization, subtitle extraction, and Dolby Vision compatibility.

## Why

Xbox Series X has specific limitations that force transcoding:

- Video: Only H.264, HEVC, VP9 supported natively
- Audio: normalize anything that is not already AAC stereo to AAC stereo; this includes Opus/DTS/TrueHD, mono tracks, and >2ch audio
- Subtitles: 4K + embedded subs = forced transcode
- Dolby Vision: direct play / transcode support is unreliable on Plex for Xbox

These tools pre-process media to avoid server-side transcoding.

## Install

```bash
uv tool install git+https://github.com/xnoto/xbox-media-utils.git
```

Update: `uv tool upgrade xbox-media-utils`

## Tools

- `xbox-recode` - In-place processor for existing libraries
- `xbox-import` - Import new media with proper structure
- `xbox-plex-scan` - Trigger Plex library scans via HTTP API

### xbox-recode

In-place processor for existing libraries.

```bash
# Scan what needs processing
xbox-recode scan /path/to/library

# Process everything
xbox-recode process /path/to/library

# Resync an entire library after compatibility rules change
xbox-recode process /mnt/media/plex/movies
xbox-recode process /mnt/media/plex/tv

# Single file
xbox-recode process /path/to/file.mkv --file

# Dry run (see what would happen)
xbox-recode process /path/to/library --dry-run

# Software only (no VAAPI)
xbox-recode process /path/to/library --no-hardware
```

**What it does:**

- Video: Pass-through H.264/HEVC unless Dolby Vision is present; transcode others to HEVC via VAAPI
  (with MPEG-4 fallback)
- Audio: Copy already-compatible AAC stereo; recode non-AAC stereo, all mono tracks, and all >2ch tracks to AAC 256k stereo
- Subtitles: Extract to sidecar files (SRT/ASS), OCR PGS/SUP via pgsrip
- Dolby Vision: Force video recode for all DoVi content; during recode, DoVi Profile 8 files with no other work needed can promote an `.HDR10.mkv` copy to the main `.mkv` name and archive the original as `.DV.mkv`
- Replaces originals after validation

### xbox-import

Import new media with proper structure.

```bash
# Import to movies library (default)
xbox-import Movie.2024.1080p/

# Import to TV library
xbox-import Show.S01/ --library tv

# Custom plex root
xbox-import Movie/ --plex /mnt/media/plex

# Dry run
xbox-import Movie/ --dry-run
```

**What it does:**

- Same processing as `recode` but copies instead of replacing
- Preserves directory structure
- Sets ownership on destination
- Creates parent directories as needed

### xbox-plex-scan

Trigger Plex library scans via HTTP API. Useful after importing or moving files.

```bash
# Partial scan by path (auto-detects library section)
xbox-plex-scan /path/to/library/Some.Movie.2024

# Full scan specific section keys
xbox-plex-scan --sections 6 9 10

# List all library sections
xbox-plex-scan --list
```

**What it does:**

- Resolves filesystem paths to Plex library sections
- Triggers partial scans (by path) or full scans (by section key)
- Uses Plex HTTP API with token from env var or Preferences.xml

## Configuration

Environment variables (optional):

```bash
# General
XBOX_PLEX_ROOT=~/plex                    # Default: ~/plex
XBOX_PLEX_USER=plex                      # Default: plex
XBOX_PLEX_GROUP=media                    # Default: libstoragemgmt

# Logging
XBOX_RECODE_LOG_DIR=/var/log/recode      # Default: /var/log/xbox-recode
XBOX_IMPORT_LOG_DIR=/var/log/import      # Default: /var/log/xbox-import
XBOX_RECODE_LOCK_FILE=/var/run/lock      # Default: /var/run/xbox-recode.lock

# Plex Scanner
XBOX_PLEX_URL=http://localhost:32400     # Plex server URL
XBOX_PLEX_TOKEN=xxxxxxxx                 # Plex auth token (or use PLEX_TOKEN)
XBOX_PLEX_PREFS_PATH=/var/lib/plexmediaserver/...  # Path to Preferences.xml
```

## Requirements

- Python 3.9+
- ffmpeg with VAAPI support (optional but recommended)
- AMD/Intel GPU with VAAPI HEVC encode support
- pgsrip, babelfish (auto-installed)

## Log Files

```
/var/log/xbox-recode/recode-YYYY-MM-DD.jsonl
/var/log/xbox-import/import-YYYY-MM-DD.jsonl
```

JSON Lines format with processing results for each file.

## License

MIT

## Development

```bash
# Install in development mode
uv pip install -e ".[dev]"

# Run tests
uv run pytest

# Run linting
uv run ruff check .
uv run mypy src/
```

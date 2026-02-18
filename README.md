# xbox-media-utils

CLI tools to make media playable via **Direct Play** on Xbox Series X through Plex.
Handles transcoding, audio downmixing, subtitle extraction, and Dolby Vision compatibility.

## Why

Xbox Series X has specific limitations that force transcoding:

- Video: Only H.264, HEVC, VP9 supported natively
- Audio: No DTS/TrueHD decode; >2ch requires passthrough
- Subtitles: 4K + embedded subs = forced transcode
- Dolby Vision Profile 8: Crashes Plex app

These tools pre-process media to avoid server-side transcoding.

## Install

```bash
uv tool install git+https://github.com/xnoto/xbox-media-utils.git
```

Update: `uv tool upgrade xbox-media-utils`

## Tools

### xbox-recode

In-place processor for existing libraries.

```bash
# Scan what needs processing
xbox-recode scan /path/to/library

# Process everything
xbox-recode process /path/to/library

# Single file
xbox-recode process /path/to/file.mkv --file

# Dry run (see what would happen)
xbox-recode process /path/to/library --dry-run

# Software only (no VAAPI)
xbox-recode process /path/to/library --no-hardware
```

**What it does:**

- Video: Pass-through H.264/HEVC; transcode others to HEVC via VAAPI
  (with MPEG-4 fallback)
- Audio: Copy stereo; downmix >2ch to AAC 256k stereo
- Subtitles: Extract to sidecar files (SRT/ASS), OCR PGS/SUP via pgsrip
- DoVi P8: Create `.HDR10.mkv` sidecar with RPU stripped
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

## Configuration

Environment variables (optional):

```bash
XBOX_PLEX_ROOT=~/plex                    # Default: ~/plex
XBOX_PLEX_USER=plex                      # Default: plex
XBOX_PLEX_GROUP=media                    # Default: libstoragemgmt
XBOX_RECODE_LOG_DIR=/var/log/recode      # Default: /var/log/xbox-recode
XBOX_IMPORT_LOG_DIR=/var/log/import      # Default: /var/log/xbox-import
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

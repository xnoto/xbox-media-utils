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

Conformance processor for existing libraries.

```bash
# Scan what needs processing
xbox-recode scan /path/to/library

# Process everything
xbox-recode process /path/to/library

# Process archived DoVi backups only (copy video, normalize audio/subtitles)
xbox-recode process-backups /path/to/plex/backup

# Resync an entire library after compatibility rules change
xbox-recode process /mnt/media/plex/movies
xbox-recode process /mnt/media/plex/tv

# Single file
xbox-recode process /path/to/file.mkv --file

# Dry run (see what would happen)
xbox-recode process /path/to/library --dry-run

# Software only (no VAAPI)
xbox-recode process /path/to/library --no-hardware

# Process without triggering a Plex scan
xbox-recode process /path/to/library --no-plex-scan
```

**What it does:**

- Video: Pass through H.264 through 1080p60 and HEVC/VP9 through 4K60 when their profiles and bit
  depths are Xbox-compatible; transcode other video to HEVC
- Converts H.264 above 1080p to HEVC while preserving its resolution and frame rate when they are
  already within 4K60; only video above 4K or 60 fps is reduced to the 4K60 media-app limit
- Uses VAAPI where supported, with software fallback for MPEG-4 and filtered/10-bit conversions
- Audio: Copy already-compatible AAC stereo; recode non-AAC stereo, all mono tracks, and all >2ch tracks to AAC 256k stereo
- Subtitles: Extract to sidecar files (SRT/ASS), OCR PGS/SUP via pgsrip
- Dolby Vision: For DoVi Profile 8, create an HDR10-only copy, promote/process it as the main `.mkv`, and archive the original outside the Plex library under the DoVi backup root
- Organization: Move movie/other media directly under a library root into a same-stem directory. In a `tv` library, conventional `SxxEyy` names (including specials and multi-episode names) go to `<Show Name>/Season NN`; ambiguous TV names stay at the library root. Media filenames are unchanged, and only attributable same-stem sidecars move with them.
- Replaces originals after validation
- After all target files process successfully, triggers one partial Plex scan for the target directory; organized single-file targets scan the original library directory so Plex removes the old root entry and finds the new child
- Skips the automatic scan for dry runs, failed processing, or when `--no-plex-scan` is used

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

# Import without triggering a Plex scan
xbox-import Movie/ --no-plex-scan
```

**What it does:**

- Same processing as `recode` but copies instead of replacing
- Places a standalone source file in its own same-named directory
- Preserves directory structure
- Sets ownership on destination
- Creates parent directories as needed
- After all files import successfully, triggers one partial Plex scan for the imported directory
- Skips the automatic scan for dry runs, failed imports, or when `--no-plex-scan` is used

### xbox-plex-scan

Trigger Plex library scans via HTTP API. Useful for manual scans after moving files or imports made
with `--no-plex-scan`.

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
XBOX_DOVI_BACKUP_ROOT=~/plex/backup      # Default: $XBOX_PLEX_ROOT/backup
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
- MKVToolNix (`mkvmerge`) for timestamp-safe Matroska remuxing
- ffmpeg with VAAPI support (optional but recommended)
- AMD/Intel GPU with VAAPI HEVC encode support
- pgsrip, babelfish (auto-installed)

## Log Files

```
/var/log/xbox-recode/recode-YYYY-MM-DD.jsonl
/var/log/xbox-import/import-YYYY-MM-DD.jsonl
```

JSON Lines format with processing results for each file.

## Resumable Library Processing

Long-running library recodes should run one Plex section at a time under the packaged systemd
template. The service waits between files while Plex video playback or ROCm compute/VRAM is busy.
While FFmpeg is running it continues polling Plex, pauses the recode process group if Direct Play,
Direct Stream, or transcoding begins, and resumes after playback finishes. The service checks Plex
every five seconds. Otherwise it runs at full speed with idle-class/low-weight I/O scheduling. It
restarts after abnormal termination or reboot and relies on
`xbox-recode` to recover transactional `.xbox.mkv`/`.bak` artifacts before resuming.

Install the unit after upgrading the uv tool:

```bash
unit=$(sudo -H /root/.local/bin/xbox-recode service-unit)
sudo install -m 0644 "$unit" /etc/systemd/system/xbox-recode-library@.service
printf 'XBOX_PLEX_ROOT=/path/to/plex\n' | sudo tee /etc/sysconfig/xbox-recode
sudo systemctl daemon-reload
```

Set `XBOX_PLEX_ROOT` to the local Plex root, then run only one active section at a time in this
order. Do not target the Plex root itself because it may contain a separately managed `backup` tree.

```bash
sudo systemctl enable --now xbox-recode-library@movies.service
# After movies completes and its failures are reviewed:
sudo systemctl disable xbox-recode-library@movies.service
sudo systemctl enable --now xbox-recode-library@tv.service
# Then repeat for other.
```

Inspect progress without changing the running job:

```bash
sudo systemctl status xbox-recode-library@movies.service
sudo journalctl -u xbox-recode-library@movies.service -f
sudo xbox-recode status
sudo xbox-recode status --json
```

Completed files are compatible on the next scan and are skipped after a restart. A normal nonzero
exit caused by permanent file failures is deliberately not restarted in a loop; an operator should
review `xbox-recode status` before restarting. Stopping the service may interrupt the current file,
but the original remains recoverable and the next start reconciles partial artifacts.

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

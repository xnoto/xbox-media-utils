# AGENTS.md

## Project Context

**This is a PERSONAL toolset, not production software.**

These are scripts developed for a specific home server setup (Plex on Xbox Series X). While they work and are shared publicly, they're maintained for personal use first. Don't over-engineer them or treat them like enterprise software.

## Why No PyPI?

- **Personal tools**: Built for a specific hardware/software combo (Radeon VII VAAPI, Plex, Xbox)
- **Niche use case**: Most users don't need Xbox-specific media processing
- **Maintenance overhead**: PyPI requires careful versioning, security updates, support burden
- **Git-based works fine**: `uv tool install git+...` is sufficient for the handful of people who might use this

## Architecture Decisions

### Code Organization

```
src/xbox_media_utils/
├── api/
│   └── plex.py         # Plex HTTP API client
├── cli/
│   ├── common.py       # Shared CLI utilities
│   ├── import_.py      # CLI: import tool with post-import Plex scan
│   ├── plex_scan.py    # CLI: plex scanner
│   └── recode.py       # CLI: in-place processor
├── core/
│   ├── config.py       # Configuration with env var fallbacks
│   ├── locking.py      # File locking utilities
│   └── logging.py      # Structured JSONL logging
├── constants.py        # CODEC sets, language maps
├── models.py           # Dataclasses (MediaInfo, AudioTrack, etc.)
├── media.py            # Probing, analysis logic
├── ffmpeg.py           # FFmpeg command building
├── subtitles.py        # OCR and extraction
├── hdr.py              # Dolby Vision handling
└── files.py            # File operations
```

**Key principle**:

- Shared logic in modules (`api/`, `core/`, `media.py`, etc.)
- CLI-specific code isolated in `cli/` package
- Entry points configured in `pyproject.toml`

### No Global State

Old code had `QUIET_MODE = False` globals. Refactored to pass parameters explicitly:

```python
# Bad
def log(msg):
    if not QUIET_MODE:  # Global!
        print(msg)


# Good
def log(msg, quiet: bool = False):  # Explicit
    if not quiet:
        print(msg)
```

### Hardcoded Paths (Addressed)

Original code had server-specific paths hardcoded. Fixed with environment variable fallbacks:

```python
DEFAULT_PLEX_ROOT = os.environ.get("XBOX_PLEX_ROOT", "~/plex")
```

The VAAPI device (`/dev/dri/renderD128`) is still hardcoded—this is standard on Linux systems with AMD/Intel GPUs. If users have different setups, they'll need to patch or we can add another env var.

## Common Issues

### VAAPI MPEG-4 Failures

Radeon VII (and many AMD cards) can't hardware-decode MPEG-4/XviD. The code has fallback logic:

```python
def run_ffmpeg_with_fallback(info, output_path):
    # Try VAAPI first
    # If fails with hwaccel errors, retry with software
```

If you see "VAAPI failed, falling back to software decode" in logs, this is working as intended.

### Xbox Video Limits

The media-app target is codec-specific: H.264 is supported through 1080p60, while HEVC Main/Main10
and VP9 are supported through 4K60. H.264 above 1080p is converted to HEVC without reducing its
resolution or frame rate when already within 4K60. Scaling or frame-rate limiting applies only above
the 4K60 output envelope. Keep this distinct from the console's 4K/120 gaming output capability.

### OCR Timeouts

pgsrip can hang on corrupted PGS streams. We use SIGALRM for 10-minute timeout. If OCR fails, the SUP file is kept as fallback (though Plex won't use it).

### Post-Processing Plex Scans

`xbox-import` triggers one partial Plex scan after all files import successfully. It does not scan
after a failed import or dry run, and `--no-plex-scan` disables the behavior. A failed scan makes the
command exit nonzero even though the imported files remain in place.

`xbox-recode process` follows the same rules after successfully processing its target. Directory
targets scan that directory, while single-file targets scan their parent directory. Archived DoVi
backup processing does not trigger Plex scans.

Media files directly under `<plex-root>/<library>/` are out of conformance. `xbox-recode process`
moves movie/other files and attributable same-stem sidecars into `<library>/<media-stem>/` before
processing. In the case-insensitive `tv` library, conventional `SxxEyy` filenames, including season
00 and multi-episode forms, move without filename changes into `<Show Name>/Season NN`; ambiguous TV
filenames stay at the library root and continue through normal recode processing. Dry runs perform
the same organization and collision preflight without moving files, collisions fail without
overwriting, organized single-file runs scan the original library directory, and `process-backups`
never reorganizes archive content.

### Lock Files

`xbox-recode` uses `flock` on a configurable lock path (default `/var/run/xbox-recode.lock`) to prevent concurrent runs. The kernel releases the lock when the owning process exits, including after a crash. Never delete the pathname merely because it exists: deleting an actively locked file can let another process lock a new inode and run concurrently. If lock acquisition fails, inspect the owning process and configuration, then retry normally.

### Long-Running Library Recodes

Use the packaged `xbox-recode-library@.service` template for multi-day processing, with only one of
`movies`, `tv`, or `other` active at a time. The service waits for idle Plex video playback and ROCm
compute/VRAM between files, pauses an active FFmpeg process if Direct Play, Direct Stream, or
transcoding begins, and otherwise runs with reduced CPU/I/O priority. Plex playback is polled every
five seconds. `xbox-recode status --json` is the agent-facing progress
interface. Do not process `<plex-root>` as one root because it may include the separately managed
`backup` tree. After interruption, allow artifact recovery to reconcile `.xbox.mkv` and `.bak` files
while holding the normal recode lock.

## Testing Strategy

Unit tests exist for core modules. Run with pytest:

```bash
# Run all tests
uv run pytest

# Run specific test file
uv run pytest tests/unit/test_locking.py -v

# Run with coverage
uv run pytest --cov=src/xbox_media_utils

# Run the complete contributor checks
uv run pre-commit run --all-files
```

For manual integration testing:

```bash
# Build and test locally
uv run xbox-recode --help
uv run xbox-recode process /path/to/test/media --dry-run

# Test specific scenarios
# 1. MPEG-4 file (triggers VAAPI fallback)
# 2. Dolby Vision file (forces video recode; Profile 8 also creates HDR10 copy)
# 3. Multi-track audio (5.1/7.1/mono -> AAC stereo)
# 4. PGS subtitles (OCR to SRT)
```

## Adding Features

**Before adding complexity, ask:**

1. Does this solve a problem *I* actually have?
2. Can it be done with a simple shell wrapper instead?
3. Will this break existing workflows?

**Good additions:**

- New codec support (AV1, etc.)
- Better error messages
- Environment variables for more paths

**Bad additions:**

- GUI
- Database backends
- Cloud storage integration
- Webhooks/notifications

## Release Process

Work on a feature branch; `main` is protected. Commit messages follow Conventional Commits. Run the configured lint, type, secret, dead-code, and test hooks locally before requesting review; GitHub does not currently enforce those status checks, so do not assume a pull request ran them.

1. Tag with version: `git tag -a v0.2.0 -m "Add AV1 support"`
2. Push tag: `git push origin v0.2.0`
3. Done. No PyPI, no release notes, no artifacts.

Users install via:

```bash
uv tool install git+https://github.com/xnoto/xbox-media-utils.git@v0.2.0
```

## Dependencies to Watch

- **pgsrip**: OCR library. If it breaks, subtitle extraction breaks.
- **ffmpeg**: Default binaries are supplied through `static-ffmpeg`. VAAPI availability still depends on the selected binary and host drivers; do not assume the system `ffmpeg` is used.
- **babelfish**: Language code handling. Rarely changes.

## Server Context

Current deployment target:

- OS: Ubuntu/Debian Linux
- GPU: AMD Radeon VII (VAAPI)
- Plex: Running as `plex:libstoragemgmt`

If this changes (new GPU, different paths), update defaults accordingly.

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
├── constants.py   # CODEC sets, language maps
├── models.py      # Dataclasses (MediaInfo, AudioTrack, etc.)
├── media.py       # Probing, analysis logic
├── ffmpeg.py      # FFmpeg command building
├── subtitles.py   # OCR and extraction
├── hdr.py         # Dolby Vision handling
├── files.py       # File operations
├── recode.py      # CLI: in-place processor
└── import_.py     # CLI: import tool
```

**Key principle**: Shared logic in modules, CLI-specific code in `recode.py`/`import_.py`.

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

### OCR Timeouts

pgsrip can hang on corrupted PGS streams. We use SIGALRM for 10-minute timeout. If OCR fails, the SUP file is kept as fallback (though Plex won't use it).

### Lock Files

`xbox-recode` uses `/var/run/xbox-recode.lock` to prevent concurrent runs. If a run crashes, the lock may stale. Manually delete it:

```bash
sudo rm /var/run/xbox-recode.lock
```

## Testing Strategy

Since these are personal scripts, no unit tests. Test manually:

```bash
# Build and test locally
uv run xbox-recode --help
uv run xbox-recode scan /path/to/test/media --dry-run

# Test specific scenarios
# 1. MPEG-4 file (triggers VAAPI fallback)
# 2. DoVi Profile 8 file (creates HDR10 copy)
# 3. Multi-track audio (5.1 DTS -> AAC stereo)
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

1. Tag with version: `git tag -a v0.2.0 -m "Add AV1 support"`
2. Push tag: `git push origin v0.2.0`
3. Done. No PyPI, no release notes, no artifacts.

Users install via:

```bash
uv tool install git+https://github.com/xnoto/xbox-media-utils.git@v0.2.0
```

## Dependencies to Watch

- **pgsrip**: OCR library. If it breaks, subtitle extraction breaks.
- **ffmpeg**: External dependency. VAAPI support varies by build.
- **babelfish**: Language code handling. Rarely changes.

## Server Context

Current deployment target:

- OS: Ubuntu/Debian Linux
- GPU: AMD Radeon VII (VAAPI)
- Plex: Running as `plex:libstoragemgmt`

If this changes (new GPU, different paths), update defaults accordingly.

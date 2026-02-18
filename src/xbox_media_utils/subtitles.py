"""Subtitle extraction and OCR utilities."""

import signal
from pathlib import Path
from typing import Optional

from .constants import LANG_CODE_MAP
from .media import run_cmd
from .models import MediaInfo


class OcrTimeoutError(Exception):
    """Raised when OCR exceeds the allowed timeout."""


class OcrAlarmHandler:
    """Context manager for OCR timeout handling."""

    def __init__(self, timeout: int):
        self.timeout = timeout
        self.old_handler = None

    def __enter__(self):
        self.old_handler = signal.signal(signal.SIGALRM, self._alarm_handler)
        signal.alarm(self.timeout)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        signal.alarm(0)
        signal.signal(signal.SIGALRM, self.old_handler)
        return False

    @staticmethod
    def _alarm_handler(signum, frame):
        raise OcrTimeoutError("OCR timed out")


def normalize_lang_code(lang_3: str) -> Optional[str]:
    """Convert 3-letter ISO 639-2 code to 2-letter ISO 639-1 code.

    Returns None if input is not a recognized language code.
    """
    return LANG_CODE_MAP.get(lang_3.lower())


def detect_sub_language(sup_path: Path) -> str:
    """Extract 2-letter language code from SUP filename. Defaults to 'en'."""
    from .constants import TESSERACT_LANG_MAP

    parts = sup_path.stem.split(".")
    for part in reversed(parts):
        if part.isdigit() or part in ("forced", "sdh", "cc", "un"):
            continue
        if len(part) == 2 and part.isalpha():
            if part.lower() in TESSERACT_LANG_MAP:
                return part.lower()
        elif len(part) == 3 and part.isalpha():
            normalized = normalize_lang_code(part)
            if normalized is not None:
                return normalized
    return "en"


def ocr_sup_to_srt(sup_path: Path, timeout: int = 600) -> tuple[bool, str, Optional[Path]]:
    """Convert SUP (PGS) file to SRT using pgsrip as a library.

    Returns: (success, message, srt_path)
    """
    if not sup_path.exists():
        return False, f"SUP file not found: {sup_path}", None

    final_srt = sup_path.with_suffix(".srt")
    lang_2 = detect_sub_language(sup_path)

    try:
        with OcrAlarmHandler(timeout):
            from babelfish import Language
            from pgsrip.media import Pgs
            from pgsrip.media_path import MediaPath
            from pgsrip.options import Options as PgsripOptions
            from pgsrip.ripper import PgsToSrtRipper

            # Build MediaPath manually and override language
            media_path = MediaPath(str(sup_path))
            media_path.language = Language.fromietf(lang_2)

            options = PgsripOptions(overwrite=True, one_per_lang=False)

            pgs = Pgs(
                media_path=media_path,
                options=options,
                data_reader=lambda: sup_path.read_bytes(),
                temp_folder="",
            )

            if not pgs.items:
                return False, "No PGS subtitle data found in SUP file", None

            ripper = PgsToSrtRipper(pgs, options)
            subs = ripper.rip(post_process=None)

            if not subs or len(subs) == 0:
                return False, "OCR produced no subtitle entries", None

            subs.save(str(final_srt), encoding="utf-8")

            if final_srt.stat().st_size < 100:
                final_srt.unlink()
                return False, "SRT file too small (OCR likely failed)", None

            return True, "OCR successful", final_srt

    except OcrTimeoutError:
        if final_srt.exists():
            final_srt.unlink()
        return False, f"OCR timed out after {timeout}s", None
    except Exception as e:
        if final_srt.exists():
            final_srt.unlink()
        return False, f"OCR error: {e}", None


def extract_subtitles(info: MediaInfo, output_base: Path, logger=print) -> list[dict]:
    """Extract subtitles to sidecar files.

    Returns list of extraction results.
    """
    results = []

    extractable_subs = [s for s in info.subtitle_tracks if s.is_text or s.is_image]
    if not extractable_subs:
        return results

    lang_counts: dict[str, int] = {}

    for sub in extractable_subs:
        lang_3 = sub.language or "und"
        lang = normalize_lang_code(lang_3) or "en"

        # Only extract English or unknown-language subs
        if lang not in ("en", "un"):
            logger(f"    Skipping non-English subtitle track {sub.index} ({lang_3}/{lang})")
            continue

        if lang == "un":
            lang = "en"

        lang_counts[lang] = lang_counts.get(lang, 0) + 1
        count = lang_counts[lang]

        parts = [output_base.stem, lang]
        if count > 1:
            parts.append(str(count))
        if sub.is_forced:
            parts.append("forced")
        if sub.title and any(x in sub.title.lower() for x in ["sdh", "cc", "hearing"]):
            parts.append("sdh")

        # Determine output extension
        if sub.is_image:
            ext = ".sup"
            codec_out = "copy"
        else:
            if sub.codec in ("ass", "ssa"):
                ext = ".ass"
                codec_out = "copy"
            elif sub.codec in ("subrip", "srt"):
                ext = ".srt"
                codec_out = "copy"
            else:
                ext = ".srt"
                codec_out = "srt"

        output_path = output_base.parent / ((".".join(parts)) + ext)

        cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(info.path),
            "-map",
            f"0:{sub.index}",
            "-c:s",
            codec_out,
            str(output_path),
        ]

        result = run_cmd(cmd)

        extract_result = {
            "track_index": sub.index,
            "language": lang,
            "codec": sub.codec,
            "type": "image" if sub.is_image else "text",
            "output": str(output_path),
            "success": result.returncode == 0,
            "error": result.stderr if result.returncode != 0 else None,
            "ocr_performed": False,
        }

        if result.returncode == 0:
            # If image-based, OCR to SRT
            if sub.is_image:
                logger(f"    Extracted PGS: {output_path.name}, running OCR...")
                ocr_success, ocr_msg, srt_path = ocr_sup_to_srt(output_path)
                extract_result["ocr_performed"] = True
                extract_result["ocr_success"] = ocr_success
                extract_result["ocr_message"] = ocr_msg

                if ocr_success and srt_path:
                    output_path.unlink()
                    extract_result["output"] = str(srt_path)
                    extract_result["sup_deleted"] = True
                    logger(f"    OCR complete: {srt_path.name}")
                else:
                    logger(f"    OCR failed: {ocr_msg}, keeping SUP")
                    extract_result["sup_deleted"] = False
            else:
                logger(f"    Extracted subtitle: {output_path.name}")
        else:
            logger(f"    Failed to extract subtitle track {sub.index}: {result.stderr}")

        results.append(extract_result)

    return results

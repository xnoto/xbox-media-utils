"""Codec and language constants for xbox-media-utils."""

# Compatible codecs (Xbox Direct Play supported)
# Reference: https://support.plex.tv/articles/203824396-what-media-formats-are-supported/
COMPATIBLE_VIDEO_CODECS = {"h264", "hevc", "vp9"}

# Codecs that VAAPI hardware decoding cannot handle (will fallback to software)
# Radeon VII VAAPI driver doesn't support MPEG-4 (XviD/DivX) decoding
VAAPI_INCOMPATIBLE_CODECS = {"mpeg4", "msmpeg4v1", "msmpeg4v2", "msmpeg4v3"}

# Text-based subtitle codecs that can be extracted as-is
TEXT_SUBTITLE_CODECS = {
    "subrip",
    "srt",
    "ass",
    "ssa",
    "mov_text",
    "webvtt",
    "text",
    "sami",
}

# Image-based subtitle codecs - extract and OCR to SRT
IMAGE_SUBTITLE_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvb_subtitle",
    "pgs",
    "vobsub",
}

# Media file extensions
MEDIA_EXTENSIONS = {".mkv", ".mp4", ".avi", ".m4v", ".mov", ".wmv", ".ts", ".m2ts"}

# ISO 639-2 (3-letter) to ISO 639-1 (2-letter) mapping for pgsrip compatibility
# pgsrip requires 2-letter codes in filenames
LANG_CODE_MAP = {
    "eng": "en",
    "spa": "es",
    "fre": "fr",
    "fra": "fr",
    "deu": "de",
    "ger": "de",
    "ita": "it",
    "por": "pt",
    "jpn": "ja",
    "chi": "zh",
    "zho": "zh",
    "kor": "ko",
    "rus": "ru",
    "ara": "ar",
    "nld": "nl",
    "dut": "nl",
    "swe": "sv",
    "dan": "da",
    "nor": "no",
    "fin": "fi",
    "pol": "pl",
    "tur": "tr",
    "tha": "th",
    "vie": "vi",
    "hun": "hu",
    "ces": "cs",
    "cze": "cs",
    "ell": "el",
    "gre": "el",
    "heb": "he",
    "hin": "hi",
    "ind": "id",
    "msa": "ms",
    "may": "ms",
    "ron": "ro",
    "rum": "ro",
    "ukr": "uk",
    "bul": "bg",
    "hrv": "hr",
    "slk": "sk",
    "slo": "sk",
    "slv": "sl",
    "srp": "sr",
    "cat": "ca",
    "eus": "eu",
    "baq": "eu",
    "glg": "gl",
    "lit": "lt",
    "lav": "lv",
    "est": "et",
    "isl": "is",
    "ice": "is",
    "mlt": "mt",
    "cym": "cy",
    "wel": "cy",
    "gle": "ga",
    "iri": "ga",
    "und": "un",
}

# Tesseract language codes (ISO 639-3)
# Maps 2-letter codes to tesseract language names
TESSERACT_LANG_MAP = {
    "en": "eng",
    "es": "spa",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "ja": "jpn",
    "zh": "chi_sim",
    "ko": "kor",
    "ru": "rus",
    "ar": "ara",
    "nl": "nld",
    "sv": "swe",
    "da": "dan",
    "no": "nor",
    "fi": "fin",
    "pl": "pol",
    "tr": "tur",
    "th": "tha",
    "vi": "vie",
    "hu": "hun",
    "cs": "ces",
    "el": "ell",
    "he": "heb",
    "hi": "hin",
    "id": "ind",
    "ms": "msa",
    "ro": "ron",
    "uk": "ukr",
    "bg": "bul",
    "hr": "hrv",
    "sk": "slk",
    "sl": "slv",
    "sr": "srp",
    "ca": "cat",
    "eu": "eus",
    "gl": "glg",
    "lt": "lit",
    "lv": "lav",
    "et": "est",
    "is": "isl",
    "mt": "mlt",
    "cy": "cym",
    "ga": "gle",
}

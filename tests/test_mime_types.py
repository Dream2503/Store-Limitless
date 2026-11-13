from backend.server.mime_types import get_file_mime_type


def test_mkv_uses_legacy_compatible_mime_type() -> None:
    assert get_file_mime_type("video.mkv") == "video/x-matroska"
    assert get_file_mime_type("VIDEO.MKV") == "video/x-matroska"


def test_other_mime_types_still_use_standard_detection() -> None:
    assert get_file_mime_type("video.mp4") == "video/mp4"
    assert get_file_mime_type("document.pdf") == "application/pdf"


def test_unknown_extension_uses_fallback() -> None:
    assert get_file_mime_type("file.unknown") == "application/octet-stream"

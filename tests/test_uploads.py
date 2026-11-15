import pytest

from app.uploads import UploadError, matches_mime, parse_upload, validate_image

ALLOWED = ("image/jpeg", "image/png", "image/webp", "image/heic", "image/heif")
CAP = 1024
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 16
PNG = (
    b"\x89PNG\r\n\x1a\n"
    + b"\x00" * 16
)
WEBP = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 8
HEIC = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 8


def test_accepts_matching_magic_and_rejects_mismatch_empty_and_oversize():
    assert validate_image(JPEG, "image/jpeg", ALLOWED, CAP) == "image/jpeg"
    assert validate_image(PNG, "image/png; charset=binary", ALLOWED, CAP) == "image/png"
    assert validate_image(WEBP, "image/webp", ALLOWED, CAP) == "image/webp"
    assert validate_image(HEIC, "image/heic", ALLOWED, CAP) == "image/heic"
    with pytest.raises(UploadError, match="not valid image/jpeg"):
        validate_image(PNG, "image/jpeg", ALLOWED, CAP)
    with pytest.raises(UploadError, match="empty"):
        validate_image(b"", "image/jpeg", ALLOWED, CAP)
    with pytest.raises(UploadError, match="not accepted"):
        validate_image(JPEG, "image/gif", ALLOWED, CAP)
    with pytest.raises(UploadError, match="cap") as exc:
        validate_image(JPEG + b"\x00" * CAP, "image/jpeg", ALLOWED, CAP)
    assert exc.value.status_code == 413


def test_heif_brand_does_not_count_as_heic():
    heif = b"\x00\x00\x00\x18ftypheif" + b"\x00" * 8
    assert matches_mime(heif, "image/heif")
    assert not matches_mime(heif, "image/heic")


def test_multipart_parse_keeps_bytes_and_fields():
    body, content_type = _multipart(
        {
            "mode": "live",
            "fallback": "1",
        },
        JPEG,
        "image/jpeg",
    )
    data, mime, mode, fallback = parse_upload(content_type, body, CAP)
    assert data == JPEG
    assert mime == "image/jpeg"
    assert mode == "live"
    assert fallback is True


def _multipart(fields: dict[str, str], file_bytes: bytes, mime: str) -> tuple[bytes, str]:
    boundary = "----testboundary"
    chunks = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode()
        )
    chunks.append(
        (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"photo\"; filename=\"rack.jpg\"\r\n"
            f"Content-Type: {mime}\r\n\r\n"
        ).encode()
        + file_bytes
        + b"\r\n"
    )
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"

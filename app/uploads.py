"""Validate an in-memory photo. Nothing here writes the upload to disk."""

from __future__ import annotations

from python_multipart.exceptions import FormParserError
from python_multipart.multipart import create_form_parser

class UploadError(Exception):
    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


HEIC_BRANDS = {b"heic", b"heix", b"hevc", b"hevx"}
HEIF_BRANDS = {b"heif", b"mif1", b"msf1"}


def normalize_mime(value: str | None) -> str:
    if not value:
        return ""
    return value.split(";", 1)[0].strip().lower()


def matches_mime(data: bytes, mime: str) -> bool:
    if mime == "image/jpeg":
        return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"
    if mime == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    if mime == "image/heic":
        return _ftyp(data, HEIC_BRANDS)
    if mime == "image/heif":
        return _ftyp(data, HEIF_BRANDS)
    return False


def validate_image(data: bytes, mime: str, allowed: tuple[str, ...], max_bytes: int) -> str:
    declared = normalize_mime(mime)
    if not data:
        raise UploadError("The upload is empty.")
    if len(data) > max_bytes:
        raise UploadError(
            f"The photo is {len(data)} bytes, over the {max_bytes} byte cap.",
            status_code=413,
        )
    if declared not in allowed:
        allowed_list = ", ".join(allowed)
        raise UploadError(f"MIME type {declared or 'missing'} is not accepted. Use one of: {allowed_list}.")
    if not matches_mime(data, declared):
        raise UploadError(f"The file bytes are not valid {declared}.")
    return declared


def parse_upload(content_type: str, body: bytes, max_file_bytes: int) -> tuple[bytes, str, str, bool]:
    """Return image bytes, declared mime, mode, and whether fallback was requested.

    The parser is configured so the photo stays in a BytesIO. If a part ever
    rolls over to a temp file, this raises instead of continuing.
    """
    if not content_type or "multipart/form-data" not in content_type.lower():
        raise UploadError("Upload must be multipart/form-data.")
    # Room for the multipart framing around a photo that is itself at the cap.
    body_cap = max_file_bytes + 65_536
    if len(body) > body_cap:
        raise UploadError(f"The request body exceeds the {body_cap} byte cap.", status_code=413)

    fields: dict[str, str] = {}
    files: list[tuple[str, bytes, str]] = []

    def on_field(field) -> None:
        name = (field.field_name or b"").decode("utf-8", "replace")
        raw = field.value or b""
        fields[name] = raw.decode("utf-8", "replace")

    def on_file(file) -> None:
        name = (file.field_name or b"").decode("utf-8", "replace")
        if not file.in_memory:
            file.close()
            raise UploadError("Refusing the upload because the parser tried to write it to disk.")
        handle = file.file_object
        handle.seek(0)
        data = handle.read()
        file.close()
        files.append((name, data, file.content_type or ""))

    parser = create_form_parser(
        {"Content-Type": content_type},
        on_field,
        on_file,
        config={
            "MAX_MEMORY_FILE_SIZE": body_cap,
            "MAX_BODY_SIZE": body_cap,
            "MAX_FIELDS": 8,
            "MAX_FIELD_SIZE": 4096,
        },
    )
    try:
        parser.write(body)
        parser.finalize()
    except UploadError:
        raise
    except FormParserError as exc:
        raise UploadError(f"Could not read the upload: {exc}") from exc

    photos = [item for item in files if item[0] == "photo"]
    if len(photos) != 1:
        raise UploadError("Choose one photo in the photo field.")
    _, data, mime = photos[0]
    mode = fields.get("mode", "replay").strip().lower() or "replay"
    if mode not in {"replay", "live"}:
        raise UploadError("mode must be replay or live.")
    fallback = fields.get("fallback", "").strip().lower() in {"1", "true", "yes", "on"}
    return data, mime, mode, fallback


def _ftyp(data: bytes, brands: set[bytes]) -> bool:
    return len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in brands

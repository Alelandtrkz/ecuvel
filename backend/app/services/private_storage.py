from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename


class PrivateStorageError(Exception):
    """No fue posible validar o guardar el archivo privado."""


class InvalidPaymentProofFileError(PrivateStorageError):
    """El archivo no cumple el formato permitido."""


class PaymentProofFileTooLargeError(PrivateStorageError):
    """El archivo supera el límite permitido."""


class InvalidPrivateFileError(PrivateStorageError):
    """El archivo privado no cumple el formato permitido."""


class PrivateFileTooLargeError(PrivateStorageError):
    """El archivo privado supera el límite permitido."""


@dataclass(frozen=True, slots=True)
class StagedPrivateFile:
    temporary_path: Path
    storage_key: str
    original_filename: str
    media_type: str
    size_bytes: int
    sha256: str
    width: int | None = None
    height: int | None = None


_FORMATS = {
    "jpg": ("image/jpeg", "jpg"),
    "jpeg": ("image/jpeg", "jpg"),
    "png": ("image/png", "png"),
    "pdf": ("application/pdf", "pdf"),
    "webp": ("image/webp", "webp"),
}


def _detected_media_type(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header.startswith(b"%PDF-"):
        return "application/pdf"
    if len(header) >= 12 and header.startswith(b"RIFF") and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def private_file_path(root: str | Path, storage_key: str) -> Path:
    root_path = Path(root).resolve()
    candidate = (root_path / storage_key).resolve()
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise PrivateStorageError("La clave privada no es válida.") from exc
    return candidate


def stage_payment_proof(
    uploaded_file: FileStorage,
    *,
    root: str | Path,
    max_bytes: int,
) -> StagedPrivateFile:
    filename = secure_filename(uploaded_file.filename or "")[:255]
    if not filename or "." not in filename:
        raise InvalidPaymentProofFileError(
            "Selecciona un archivo JPEG, PNG o PDF válido."
        )
    extension = filename.rsplit(".", 1)[1].lower()
    expected = _FORMATS.get(extension)
    if expected is None:
        raise InvalidPaymentProofFileError(
            "Solo se aceptan archivos JPEG, PNG y PDF."
        )
    declared_type = (uploaded_file.mimetype or "").lower()
    expected_type, final_extension = expected
    if declared_type != expected_type:
        raise InvalidPaymentProofFileError(
            "La extensión y el tipo del archivo no coinciden."
        )

    root_path = Path(root).resolve()
    staging_dir = root_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(staging_dir, 0o700)
    except OSError:
        pass
    temporary_path = staging_dir / f"{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    header = b""
    descriptor = os.open(
        temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
    )
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = uploaded_file.stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PaymentProofFileTooLargeError(
                        "El comprobante no puede superar 10 MiB."
                    )
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0 or _detected_media_type(header) != expected_type:
            raise InvalidPaymentProofFileError(
                "El contenido del archivo no coincide con un JPEG, PNG o PDF válido."
            )
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    now = datetime.now(timezone.utc)
    storage_key = (
        f"{now:%Y/%m}/{uuid.uuid4().hex}.{final_extension}"
    )
    return StagedPrivateFile(
        temporary_path=temporary_path,
        storage_key=storage_key,
        original_filename=filename,
        media_type=expected_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
    )


def stage_private_upload(
    uploaded_file: FileStorage,
    *,
    root: str | Path,
    max_bytes: int,
    allowed_extensions: set[str],
    storage_prefix: str,
    require_image_decode: bool = False,
) -> StagedPrivateFile:
    filename = secure_filename(uploaded_file.filename or "")[:255]
    if not filename or "." not in filename:
        raise InvalidPrivateFileError("Selecciona un archivo válido.")
    extension = filename.rsplit(".", 1)[1].lower()
    if extension not in allowed_extensions:
        raise InvalidPrivateFileError("El tipo de archivo no está permitido.")
    expected = _FORMATS.get(extension)
    if expected is None:
        raise InvalidPrivateFileError("El tipo de archivo no está permitido.")
    declared_type = (uploaded_file.mimetype or "").lower()
    expected_type, final_extension = expected
    if declared_type != expected_type:
        raise InvalidPrivateFileError("La extensión y el tipo del archivo no coinciden.")

    root_path = Path(root).resolve()
    staging_dir = root_path / ".staging"
    staging_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(staging_dir, 0o700)
    except OSError:
        pass
    temporary_path = staging_dir / f"{uuid.uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0
    header = b""
    descriptor = os.open(temporary_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = uploaded_file.stream.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise PrivateFileTooLargeError("El archivo supera el tamaño máximo permitido.")
                if len(header) < 16:
                    header += chunk[: 16 - len(header)]
                destination.write(chunk)
                digest.update(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if size == 0 or _detected_media_type(header) != expected_type:
            raise InvalidPrivateFileError("El contenido del archivo no coincide con el tipo indicado.")
        width = None
        height = None
        if require_image_decode:
            try:
                with Image.open(temporary_path) as image:
                    image.verify()
                with Image.open(temporary_path) as image:
                    width, height = image.size
            except (SyntaxError, UnidentifiedImageError, OSError) as exc:
                raise InvalidPrivateFileError("La imagen no se puede procesar.") from exc
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise

    now = datetime.now(timezone.utc)
    clean_prefix = storage_prefix.strip("/").replace("..", "")
    storage_key = f"{clean_prefix}/{now:%Y/%m}/{uuid.uuid4().hex}.{final_extension}"
    return StagedPrivateFile(
        temporary_path=temporary_path,
        storage_key=storage_key,
        original_filename=filename,
        media_type=expected_type,
        size_bytes=size,
        sha256=digest.hexdigest(),
        width=width,
        height=height,
    )


def promote_private_file(
    staged: StagedPrivateFile, *, root: str | Path
) -> Path:
    destination = private_file_path(root, staged.storage_key)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if destination.exists():
        raise PrivateStorageError("La clave privada ya está ocupada.")
    os.replace(staged.temporary_path, destination)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    return destination


def delete_private_file(path: str | Path | None) -> None:
    if path is not None:
        Path(path).unlink(missing_ok=True)


def verify_private_file(
    *, root: str | Path, storage_key: str, size_bytes: int, sha256: str
) -> Path:
    path = private_file_path(root, storage_key)
    if not path.is_file() or path.stat().st_size != size_bytes:
        raise PrivateStorageError("El archivo privado no existe o está incompleto.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(64 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha256:
        raise PrivateStorageError("El comprobante no supera la verificación de integridad.")
    return path

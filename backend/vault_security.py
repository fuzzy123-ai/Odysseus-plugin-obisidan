import base64
import hashlib
import hmac
import json
import os
import posixpath
import time
import zipfile
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


STATE_FILENAME = ".odysseus-vault.json"
EXPORT_MANIFEST = "odysseus-vault.json"
EXPORT_PAYLOAD = "vault.bin"
EXPORT_VERSION = 1
MAX_ARCHIVE_BYTES = 50 * 1024 * 1024
MAX_IMPORT_FILES = 5000
MAX_IMPORT_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
PBKDF2_ITERATIONS = 390000


class VaultSecurityError(ValueError):
    pass


@dataclass
class VaultArchive:
    data: bytes
    encrypted: bool
    file_count: int
    filename: str


def _state_path(vault_dir: str) -> str:
    return os.path.join(vault_dir, STATE_FILENAME)


def _load_state(vault_dir: str) -> dict:
    try:
        with open(_state_path(vault_dir), "r", encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        raise VaultSecurityError("Vault protection metadata is unreadable") from exc


def _save_state(vault_dir: str, state: dict) -> None:
    os.makedirs(vault_dir, exist_ok=True)
    path = _state_path(vault_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _derive_key(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> bytes:
    if not password:
        raise VaultSecurityError("Password is required")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations, dklen=32)


def _password_hash(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    return base64.b64encode(_derive_key(password, salt, iterations)).decode("ascii")


def _verify_password(state: dict, password: str) -> bool:
    if not state.get("protected"):
        return True
    try:
        salt = base64.b64decode(state["salt"])
        expected = state["password_hash"]
        iterations = int(state.get("iterations") or PBKDF2_ITERATIONS)
        actual = _password_hash(password, salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def protection_status(vault_dir: str) -> dict:
    state = _load_state(vault_dir)
    return {
        "protected": bool(state.get("protected")),
        "locked": bool(state.get("protected") and state.get("locked")),
        "version": state.get("version"),
    }


def require_unlocked(vault_dir: str) -> None:
    status = protection_status(vault_dir)
    if status["locked"]:
        raise VaultSecurityError("Vault is locked")


def set_password(vault_dir: str, password: str) -> dict:
    if not password or len(password) < 8:
        raise VaultSecurityError("Password must be at least 8 characters long")
    salt = os.urandom(16)
    state = {
        "version": 1,
        "protected": True,
        "locked": False,
        "salt": base64.b64encode(salt).decode("ascii"),
        "iterations": PBKDF2_ITERATIONS,
        "password_hash": _password_hash(password, salt),
        "updated_at": int(time.time()),
    }
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def lock_vault(vault_dir: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        raise VaultSecurityError("Vault has no password protection enabled")
    state["locked"] = True
    state["updated_at"] = int(time.time())
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def unlock_vault(vault_dir: str, password: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        return protection_status(vault_dir)
    if not _verify_password(state, password):
        raise VaultSecurityError("Invalid password")
    state["locked"] = False
    state["updated_at"] = int(time.time())
    _save_state(vault_dir, state)
    return protection_status(vault_dir)


def remove_password(vault_dir: str, password: str) -> dict:
    state = _load_state(vault_dir)
    if not state.get("protected"):
        return protection_status(vault_dir)
    if not _verify_password(state, password):
        raise VaultSecurityError("Invalid password")
    try:
        os.remove(_state_path(vault_dir))
    except FileNotFoundError:
        pass
    return protection_status(vault_dir)


def validate_archive_member(name: str) -> str:
    cleaned = (name or "").replace("\\", "/").strip()
    if not cleaned or cleaned.endswith("/"):
        raise VaultSecurityError("Archive entry is not a file")
    drive_like = len(cleaned) >= 2 and cleaned[1] == ":"
    if cleaned.startswith("/") or cleaned.startswith("//") or drive_like:
        raise VaultSecurityError("Archive contains an absolute path")
    normalized = posixpath.normpath(cleaned)
    if normalized == "." or normalized.startswith("../") or normalized == "..":
        raise VaultSecurityError("Archive contains a path traversal entry")
    parts = normalized.split("/")
    if STATE_FILENAME in parts:
        raise VaultSecurityError("Archive may not contain vault protection metadata")
    return normalized


def _iter_vault_files(vault_dir: str, root: str = ""):
    base = os.path.abspath(vault_dir)
    start = os.path.abspath(os.path.join(base, root.strip("/\\")))
    if os.path.commonpath([base, start]) != base:
        raise VaultSecurityError("Export path is outside the vault")
    if not os.path.exists(start):
        raise VaultSecurityError("Export path does not exist")
    if os.path.isfile(start):
        rel = os.path.relpath(start, base).replace("\\", "/")
        if os.path.basename(rel) != STATE_FILENAME:
            yield rel, start
        return
    for dirpath, dirs, files in os.walk(start):
        dirs[:] = [d for d in dirs if d not in {".obsidian", "__pycache__"}]
        for filename in files:
            if filename == STATE_FILENAME:
                continue
            abs_path = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(abs_path, base).replace("\\", "/")
            yield rel_path, abs_path


def _build_plain_zip(vault_dir: str, root: str = "") -> tuple[bytes, int]:
    buffer = BytesIO()
    count = 0
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format": "odysseus-obsidian-vault",
            "version": EXPORT_VERSION,
            "encrypted": False,
            "created_at": int(time.time()),
        }
        zf.writestr(EXPORT_MANIFEST, json.dumps(manifest, sort_keys=True))
        for rel_path, abs_path in _iter_vault_files(vault_dir, root):
            zf.write(abs_path, rel_path)
            count += 1
    return buffer.getvalue(), count


def export_vault(vault_dir: str, password: Optional[str] = None, root: str = "") -> VaultArchive:
    require_unlocked(vault_dir)
    plain_zip, count = _build_plain_zip(vault_dir, root)
    if not password:
        return VaultArchive(
            data=plain_zip,
            encrypted=False,
            file_count=count,
            filename="obsidian-vault.zip",
        )

    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = _derive_key(password, salt)
    encrypted = AESGCM(key).encrypt(nonce, plain_zip, None)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        manifest = {
            "format": "odysseus-obsidian-vault",
            "version": EXPORT_VERSION,
            "encrypted": True,
            "kdf": "pbkdf2-sha256",
            "iterations": PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "created_at": int(time.time()),
        }
        zf.writestr(EXPORT_MANIFEST, json.dumps(manifest, sort_keys=True))
        zf.writestr(EXPORT_PAYLOAD, encrypted)
    return VaultArchive(
        data=buffer.getvalue(),
        encrypted=True,
        file_count=count,
        filename="obsidian-vault.encrypted.zip",
    )


def _read_zip_bytes(archive_data: bytes, password: Optional[str]) -> bytes:
    if len(archive_data) > MAX_ARCHIVE_BYTES:
        raise VaultSecurityError("Archive is too large")
    try:
        with zipfile.ZipFile(BytesIO(archive_data), "r") as zf:
            names = set(zf.namelist())
            if EXPORT_MANIFEST in names and EXPORT_PAYLOAD in names:
                manifest = json.loads(zf.read(EXPORT_MANIFEST).decode("utf-8"))
                if manifest.get("encrypted"):
                    if not password:
                        raise VaultSecurityError("Password is required")
                    try:
                        salt = base64.b64decode(manifest["salt"])
                        nonce = base64.b64decode(manifest["nonce"])
                        iterations = int(manifest.get("iterations") or PBKDF2_ITERATIONS)
                        key = _derive_key(password, salt, iterations)
                        return AESGCM(key).decrypt(nonce, zf.read(EXPORT_PAYLOAD), None)
                    except (InvalidTag, KeyError, ValueError) as exc:
                        raise VaultSecurityError("Invalid password or corrupted archive") from exc
            return archive_data
    except zipfile.BadZipFile as exc:
        raise VaultSecurityError("Archive is not a valid ZIP file") from exc


def import_vault(vault_dir: str, archive_data: bytes, password: Optional[str] = None) -> dict:
    require_unlocked(vault_dir)
    plain_archive = _read_zip_bytes(archive_data, password)
    imported = 0
    total_size = 0
    planned: list[tuple[str, zipfile.ZipInfo]] = []
    try:
        with zipfile.ZipFile(BytesIO(plain_archive), "r") as zf:
            for info in zf.infolist():
                if info.is_dir() or info.filename == EXPORT_MANIFEST:
                    continue
                rel_path = validate_archive_member(info.filename)
                if info.file_size < 0:
                    raise VaultSecurityError("Archive contains an invalid file")
                total_size += info.file_size
                if len(planned) >= MAX_IMPORT_FILES:
                    raise VaultSecurityError("Archive contains too many files")
                if total_size > MAX_IMPORT_UNCOMPRESSED_BYTES:
                    raise VaultSecurityError("Archive expands beyond the size limit")
                target = os.path.abspath(os.path.join(vault_dir, rel_path))
                if os.path.commonpath([os.path.abspath(vault_dir), target]) != os.path.abspath(vault_dir):
                    raise VaultSecurityError("Archive entry escapes the vault")
                if os.path.exists(target):
                    raise VaultSecurityError(f"Import conflict: {rel_path}")
                planned.append((rel_path, info))

            os.makedirs(vault_dir, exist_ok=True)
            pwd = password.encode("utf-8") if password else None
            for rel_path, info in planned:
                target = os.path.abspath(os.path.join(vault_dir, rel_path))
                os.makedirs(os.path.dirname(target), exist_ok=True)
                try:
                    with zf.open(info, "r", pwd=pwd) as src, open(target, "wb") as dst:
                        dst.write(src.read())
                except RuntimeError as exc:
                    raise VaultSecurityError("Invalid password or encrypted archive unsupported") from exc
                imported += 1
    except zipfile.BadZipFile as exc:
        raise VaultSecurityError("Archive is not a valid ZIP file") from exc
    return {"imported_files": imported, "bytes": total_size}

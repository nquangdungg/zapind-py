from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import json
import os
import secrets
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .errors import SessionError
from .models import SessionCredentials, StoredSessionRecord


class SessionStore(ABC):
    @abstractmethod
    async def load(self) -> StoredSessionRecord | None: ...

    @abstractmethod
    async def save(self, record: StoredSessionRecord) -> None: ...

    @abstractmethod
    async def clear(self) -> None: ...


def _record_to_dict(record: StoredSessionRecord) -> dict[str, Any]:
    return {
        "version": record.version,
        "savedAt": record.saved_at,
        "credentials": {
            "cookie": record.credentials.cookie,
            "imei": record.credentials.imei,
            "userAgent": record.credentials.user_agent,
            "language": record.credentials.language,
        },
        "account": record.account,
    }


def _dict_to_record(data: dict[str, Any]) -> StoredSessionRecord | None:
    creds = data.get("credentials")
    if not isinstance(creds, dict):
        return None
    return StoredSessionRecord(
        version=int(data.get("version") or 1),
        saved_at=int(data.get("savedAt") or 0),
        credentials=SessionCredentials(
            cookie=list(creds.get("cookie") or []),
            imei=str(creds.get("imei") or ""),
            user_agent=str(creds.get("userAgent") or ""),
            language=str(creds.get("language") or "vi"),
        ),
        account=data.get("account") if isinstance(data.get("account"), dict) else None,
    )


class MemorySessionStore(SessionStore):
    def __init__(self) -> None:
        self._record: StoredSessionRecord | None = None

    async def load(self) -> StoredSessionRecord | None:
        return copy.deepcopy(self._record) if self._record is not None else None

    async def save(self, record: StoredSessionRecord) -> None:
        self._record = copy.deepcopy(record)

    async def clear(self) -> None:
        self._record = None


class FileSessionStore(SessionStore):
    def __init__(self, file_path: str | Path) -> None:
        path_str = str(file_path).strip()
        if not path_str:
            raise SessionError("FileSessionStore requires a non-empty file_path")
        self.file_path = Path(path_str)

    def _load_sync(self) -> StoredSessionRecord | None:
        if not self.file_path.exists():
            return None
        try:
            raw = self.file_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                return None
            return _dict_to_record(parsed)
        except Exception as err:
            raise SessionError(f'Failed to read session file "{self.file_path}"', cause=err) from err

    async def load(self) -> StoredSessionRecord | None:
        return await asyncio.to_thread(self._load_sync)

    def _save_sync(self, record: StoredSessionRecord) -> None:
        tmp = self.file_path.with_name(
            f"{self.file_path.name}.tmp.{int(time.time() * 1000)}.{secrets.token_hex(4)}"
        )
        try:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(_record_to_dict(record), indent=2), encoding="utf-8")
            os.replace(tmp, self.file_path)
            if os.name != "nt":
                try:
                    os.chmod(self.file_path, 0o600)
                except OSError:
                    pass
        except Exception as err:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SessionError(f'Failed to save session file "{self.file_path}"', cause=err) from err

    async def save(self, record: StoredSessionRecord) -> None:
        await asyncio.to_thread(self._save_sync, record)

    def _clear_sync(self) -> None:
        try:
            self.file_path.unlink(missing_ok=True)
        except Exception as err:
            raise SessionError(f'Failed to remove session file "{self.file_path}"', cause=err) from err

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)


class EncryptedFileSessionStore(SessionStore):
    """AES-256-GCM encrypted session file store (requires `pip install "zapind[crypto]"`)."""

    def __init__(self, file_path: str | Path, secret: str | bytes) -> None:
        path_str = str(file_path).strip()
        if not path_str:
            raise SessionError("EncryptedFileSessionStore requires a non-empty file_path")
        secret_bytes = secret.encode("utf-8") if isinstance(secret, str) else secret
        if len(secret_bytes) < 16:
            raise SessionError("Encryption secret must be at least 16 bytes")
        self.file_path = Path(path_str)
        self._secret = secret_bytes

    @staticmethod
    def _import_aesgcm() -> Any:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        except ImportError as err:
            raise SessionError(
                'EncryptedFileSessionStore requires the "cryptography" package. '
                'Install with: pip install "zapind[crypto]"',
                cause=err,
            ) from err
        return AESGCM

    def _derive_key(self, salt: bytes) -> bytes:
        return hashlib.scrypt(self._secret, salt=salt, n=16384, r=8, p=1, dklen=32)

    def _load_sync(self) -> StoredSessionRecord | None:
        if not self.file_path.exists():
            return None
        AESGCM = self._import_aesgcm()
        try:
            envelope = json.loads(self.file_path.read_text(encoding="utf-8"))
            if envelope.get("v") != 1 or envelope.get("alg") != "aes-256-gcm":
                raise SessionError("Unsupported encrypted session format")
            salt = base64.b64decode(envelope["salt"])
            iv = base64.b64decode(envelope["iv"])
            tag = base64.b64decode(envelope["tag"])
            ciphertext = base64.b64decode(envelope["data"])
            key = self._derive_key(salt)
            plaintext = AESGCM(key).decrypt(iv, ciphertext + tag, None)
            parsed = json.loads(plaintext.decode("utf-8"))
            return _dict_to_record(parsed)
        except SessionError:
            raise
        except Exception as err:
            raise SessionError(
                f'Failed to decrypt session file "{self.file_path}"', cause=err
            ) from err

    async def load(self) -> StoredSessionRecord | None:
        return await asyncio.to_thread(self._load_sync)

    def _save_sync(self, record: StoredSessionRecord) -> None:
        AESGCM = self._import_aesgcm()
        tmp = self.file_path.with_name(
            f"{self.file_path.name}.tmp.{int(time.time() * 1000)}.{secrets.token_hex(4)}"
        )
        try:
            salt = os.urandom(16)
            iv = os.urandom(12)
            key = self._derive_key(salt)
            plaintext = json.dumps(_record_to_dict(record)).encode("utf-8")
            combined = AESGCM(key).encrypt(iv, plaintext, None)
            ciphertext, tag = combined[:-16], combined[-16:]
            envelope = {
                "v": 1,
                "alg": "aes-256-gcm",
                "salt": base64.b64encode(salt).decode("ascii"),
                "iv": base64.b64encode(iv).decode("ascii"),
                "tag": base64.b64encode(tag).decode("ascii"),
                "data": base64.b64encode(ciphertext).decode("ascii"),
            }
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(envelope), encoding="utf-8")
            os.replace(tmp, self.file_path)
            if os.name != "nt":
                try:
                    os.chmod(self.file_path, 0o600)
                except OSError:
                    pass
        except Exception as err:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
            raise SessionError(
                f'Failed to save encrypted session "{self.file_path}"', cause=err
            ) from err

    async def save(self, record: StoredSessionRecord) -> None:
        await asyncio.to_thread(self._save_sync, record)

    def _clear_sync(self) -> None:
        try:
            self.file_path.unlink(missing_ok=True)
        except Exception as err:
            raise SessionError(
                f'Failed to remove encrypted session "{self.file_path}"', cause=err
            ) from err

    async def clear(self) -> None:
        await asyncio.to_thread(self._clear_sync)

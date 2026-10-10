"""Strict SQLCipher v4 raw-key reader for the observed KakaoTalk format.

Adapted from https://github.com/is-theo/kakao-cli-win (MIT),
Copyright (c) 2026 theo; see the distribution's THIRD_PARTY_NOTICES.md.
The page-1 HMAC excludes its 16-byte salt, as in SQLCipher's codec.
Only 4096-byte pages, 80 reserved bytes and SHA-512 HMAC are supported.
"""
from __future__ import annotations

import hashlib
import hmac
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

PAGE_SIZE = 4096
RESERVE = 80
SQLITE_MAGIC = b"SQLite format 3\x00"
ZERO_PAGE_POLICY = "reject_unauthenticated_zero_pages"


class CryptoError(ValueError):
    """An encrypted page failed authentication or the format is unsupported."""


def _hmac_key(key: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", key, bytes(b ^ 0x3A for b in salt), 2, dklen=32)


def _verify_with_hmac_key(page: bytes, hmac_key: bytes, pgno: int) -> bool:
    if len(page) != PAGE_SIZE or not 1 <= pgno <= 0xFFFFFFFF:
        return False
    start = 16 if pgno == 1 else 0
    region = page[start:PAGE_SIZE - 64] + struct.pack("<I", pgno)
    expected = hmac.new(hmac_key, region, hashlib.sha512).digest()
    return hmac.compare_digest(expected, page[-64:])


def verify_page(page: bytes, key: bytes, pgno: int, salt: bytes) -> bool:
    """Authenticate ciphertext + IV + little-endian page number; never log keys."""
    if len(key) != 32 or len(salt) != 16:
        return False
    return _verify_with_hmac_key(page, _hmac_key(key, salt), pgno)


def _decrypt_page(page: bytes, key: bytes, pgno: int) -> bytes:
    usable = PAGE_SIZE - RESERVE
    start = 16 if pgno == 1 else 0
    decryptor = Cipher(algorithms.AES(key), modes.CBC(page[usable:usable + 16])).decryptor()
    payload = decryptor.update(page[start:usable]) + decryptor.finalize()
    return (SQLITE_MAGIC if pgno == 1 else b"") + payload + bytes(RESERVE)


def _validate_header(page: bytes) -> None:
    if page[:16] != SQLITE_MAGIC:
        raise CryptoError("invalid_sqlite_magic")
    if int.from_bytes(page[16:18], "big") != PAGE_SIZE or page[20] != RESERVE:
        raise CryptoError("unsupported_sqlcipher_page_layout")
    if page[18] not in (1, 2) or page[19] not in (1, 2):
        raise CryptoError("unsupported_sqlite_journal_format")
    if page[21:24] != bytes((64, 32, 32)):
        raise CryptoError("invalid_sqlite_payload_fractions")


def verify_first_page(page: bytes, key: bytes) -> bool:
    if len(page) != PAGE_SIZE or not verify_page(page, key, 1, page[:16]):
        return False
    try:
        _validate_header(_decrypt_page(page, key, 1))
    except (CryptoError, ValueError):
        return False
    return True


def decrypt_database(data: bytes, key: bytes) -> bytes:
    """Authenticate every page before returning plaintext; zero pages fail too.

    No file I/O is performed. The returned database retains reserved-byte count
    80; its reserved regions are cleared. Journal header bytes are preserved.
    HMAC authenticates the available pages, not the completeness of chat history.
    """
    if len(key) != 32:
        raise CryptoError("raw_key_must_be_32_bytes")
    if len(data) < PAGE_SIZE or len(data) % PAGE_SIZE:
        raise CryptoError("invalid_database_size")
    hmac_key = _hmac_key(key, data[:16])
    result = bytearray()
    for offset in range(0, len(data), PAGE_SIZE):
        pgno = offset // PAGE_SIZE + 1
        page = data[offset:offset + PAGE_SIZE]
        if not _verify_with_hmac_key(page, hmac_key, pgno):
            kind = "unauthenticated_zero_page" if page == bytes(PAGE_SIZE) else "page_hmac_failed"
            raise CryptoError(f"{kind}:{pgno}")
        result.extend(_decrypt_page(page, key, pgno))
    _validate_header(result[:PAGE_SIZE])
    return bytes(result)

#!/usr/bin/env python3
"""Create an Android-10-sideloadable AIM Lag build for Free Fire MAX.

The original APK hard-codes ``com.dts.freefireth`` (standard Free Fire) in
its Android VPN bridge. A phone with only Free Fire MAX therefore reports
that the target is absent and never reaches ``VpnService.prepare()``.

A direct string replacement makes the DEX string-ID table non-canonical and
causes Android 10's DEX verifier to kill the process. Instead, this patch
makes the target-installed check succeed and removes the stale single-package
allow-list call. The service then starts for Free Fire MAX and Android can
show its normal VPN permission prompt. It recalculates DEX integrity fields
and creates a fresh APK v1 signature suitable for Android 10 sideloading.

Usage:
    python3 tools/patch_freefiremax_apk.py original.apk aimlag-freefiremax.apk

Requires the system ``openssl`` command.  The resulting APK is signed with a
new local certificate, so Android will require the previous build with the
same package ID to be uninstalled before this APK is installed.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zlib
import zipfile

SOURCE_PACKAGE = b"com.dts.freefireth"

# Exact six-byte Dalvik invoke-virtual instructions in the supplied APK's
# classes.dex. Each is replaced with three NOP instructions, retaining every
# code offset, register count, branch target and DEX string-ID table entry.
TARGET_INSTALLED_CALL = bytes.fromhex("6e307b050d02")
ALLOWED_APPLICATION_CALL = bytes.fromhex("6e20f9052d00")
NOP_INSTRUCTIONS = b"\x00" * 6

SIGNATURE_FILES = {
    "META-INF/MANIFEST.MF",
    "META-INF/ANDROID.SF",
    "META-INF/ANDROID.RSA",
}


def read_uleb128(data: bytearray, offset: int) -> tuple[int, int]:
    """Read a DEX unsigned LEB128 value and return (value, next_offset)."""
    value = 0
    shift = 0
    while True:
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift > 35:
            raise ValueError("Invalid DEX ULEB128 value")


def dex_string_ids(data: bytearray) -> list[tuple[int, int, bytes]]:
    """Return (string-id-index, item-offset, UTF-8-bytes) for each DEX string."""
    if data[:3] != b"dex":
        raise ValueError("classes.dex does not have a DEX header")
    string_count, string_ids_offset = struct.unpack_from("<II", data, 56)
    strings: list[tuple[int, int, bytes]] = []
    for index in range(string_count):
        item_offset = struct.unpack_from("<I", data, string_ids_offset + index * 4)[0]
        _utf16_length, text_offset = read_uleb128(data, item_offset)
        terminator = data.index(0, text_offset)
        strings.append((index, item_offset, bytes(data[text_offset:terminator])))
    return strings


def patch_dex_for_freefire_max(dex: bytes) -> bytes:
    """Allow the supplied APK to start its VPN when only Free Fire MAX exists.

    The original target-app probe throws ``NameNotFoundException`` for MAX,
    which makes the Flutter UI decline to start. The original service also
    throws while adding the absent standard-Free-Fire package to its VPN
    allow-list. Replacing those two calls with NOPs keeps DEX layout stable:
    the pre-initialised target result remains ``true`` and the service starts
    without a stale package-specific allow-list.
    """
    data = bytearray(dex)
    source_ids = [index for index, _offset, text in dex_string_ids(data) if text == SOURCE_PACKAGE]
    if len(source_ids) != 1:
        raise ValueError(
            f"Expected one {SOURCE_PACKAGE.decode()} DEX string, found {len(source_ids)}"
        )

    for label, instruction in (
        ("target-installed package probe", TARGET_INSTALLED_CALL),
        ("stale VPN allowed-application call", ALLOWED_APPLICATION_CALL),
    ):
        occurrences = data.count(instruction)
        if occurrences != 1:
            raise ValueError(f"Expected one {label}, found {occurrences}")
        offset = data.index(instruction)
        data[offset : offset + len(instruction)] = NOP_INSTRUCTIONS

    # DEX SHA-1 covers bytes from offset 32. Adler-32 covers bytes from offset 12.
    data[12:32] = hashlib.sha1(data[32:]).digest()
    struct.pack_into("<I", data, 8, zlib.adler32(data[12:]) & 0xFFFFFFFF)
    return bytes(data)


def wrap_manifest_header(name: str, value: str) -> bytes:
    """Encode a JAR-manifest header with the 72-byte line limit."""
    raw = f"{name}: {value}".encode("utf-8")
    lines: list[bytes] = []
    first = True
    while raw:
        limit = 72 if first else 71
        lines.append((b"" if first else b" ") + raw[:limit])
        raw = raw[limit:]
        first = False
    return b"\r\n".join(lines) + b"\r\n"


def build_manifest(entries: dict[str, bytes]) -> tuple[bytes, dict[str, bytes]]:
    """Build a deterministic v1 JAR manifest and retain every entry section."""
    manifest = b"Manifest-Version: 1.0\r\nCreated-By: AIM Lag Android 10 patch\r\n\r\n"
    sections: dict[str, bytes] = {}
    for name in sorted(entries):
        digest = base64.b64encode(hashlib.sha256(entries[name]).digest()).decode("ascii")
        section = wrap_manifest_header("Name", name) + wrap_manifest_header("SHA-256-Digest", digest) + b"\r\n"
        manifest += section
        sections[name] = section
    return manifest, sections


def build_signature_file(manifest: bytes, sections: dict[str, bytes]) -> bytes:
    """Build the .SF file used by the APK/JAR v1 signature scheme."""
    manifest_digest = base64.b64encode(hashlib.sha256(manifest).digest()).decode("ascii")
    signature_file = (
        b"Signature-Version: 1.0\r\n"
        b"Created-By: AIM Lag Android 10 patch\r\n"
        + wrap_manifest_header("SHA-256-Digest-Manifest", manifest_digest)
        + b"\r\n"
    )
    for name in sorted(sections):
        digest = base64.b64encode(hashlib.sha256(sections[name]).digest()).decode("ascii")
        signature_file += (
            wrap_manifest_header("Name", name)
            + wrap_manifest_header("SHA-256-Digest", digest)
            + b"\r\n"
        )
    return signature_file


def create_signature_block(signature_file: bytes, workdir: Path) -> bytes:
    """Create a self-signed certificate and PKCS#7 signature block via OpenSSL."""
    key = workdir / "signing-key.pem"
    certificate = workdir / "signing-cert.pem"
    sf = workdir / "ANDROID.SF"
    rsa = workdir / "ANDROID.RSA"
    sf.write_bytes(signature_file)

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-sha256",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(certificate),
            "-days",
            "3650",
            "-subj",
            "/CN=AIM Lag Android 10 Free Fire MAX Fix/",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [
            "openssl",
            "cms",
            "-sign",
            "-binary",
            "-noattr",
            "-nosmimecap",
            "-in",
            str(sf),
            "-signer",
            str(certificate),
            "-inkey",
            str(key),
            "-outform",
            "DER",
            "-out",
            str(rsa),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return rsa.read_bytes()


def build_patched_apk(source: Path, destination: Path) -> None:
    """Patch classes.dex, replace APK signing metadata, and write destination."""
    with zipfile.ZipFile(source) as archive:
        files = {
            info.filename: archive.read(info.filename)
            for info in archive.infolist()
            if info.filename not in SIGNATURE_FILES
        }
        infos = {
            info.filename: info
            for info in archive.infolist()
            if info.filename not in SIGNATURE_FILES
        }

    if "classes.dex" not in files:
        raise ValueError("APK does not contain classes.dex")
    files["classes.dex"] = patch_dex_for_freefire_max(files["classes.dex"])

    manifest, sections = build_manifest(files)
    signature_file = build_signature_file(manifest, sections)
    with tempfile.TemporaryDirectory(prefix="aimlag-sign-") as temp:
        signature_block = create_signature_block(signature_file, Path(temp))

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_output = destination.with_suffix(destination.suffix + ".tmp")
    with zipfile.ZipFile(temporary_output, "w", allowZip64=False) as output:
        # Android/JAR tooling expects these signature files before the payload.
        for name, content in (
            ("META-INF/MANIFEST.MF", manifest),
            ("META-INF/ANDROID.SF", signature_file),
            ("META-INF/ANDROID.RSA", signature_block),
        ):
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            output.writestr(info, content)
        for name in files:
            info = infos[name]
            output.writestr(info, files[name])
    os.replace(temporary_output, destination)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="the original AIM Lag APK")
    parser.add_argument("destination", type=Path, help="where to write the patched APK")
    args = parser.parse_args()

    if shutil.which("openssl") is None:
        parser.error("openssl is required to create the APK signature")
    build_patched_apk(args.source, args.destination)
    print(f"Wrote {args.destination}")


if __name__ == "__main__":
    main()

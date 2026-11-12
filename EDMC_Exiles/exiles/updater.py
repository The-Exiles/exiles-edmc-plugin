"""Updates that put themselves in place (0.6.0).

The ops site's version file names the newest version, where its zip is, the zip's SHA-256 and its signature. A zip is
put in place only when it is signed with the squad's release key, holds nothing but plugin files under one EDMC_Exiles
folder, and its version.txt says the version the site named. The files are written over the running plugin: EDMC
already holds all of the plugin in memory (load.py loads it whole at start), so the new version starts the next time
EDMC starts. The spool of unsent journal lines and Python's caches are never touched. Any trouble leaves the plugin as
it was, and the Settings page keeps its Get the update button for an update by hand."""
from __future__ import annotations

import hashlib
import hmac
import io
import os
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterable, Optional

FOLDER = "EDMC_Exiles"
KINDS = (".py", ".txt", ".png")           # what the plugin is made of; a zip holding anything else is not ours
KEEP = ("spool", "__pycache__")            # folders of the running plugin an update leaves alone
MOST_BYTES = 5_000_000                     # a zip, or all its files unpacked, above this is not ours
MOST_FILES = 200
LAST = "version.txt"                       # written last: until it changes, the next look tries the whole update again

# The squad's release key: RSA with exponent 65537 and the modulus below, in groups of sixteen. Only a zip signed with
# its private half is put in place, and that half never leaves the PC the releases are made on.
_RELEASE_MODULUS = """
a4ba2a056e0d9b22 ff1aabb8761d6937 ccb771d7f6d16739 a2e9228783c7a0bd
1a700dec081267f7 63714768ab221f25 cfc4d1967645f7c1 2ea5efd765b83f90
1ec6cc2d32f18d9a 2c45ecd419963c66 d3d32fe97df5a761 fb044f3950413575
96a9ca4a40e42a9d 854bea6f7befb7e6 cd10e942fd2e65db d48b9d80db5dc236
21609c74e50f543a 8537866c183c1b51 6078d164eeda2fbc 3f59f3c74037b3e5
c20fd63f778a267c b28b39bfba2da46c 18affc87b7b26fe5 ed858ed4b0b47b43
1bfe328d0c84c481 d8d88efd81501023 1d13702567db4935 859220e56c25ea97
98568ebf02c7b17e f7e2206b3faca24b 6e2b61c4fbace7c8 a7fe1b95a2895562
2978f7ef4517e66b 9e9766b36ba83cfe 8bdc2d3313ac98fa 4fa0f7cda002865b
440895da4533144f db914412c5defdcb d46287482354f655 d966448c19f8683c
23d942fd2ac8f207 b23ac8ff0e473bb5 22f375e1c91bb378 d0355f63a4c30556
cdd8440f848f3529 f23a20cb26f7c284 26cdec55e48fd24f f314d5149d82f7d7
"""
RELEASE_KEYS = ((int("".join(_RELEASE_MODULUS.split()), 16), 65537),)

# what PKCS #1 v1.5 puts in front of a SHA-256 digest before signing it (the DigestInfo of RFC 8017), in two parts
_SHA256_INFO = bytes.fromhex("3031300d0609608648 01650304020105000420")


def signed(data: bytes, signature: bytes, keys: Optional[Iterable[tuple]] = None) -> bool:
    """Whether `signature` is a release key's signature of `data`: RSA PKCS #1 v1.5 with SHA-256, the kind
    `openssl dgst -sha256 -sign` makes. The whole padded block is built and compared, never parsed."""
    info = _SHA256_INFO + hashlib.sha256(data).digest()
    for n, e in (RELEASE_KEYS if keys is None else keys):
        size = (n.bit_length() + 7) // 8
        if len(signature) != size:
            continue
        s = int.from_bytes(signature, "big")
        if s >= n:
            continue
        block = pow(s, e, n).to_bytes(size, "big")
        if hmac.compare_digest(block, b"\x00\x01" + b"\xff" * (size - 3 - len(info)) + b"\x00" + info):
            return True
    return False


def unpack(data: bytes, version: str) -> dict:
    """{path inside the plugin folder, parts joined by /: bytes} from a release zip, which must hold the plugin at
    `version` and nothing else. A ValueError says what is wrong with it."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        infos = z.infolist()
    except (zipfile.BadZipFile, OSError, ValueError):
        raise ValueError("the download is not a zip")
    if len(infos) > MOST_FILES:
        raise ValueError("the zip holds too many files")
    files: dict = {}
    total = 0
    for info in infos:
        name = info.filename
        if name.endswith("/"):
            continue
        parts = PurePosixPath(name).parts
        if ("\\" in name or ":" in name or name.startswith("/") or len(parts) < 2 or parts[0] != FOLDER
                or any(p in ("", ".", "..") for p in parts)):
            raise ValueError(f"the zip holds a file outside the plugin folder ({name})")
        if parts[1] in KEEP or PurePosixPath(name).suffix not in KINDS:
            raise ValueError(f"the zip holds a file that is not part of the plugin ({name})")
        total += info.file_size
        if total > MOST_BYTES:
            raise ValueError("the zip is too big")
        try:
            files["/".join(parts[1:])] = z.read(info)
        except Exception as e:
            raise ValueError(f"the zip is damaged ({e.__class__.__name__})")
    if "load.py" not in files or LAST not in files:
        raise ValueError("the zip is not the plugin")
    if files[LAST].decode("utf-8", "replace").strip() != version:
        raise ValueError("the zip holds another version than the site named")
    return files


def developer_copy(plugin_dir: Path) -> bool:
    """A plugin folder in a git checkout is someone working on the plugin: an update never writes over it."""
    root = Path(plugin_dir)
    return (root / ".git").exists() or (root.parent / ".git").exists()


def put_in_place(plugin_dir: Path, files: dict) -> None:
    """Write `files` over the plugin folder: each to a .new file beside its place first, then all moved into place
    with version.txt last, then the plugin files the new version no longer has taken away. A write that fails takes
    its .new files away again and leaves the old version whole."""
    root = Path(plugin_dir)
    if developer_copy(root):
        raise ValueError("this is a developer's copy, which an update never writes over")
    staged = []
    try:
        for rel in sorted(files, key=lambda r: (r == LAST, r)):
            path = root.joinpath(*rel.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            new = path.with_name(path.name + ".new")
            new.write_bytes(files[rel])
            staged.append((new, path))
    except Exception:
        for new, _ in staged:
            try:
                new.unlink()
            except OSError:
                pass
        raise
    for new, path in staged:
        os.replace(new, path)
    for old in sorted(root.rglob("*")):
        rel = old.relative_to(root)
        if (old.is_file() and old.suffix in KINDS and not any(p in KEEP for p in rel.parts)
                and "/".join(rel.parts) not in files):
            try:
                old.unlink()
            except OSError:
                pass

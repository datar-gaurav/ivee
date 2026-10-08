#!/usr/bin/env python3
"""Download Kokoro's model files into a directory, verified by SHA-256.

Usage: fetch_models.py <dest-dir>
Stdlib only (runs before any venv exists). Skips files already present and valid.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0"
FILES = {
    "kokoro-v1.0.onnx": "7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main():
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    for name, digest in FILES.items():
        path = dest / name
        if path.exists() and sha256(path) == digest:
            continue
        print(f"Downloading {name}")
        tmp = path.with_suffix(path.suffix + ".part")
        urllib.request.urlretrieve(f"{BASE}/{name}", tmp)
        if sha256(tmp) != digest:
            tmp.unlink()
            sys.exit(f"{name}: checksum mismatch, refusing to use it")
        tmp.rename(path)


if __name__ == "__main__":
    main()

"""
ffmpeg downloader
copied as into build step in Dockerfile

Pinned to one yt-dlp/FFmpeg-Builds release and verified by sha256, so a
rebuild gets the same binaries. The monthly autobuild-YYYY-MM-<last day>
releases are the ones upstream keeps; pin to one of those. To update, pick
a newer one and copy the hashes from its checksums.sha256 asset.
"""

import hashlib
import os
import sys
import tarfile
import urllib.request

RELEASE = "autobuild-2026-09-30-18-57"
BUILD = "ffmpeg-N-127043-g5a54fcf75e"
BASE_URL = (
    f"https://github.com/yt-dlp/FFmpeg-Builds/releases/download/{RELEASE}"
)
ASSETS = {
    "linux/amd64": (
        f"{BUILD}-linux64-gpl.tar.xz",
        "a6cade1ba7f04e761b8070aa133ba80aa3c889c47026feae33ff20ebf3b0d9c7",
    ),
    "linux/arm64": (
        f"{BUILD}-linuxarm64-gpl.tar.xz",
        "23fd56211cfaa7afb5ee8b06058120e4b4288d09eae61928fcb489f295a17f8b",
    ),
}
BINARIES = ["ffmpeg", "ffprobe"]


def sha256sum(filename):
    """sha256 hex digest of a file"""
    digest = hashlib.sha256()
    with open(filename, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def download_extract(platform):
    """download, verify and extract binaries"""
    if platform not in ASSETS:
        raise ValueError(f"no ffmpeg build pinned for {platform}")

    name, expected = ASSETS[platform]
    print(f"download {name}")
    filename, _ = urllib.request.urlretrieve(f"{BASE_URL}/{name}")
    actual = sha256sum(filename)
    if actual != expected:
        raise ValueError(f"sha256 mismatch for {name}: {actual}")

    print("extract file")
    with tarfile.open(filename, "r:xz") as tar:
        for member in tar.getmembers():
            member.name = os.path.basename(member.name)
            if member.name in BINARIES:
                print(f"extract {member.name}")
                tar.extract(member, member.name)


def main():
    """entry point"""
    args = sys.argv
    if len(args) == 1:
        platform = "linux/amd64"
    else:
        platform = args[1]

    download_extract(platform)


if __name__ == "__main__":
    main()

"""Listing and reading under a granted folder without leaving it.

Every open starts from the granted folder's file descriptor and goes one name at a time with O_NOFOLLOW, so a
symlink anywhere on the way is refused, never followed. Nothing depends on checking a path string: a folder could be
swapped for a symlink between a check and an open. The names come from the broker's own listings, never from the
model, and each open is checked against the device and inode the entry had when it was listed.

Files are opened read-only and without blocking, so a FIFO can't stall a read. Only regular files with one link
are read, and only as UTF-8 text with no parser.
"""
from __future__ import annotations

import errno
import os
import stat
from dataclasses import dataclass
from typing import List, Sequence, Tuple

_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOCTTY | os.O_CLOEXEC


class Refused(Exception):
    """The filesystem isn't what the grant covers any more. reason is a contracts.Reason."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Found:
    name: str
    kind: str          # "file" or "folder"
    device: int
    inode: int
    size: int
    modified: float


def _open(name: str, flags: int, parent: int) -> int:
    if not name or name in (".", "..") or "/" in name or "\0" in name:
        raise Refused("changed")  # names come from listings, so this would be a bug; refuse anyway
    try:
        return os.open(name, flags, dir_fd=parent)
    except OSError as e:
        # A symlink opened with O_NOFOLLOW fails with ELOOP (EMLINK on FreeBSD). macOS says ENOTDIR instead when
        # O_DIRECTORY is set too, so look at the entry itself to tell a symlink from a folder replaced by a file.
        if e.errno in (errno.ELOOP, errno.EMLINK):
            raise Refused("symlink") from None
        if e.errno == errno.ENOTDIR:
            try:
                is_link = stat.S_ISLNK(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
            except OSError:
                is_link = False
            raise Refused("symlink" if is_link else "changed") from None
        if e.errno == errno.ENOENT:
            raise Refused("changed") from None
        raise


def _on_device(fd: int, device: int) -> os.stat_result:
    st = os.fstat(fd)
    if st.st_dev != device:
        raise Refused("other_device")
    return st


def open_entry(root_fd: int, root_device: int, parts: Sequence[str], kind: str, device: int, inode: int) -> int:
    """Walk from the granted folder to an entry and return an open descriptor for it, checked. The caller closes it."""
    if not isinstance(root_fd, int) or isinstance(root_fd, bool) or root_fd < 0:
        raise ValueError("no granted folder to start from")  # dir_fd=None would mean the working directory
    fd = os.open(".", _DIR, dir_fd=root_fd)
    try:
        _on_device(fd, root_device)
        for i, name in enumerate(parts):
            last = i == len(parts) - 1
            nxt = _open(name, _FILE if last and kind == "file" else _DIR, fd)
            os.close(fd)
            fd = nxt
            _on_device(fd, root_device)
        st = os.fstat(fd)
        if (st.st_dev, st.st_ino) != (device, inode):
            raise Refused("changed")
        if kind == "file":
            if not stat.S_ISREG(st.st_mode):
                raise Refused("changed")
            if st.st_nlink > 1:
                raise Refused("hard_link")
        return fd
    except BaseException:
        os.close(fd)
        raise


def list_folder(fd: int, root_device: int) -> Tuple[List[Found], int]:
    """A folder's entries that can be listed and read, and how many others were left out.

    Left out without being counted: hidden entries (names starting with a dot), which Finder doesn't show either, so
    a person granting a folder may not know they're there. Left out and counted: symlinks, pipes, sockets, devices,
    files with more than one link, and anything on another device.
    """
    found, skipped = [], 0
    for name in os.listdir(fd):
        if name.startswith("."):
            continue
        try:
            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        except OSError:
            skipped += 1
            continue
        if st.st_dev != root_device:
            skipped += 1
        elif stat.S_ISDIR(st.st_mode):
            found.append(Found(name, "folder", st.st_dev, st.st_ino, 0, st.st_mtime))
        elif stat.S_ISREG(st.st_mode) and st.st_nlink == 1:
            found.append(Found(name, "file", st.st_dev, st.st_ino, st.st_size, st.st_mtime))
        else:
            skipped += 1
    found.sort(key=lambda f: (f.name.casefold(), f.name))
    return found, skipped


def read_text(fd: int, max_chars: int, max_file_bytes: int) -> Tuple[str, bool, int]:
    """Up to max_chars characters of a regular file, whether it was cut off, and its size.

    A file over max_file_bytes is refused before anything is read, and so is one with a NUL byte in what was read
    (binary). Bytes that aren't valid UTF-8 come back as U+FFFD.
    """
    size = os.fstat(fd).st_size
    if size > max_file_bytes:
        raise Refused("too_large")
    want = min(size, 4 * max_chars + 4)  # enough bytes for max_chars characters, plus a byte-order mark
    chunks, got = [], 0
    while got < want:
        chunk = os.read(fd, want - got)
        if not chunk:
            break
        chunks.append(chunk)
        got += len(chunk)
    raw = b"".join(chunks)
    if b"\0" in raw:
        raise Refused("not_text")
    text = raw.decode("utf-8-sig", errors="replace")
    return text[:max_chars], len(text) > max_chars or got < size, size

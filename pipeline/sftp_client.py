"""SFTP access to the watch folder on the web host.

The SFTP account lands at the account root, not inside socialClips/, so
every path used here (config.PENDING_DIR etc.) already includes
config.SFTP_BASE_PATH.
"""

import posixpath
import stat

import paramiko

from pipeline import config


class WatchFolder:
    """Context-managed SFTP connection to the socialClips watch folder."""

    def __init__(self) -> None:
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def __enter__(self) -> "WatchFolder":
        self._transport = paramiko.Transport((config.SFTP_HOST, config.SFTP_PORT))
        self._transport.connect(username=config.SFTP_USER, password=config.SFTP_PASS)
        self._sftp = paramiko.SFTPClient.from_transport(self._transport)
        return self

    def __exit__(self, *exc) -> None:
        if self._sftp:
            self._sftp.close()
        if self._transport:
            self._transport.close()

    @property
    def sftp(self) -> paramiko.SFTPClient:
        assert self._sftp is not None, "WatchFolder used outside a with block"
        return self._sftp

    def list_pending_pairs(self) -> list[str]:
        """Return base names in pending/ that have BOTH <base>.mp4 and
        <base>.txt. An mp4 without its txt (or the reverse) is left alone;
        it may still be mid-upload.
        """
        names = set()
        for entry in self.sftp.listdir_attr(config.PENDING_DIR):
            if stat.S_ISREG(entry.st_mode):
                names.add(entry.filename)
        bases = []
        for name in sorted(names):
            if name.lower().endswith(".mp4"):
                base = name[:-4]
                if base + ".txt" in names:
                    bases.append(base)
        return bases

    def download_pair(self, base: str, local_dir: str) -> tuple[str, str]:
        """Download <base>.mp4 and <base>.txt into local_dir.
        Returns (video_path, text_path).
        """
        video_local = posixpath.join(local_dir, base + ".mp4")
        text_local = posixpath.join(local_dir, base + ".txt")
        self.sftp.get(posixpath.join(config.PENDING_DIR, base + ".mp4"), video_local)
        self.sftp.get(posixpath.join(config.PENDING_DIR, base + ".txt"), text_local)
        return video_local, text_local

    def move_pair(self, base: str, dest_dir: str) -> None:
        """Move both files of a pair out of pending/ into dest_dir
        (done/ or rejected/) on the host.
        """
        for ext in (".mp4", ".txt"):
            src = posixpath.join(config.PENDING_DIR, base + ext)
            dst = posixpath.join(dest_dir, base + ext)
            self._rename(src, dst)

    def _rename(self, src: str, dst: str) -> None:
        try:
            self.sftp.posix_rename(src, dst)
        except OSError:
            # posix_rename is an OpenSSH extension; some servers lack it.
            # Standard SFTP rename fails if dst exists, so clear it first.
            try:
                self.sftp.remove(dst)
            except OSError:
                pass
            self.sftp.rename(src, dst)

    def file_size(self, base: str) -> int:
        """Size in bytes of <base>.mp4 in pending/ (for a pre-download check)."""
        return self.sftp.stat(posixpath.join(config.PENDING_DIR, base + ".mp4")).st_size

    # Small-file helpers (publish state, persisted IG token). Callers pass
    # full paths relative to the SFTP login root (e.g. config.IG_TOKEN_FILE).

    def read_text(self, path: str) -> str | None:
        """Contents of a small text file on the host, or None if missing."""
        try:
            with self.sftp.open(path, "r") as fh:
                return fh.read().decode("utf-8")
        except FileNotFoundError:
            return None

    def write_text(self, path: str, content: str) -> None:
        with self.sftp.open(path, "w") as fh:
            fh.write(content)

    def move_file(self, src: str, dst: str) -> None:
        self._rename(src, dst)

    def delete_file(self, path: str) -> None:
        try:
            self.sftp.remove(path)
        except FileNotFoundError:
            pass

    def list_tree(self, path: str = ".", max_depth: int = 4) -> list[str]:
        """Recursive directory listing from `path`, for diagnosing the real
        layout of the SFTP account when a configured path guess is wrong.
        Returns formatted lines, directories marked with a trailing slash.
        """
        lines: list[str] = []
        self._walk(path, 0, max_depth, lines)
        return lines

    def _walk(self, path: str, depth: int, max_depth: int, lines: list[str]) -> None:
        indent = "  " * depth
        try:
            entries = sorted(self.sftp.listdir_attr(path), key=lambda e: e.filename)
        except OSError as exc:
            lines.append(f"{indent}[could not list {path!r}: {exc}]")
            return
        for entry in entries:
            is_dir = stat.S_ISDIR(entry.st_mode)
            name = entry.filename + ("/" if is_dir else "")
            lines.append(f"{indent}{name}")
            if is_dir and depth + 1 < max_depth:
                self._walk(posixpath.join(path, entry.filename), depth + 1, max_depth, lines)

"""SFTP access to the watch folder on the web host.

The SFTP account is jailed to socialClips/ as its home directory, so all
paths here are relative: pending/, done/, rejected/.
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

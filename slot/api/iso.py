"""An ISO image of a guest directory, put into the CD-ROM drive of other nodes (CONTRACTS.md 4.7, `iso-mount`).

`POST /v1/labs/{project_id}/nodes/{source}/iso` names the directory of the source guest and the target nodes. The
slot packs the directory inside the guest through its agent (tar on a POSIX guest, a zip archive made by .NET on
Windows), copies the archive out with `guest-file-read`, unpacks it into a private work directory (regular files
and directories only), builds the image with xorriso and sets `cdrom_image` of every target. GNS3 2.2.61 changes
the medium of a running QEMU node at once (`eject -f ide1-cd0`, `change ide1-cd0 <file>` in the QEMU monitor) and
a stopped node gets it when it starts.

Every build is a new file `lab-iso-<operation>.iso` in the project directory: a running QEMU keeps reading the medium
it has open, and GNS3 only tells QEMU about a value of `cdrom_image` that differs from the current one. Images no
node refers to any more are removed after the targets are switched. The image belongs to the project, so it moves
to another slot with the student's directory; GNS3 keeps its name relative to the project.
"""

import asyncio
import base64
import os
import shlex
import shutil
import stat
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

PREFIX = "lab-iso-"
VOLUME = "LABFILES"
CHUNK = 1024 * 1024  # bytes per guest-file-read; well below the 48 MiB the agent allows
PACK_TIMEOUT = 120.0
BUILD_TIMEOUT = 300.0


def max_bytes() -> int:
    """The largest archive and the largest unpacked tree, SLOT_ISO_MAX_BYTES (default 256 MiB)."""
    return int(os.getenv("SLOT_ISO_MAX_BYTES", str(256 * 1024 * 1024)))


def max_files() -> int:
    return int(os.getenv("SLOT_ISO_MAX_FILES", "10000"))


def xorriso() -> str | None:
    return shutil.which(os.getenv("SLOT_XORRISO", "xorriso"))


def enabled() -> bool:
    """The slot announces `iso-mount` only when it can build an image."""
    return xorriso() is not None


class IsoError(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


# -- inside the guest -----------------------------------------------------------------------------------------

def pack_command(family: str, path: str) -> tuple[str, str]:
    """The shell and the command that pack `path` into a temporary archive and print `<archive>|<bytes>`.

    Exit code 3: the directory does not exist. The path comes from the image catalog of the manager, never from a
    student, and is quoted for its shell anyway.
    """
    if family == "posix":
        return "sh", (
            f"d={shlex.quote(path)}\n"
            '[ -d "$d" ] || exit 3\n'
            "t=$(mktemp /tmp/lab-iso.XXXXXXXX) || exit 4\n"
            'tar -C "$d" -cf "$t" . || { rm -f "$t"; exit 4; }\n'
            'printf \'%s|%s\\n\' "$t" "$(wc -c < "$t" | tr -d \' \')"\n'
        )
    if family == "windows":
        quoted = "'" + path.replace("'", "''") + "'"
        # ZipFile.CreateFromDirectory needs .NET 4.5 (Windows 8 / Server 2012 and newer), not tar.exe.
        return "powershell", (
            "$ErrorActionPreference = 'Stop'\n"
            f"$d = {quoted}\n"
            "if (-not (Test-Path -LiteralPath $d -PathType Container)) { exit 3 }\n"
            "$t = Join-Path ([IO.Path]::GetTempPath()) ('lab-iso-' + [guid]::NewGuid().ToString('N') + '.zip')\n"
            "try {\n"
            "  Add-Type -AssemblyName System.IO.Compression.FileSystem\n"
            "  [IO.Compression.ZipFile]::CreateFromDirectory($d, $t)\n"
            "} catch { Remove-Item -LiteralPath $t -Force -ErrorAction SilentlyContinue; exit 4 }\n"
            "Write-Output ($t + '|' + (Get-Item -LiteralPath $t).Length)\n"
        )
    raise IsoError("NOT_SUPPORTED", f"An ISO image cannot be made from a {family} guest")


def cleanup_command(family: str, archive: str) -> tuple[str, str]:
    if family == "windows":
        return "powershell", "Remove-Item -LiteralPath '" + archive.replace("'", "''") + "' -Force"
    return "sh", "rm -f " + shlex.quote(archive)


def parse_packed(family: str, stdout: str) -> tuple[str, int]:
    """The archive path and size the pack command printed; only a name the command could have made is accepted."""
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines or lines[-1].count("|") != 1:
        raise IsoError("ISO_SOURCE_FAILED", "The guest did not report the archive")
    archive, size = lines[-1].split("|")
    if family == "posix":
        good = archive.startswith("/tmp/lab-iso.") and "/" not in archive[len("/tmp/"):]
    else:
        name = archive.replace("/", "\\").rsplit("\\", 1)[-1]
        good = name.startswith("lab-iso-") and name.endswith(".zip") and len(name) == 44
    if not good or not size.isdigit():
        raise IsoError("ISO_SOURCE_FAILED", "The guest reported an unexpected archive")
    return archive, int(size)


async def read_guest_file(qga, node_id, path, target: Path, limit: int, priority):
    """Copy a guest file to `target` with guest-file-read; at most `limit` bytes."""
    handle = await qga.call(node_id, "guest-file-open", {"path": path, "mode": "rb"}, priority=priority, timeout=10)
    if type(handle) is not int:
        raise IsoError("ISO_SOURCE_FAILED", "Malformed guest-file-open reply")
    total = 0
    try:
        with open(target, "wb") as out:
            while True:
                reply = await qga.call(node_id, "guest-file-read", {"handle": handle, "count": CHUNK},
                                       priority=priority, timeout=30)
                if not isinstance(reply, dict):
                    raise IsoError("ISO_SOURCE_FAILED", "Malformed guest-file-read reply")
                data = base64.b64decode(reply.get("buf-b64", ""))
                total += len(data)
                if total > limit:
                    raise IsoError("ISO_TOO_LARGE", f"The directory is larger than {limit} bytes")
                out.write(data)
                if reply.get("eof") or not data:
                    break
    finally:
        try:
            await qga.call(node_id, "guest-file-close", {"handle": handle}, priority=priority, timeout=10)
        except Exception:  # noqa: BLE001 - the handle dies with the agent anyway
            pass
    return total


# -- on the slot ----------------------------------------------------------------------------------------------

def _parts(name: str):
    """Safe relative path parts of an archive member, or None for a member to skip."""
    parts = [p for p in PurePosixPath(name.replace("\\", "/")).parts if p not in ("", ".", "/")]
    if not parts or any(p == ".." or "\x00" in p for p in parts):
        return None
    return parts


def unpack(archive: Path, kind: str, dest: Path, limit: int, files_limit: int) -> int:
    """Unpack regular files and directories into `dest`; links, devices and unsafe names are skipped.

    Returns the number of files. ISO_TOO_LARGE when the files add up to more than `limit` bytes or are too many.
    """
    dest.mkdir(parents=True, exist_ok=True)
    total = count = 0

    def add_bytes(size):
        nonlocal total
        total += size
        if total > limit:
            raise IsoError("ISO_TOO_LARGE", f"The directory is larger than {limit} bytes")

    def add_file():
        nonlocal count
        count += 1
        if count > files_limit:
            raise IsoError("ISO_TOO_LARGE", f"The directory has more than {files_limit} files")

    if kind == "tar":
        try:
            with tarfile.open(archive, "r:") as tar:
                members = []
                for member in tar:
                    parts = _parts(member.name)
                    if parts is None or not (member.isfile() or member.isdir()):
                        continue
                    member.name = "/".join(parts)
                    if member.isfile():
                        add_file()
                        add_bytes(member.size)
                    members.append(member)
                tar.extractall(dest, members=members, filter="data")
        except tarfile.TarError as exc:
            raise IsoError("ISO_SOURCE_FAILED", "The guest archive is damaged") from exc
        return count

    try:
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                parts = _parts(info.filename)
                mode = info.external_attr >> 16
                if parts is None or (mode and stat.S_ISLNK(mode)):
                    continue
                path = dest.joinpath(*parts)
                if info.filename.endswith(("/", "\\")):
                    path.mkdir(parents=True, exist_ok=True)
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                add_file()
                with zf.open(info) as src, open(path, "wb") as out:
                    while chunk := src.read(CHUNK):  # the declared size of a member is not trusted
                        add_bytes(len(chunk))
                        out.write(chunk)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, EOFError) as exc:
        raise IsoError("ISO_SOURCE_FAILED", "The guest archive is damaged") from exc
    return count


async def build(source: Path, out: Path):
    """xorriso: Rock Ridge for Linux, Joliet with long names for Windows. File names stay UTF-8 whatever the locale
    of the service is (`-local_charset`; -in_charset and -out_charset are about the image, not the disk)."""
    binary = xorriso()
    if binary is None:
        raise IsoError("NOT_SUPPORTED", "xorriso is not installed on the slot")
    process = await asyncio.create_subprocess_exec(
        binary, "-local_charset", "UTF-8", "-as", "mkisofs", "-quiet", "-r", "-J", "-joliet-long",
        "-V", VOLUME, "-o", str(out), str(source),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "LC_ALL": "C.UTF-8"},
    )
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), BUILD_TIMEOUT)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    if process.returncode != 0 or not out.is_file():
        tail = stderr.decode(errors="replace").strip().splitlines()[-1:] if stderr else []
        raise IsoError("ISO_BUILD_FAILED", "xorriso failed" + (f": {tail[0][:200]}" if tail else ""))


def drive_busy(properties: dict) -> bool:
    """QEMU puts the CD-ROM at IDE index 2, the place of an IDE hdc: such a node cannot take a medium."""
    return bool(properties.get("hdc_disk_image")) and properties.get("hdc_disk_interface", "ide") == "ide"


def unreferenced(project_dir: Path, nodes) -> list[Path]:
    """Images of this feature in the project directory that no node has in its drive."""
    used = {PurePosixPath(str((n.get("properties") or {}).get("cdrom_image") or "")).name for n in nodes}
    return [p for p in project_dir.glob(PREFIX + "*.iso") if p.name not in used]

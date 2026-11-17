"""ISO image of a guest directory into the CD-ROM drive of other nodes (CONTRACTS.md 4.7)."""
import base64
import io
import subprocess
import tarfile
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from api import iso
from api.qga import QgaUnavailable
from main import app
from test_slot import HEADERS, wait

needs_xorriso = pytest.mark.skipif(not iso.enabled(), reason="xorriso is not installed")


class Guest:
    """A fake guest agent: POSIX commands really run in /bin/sh on this machine, files are read from disk."""

    def __init__(self, family="posix", windows_zip=None, down=False):
        self.family, self.windows_zip, self.down = family, windows_zip, down
        self.handles, self.commands = {}, []

    async def call(self, node_id, command, args=None, **options):
        if self.down:
            raise QgaUnavailable("No guest agent")
        if command == "guest-get-osinfo":
            return {"id": "mswindows"} if self.family == "windows" else {"id": "debian"}
        if command == "guest-file-open":
            handle = len(self.handles) + 1
            self.handles[handle] = open(args["path"], "rb")
            return handle
        if command == "guest-file-read":
            data = self.handles[args["handle"]].read(args["count"])
            return {"count": len(data), "buf-b64": base64.b64encode(data).decode(), "eof": not data}
        if command == "guest-file-close":
            self.handles.pop(args["handle"]).close()
            return {}
        raise AssertionError(command)

    async def exec(self, node_id, command, *, shell="sh", **options):
        if self.down:
            raise QgaUnavailable("No guest agent")
        self.commands.append((shell, command))
        if shell == "powershell":
            if command.startswith("Remove-Item"):
                Path(self.windows_zip).unlink(missing_ok=True)
                return {"exit_code": 0, "stdout": "", "stderr": ""}
            if self.windows_zip is None:
                return {"exit_code": 3, "stdout": "", "stderr": ""}
            size = Path(self.windows_zip).stat().st_size
            return {"exit_code": 0, "stdout": f"{self.windows_zip}|{size}\r\n", "stderr": ""}
        done = subprocess.run(["/bin/sh", "-c", command], capture_output=True, text=True)
        return {"exit_code": done.returncode, "stdout": done.stdout, "stderr": done.stderr}

    async def forget(self, node_id):
        pass

    async def close(self):
        pass


@pytest.fixture
def lab(mock_server):
    client, _, nodes = mock_server
    pid = str(uuid4())
    data = {"name": "lab", "project_id": pid, "nodes": [
        {"name": name, "kind": "qemu", "image": "debian12.qcow2"} for name in ("SRC", "W1", "W2")]}
    assert wait(client, client.post("/v1/labs/deploy", json=data, headers=HEADERS))["status"] == "SUCCEEDED"
    by_name = {n["name"]: n for n in nodes[pid]}
    app.state.lifecycle.guest_families.clear()
    return client, pid, by_name


def make(client, pid, source, path, targets, key=None, **extra):
    headers = {**HEADERS, **({"Idempotency-Key": key} if key else {})}
    return client.post(f"/v1/labs/{pid}/nodes/{source['node_id']}/iso", headers=headers,
                       json={"path": str(path), "targets": [t["node_id"] for t in targets], **extra})


def iso_names(image):
    """File names of an image as Joliet and Rock Ridge show them."""
    out = subprocess.run(["xorriso", "-local_charset", "UTF-8", "-indev", str(image), "-find", "/"],
                         capture_output=True, text=True).stdout
    return {line.strip("'") for line in out.splitlines()}


@needs_xorriso
def test_posix_directory_becomes_cdrom_of_targets(lab, tmp_path, projects_dir):
    client, pid, nodes = lab
    source = tmp_path / "to-iso"
    (source / "Привет").mkdir(parents=True)
    (source / "Привет" / "файл.txt").write_text("ok")
    (source / "readme.txt").write_text("hi")
    (source / "link").symlink_to("/etc/passwd")
    guest = app.state.lifecycle.qga = Guest()
    result = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"], nodes["W2"]], key="k1"))
    assert result["status"] == "SUCCEEDED", result
    assert result["action"] == "iso" and result["progress"] == 100
    body = result["result"]
    assert body["files"] == 2 and body["iso"] == f"lab-iso-{result['operation_id']}.iso"
    assert [t["status"] for t in body["targets"]] == ["attached", "attached"]
    image = projects_dir / pid / body["iso"]
    assert image.stat().st_size == body["bytes"]
    for name in ("W1", "W2"):
        assert nodes[name]["properties"]["cdrom_image"] == str(image)
    assert "cdrom_image" not in nodes["SRC"]["properties"]
    assert {"/Привет/файл.txt", "/readme.txt"} <= iso_names(image) and "/link" not in iso_names(image)
    assert not list((projects_dir / pid).glob(".lab-iso-work-*"))  # the work directory is gone
    assert guest.commands[-1][1].startswith("rm -f /tmp/lab-iso.")  # and the archive in the guest
    assert not Path(guest.commands[-1][1].split()[-1]).exists()
    # A replay with the same key returns the same operation; a new build replaces the image and removes the old one.
    replay = make(client, pid, nodes["SRC"], source, [nodes["W1"], nodes["W2"]], key="k1")
    assert replay.json()["operation_id"] == result["operation_id"] and replay.json()["replayed"]
    second = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"]], key="k2"))
    assert second["status"] == "SUCCEEDED"
    assert image.exists()  # W2 still has the first image in its drive
    nodes["W2"]["properties"]["cdrom_image"] = ""
    third = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"]], key="k3"))
    assert [p.name for p in (projects_dir / pid).glob("lab-iso-*.iso")] == [third["result"]["iso"]]


@needs_xorriso
def test_windows_zip_with_backslashes(lab, tmp_path, projects_dir):
    client, pid, nodes = lab
    archive = tmp_path / ("lab-iso-" + "a" * 32 + ".zip")
    with zipfile.ZipFile(archive, "w") as zf:  # .NET Framework before 4.6.1 writes backslashes
        zf.writestr("Docs\\", "")
        zf.writestr("Docs\\отчёт.txt", "data")
        zf.writestr("..\\evil.txt", "no")
        zf.writestr("C:\\abs.txt", "abs")
    guest = app.state.lifecycle.qga = Guest("windows", windows_zip=str(archive))
    result = wait(client, make(client, pid, nodes["SRC"], "C:\\ToISO", [nodes["W1"]]))
    assert result["status"] == "SUCCEEDED", result
    names = iso_names(projects_dir / pid / result["result"]["iso"])
    assert "/Docs/отчёт.txt" in names and not any("evil" in n for n in names)
    assert guest.commands[0][0] == "powershell" and "$d = 'C:\\ToISO'" in guest.commands[0][1]
    assert not archive.exists()


@needs_xorriso
@pytest.mark.parametrize("case,code", [
    ("missing", "ISO_SOURCE_MISSING"), ("empty", "ISO_SOURCE_EMPTY"), ("large", "ISO_TOO_LARGE"),
    ("down", "GUEST_AGENT_UNAVAILABLE"), ("stopped", "ISO_SOURCE_UNAVAILABLE")])
def test_failures_leave_lab_running(lab, tmp_path, monkeypatch, case, code):
    client, pid, nodes = lab
    source = tmp_path / "to-iso"
    if case != "missing":
        (source / "emptydir").mkdir(parents=True)
    if case == "large":
        (source / "big.bin").write_bytes(b"x" * 20000)
        monkeypatch.setenv("SLOT_ISO_MAX_BYTES", "10000")
    if case == "stopped":
        nodes["SRC"]["status"] = "stopped"
        app.state.lifecycle.get("lab", pid)  # the monitor would fail the lab; this case only looks at the operation
    app.state.lifecycle.qga = Guest(down=case == "down")
    result = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"]]))
    assert result["status"] == "FAILED" and result["error"]["code"] == code, result
    assert "cdrom_image" not in nodes["W1"]["properties"]
    if case != "stopped":
        assert client.get(f"/v1/labs/{pid}/status", headers=HEADERS).json()["state"] == "READY"


@needs_xorriso
def test_target_failures_are_per_node(lab, tmp_path):
    client, pid, nodes = lab
    source = tmp_path / "to-iso"
    source.mkdir()
    (source / "a.txt").write_text("a")
    nodes["W1"]["properties"].update(hdc_disk_image="blank.qcow2", hdc_disk_interface="ide")
    app.state.lifecycle.qga = Guest()
    ghost = {"node_id": str(uuid4())}
    result = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"], ghost, nodes["W2"]]))
    assert result["status"] == "SUCCEEDED"
    assert [(t["status"], t.get("error")) for t in result["result"]["targets"]] == [
        ("failed", "ISO_DRIVE_BUSY"), ("failed", "NODE_NOT_FOUND"), ("attached", None)]
    only_bad = wait(client, make(client, pid, nodes["SRC"], source, [nodes["W1"]]))
    assert only_bad["status"] == "FAILED" and only_bad["error"]["code"] == "ISO_ATTACH_FAILED"


def test_request_validation(lab):
    client, pid, nodes = lab
    src, w1 = nodes["SRC"], nodes["W1"]
    assert make(client, pid, src, "relative/dir", [w1]).status_code == 422
    assert make(client, pid, src, "/dir\nrm -rf /", [w1]).status_code == 422
    assert make(client, pid, src, "/dir", []).status_code == 422
    assert make(client, pid, src, "/dir", [w1, w1]).status_code == 422
    assert make(client, pid, src, "/dir", [src]).status_code == 422
    unfenced = client.post(f"/v1/labs/{pid}/nodes/{src['node_id']}/iso", json={"path": "/d", "targets": [w1["node_id"]]},
                           headers={**HEADERS, "X-Lab-Epoch": "9"})
    assert unfenced.status_code == 409


def test_not_supported_without_xorriso(lab, monkeypatch):
    client, pid, nodes = lab
    monkeypatch.setenv("SLOT_XORRISO", "/bin/true")
    assert "iso-mount" in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    monkeypatch.setenv("SLOT_XORRISO", "no-such-xorriso")
    assert "iso-mount" not in client.get("/v1/capabilities", headers=HEADERS).json()["features"]
    response = make(client, pid, nodes["SRC"], "/dir", [nodes["W1"]])
    assert response.status_code == 409 and response.json()["detail"]["code"] == "NOT_SUPPORTED"


def test_routeros_is_not_a_source():
    with pytest.raises(iso.IsoError) as err:
        iso.pack_command("routeros", "/x")
    assert err.value.code == "NOT_SUPPORTED"


@pytest.mark.parametrize("family,stdout", [
    ("posix", "/etc/passwd|10"), ("posix", "/tmp/lab-iso.x/../../etc|1"), ("posix", "/tmp/lab-iso.ab|x"),
    ("windows", "C:\\Windows\\win.ini|5"), ("posix", "")])
def test_unexpected_archive_is_refused(family, stdout):
    with pytest.raises(iso.IsoError):
        iso.parse_packed(family, stdout)


def test_tar_limits_and_links(tmp_path):
    archive = tmp_path / "a.tar"
    with tarfile.open(archive, "w") as tar:
        for name, data in (("./a.txt", b"aa"), ("./d/b.txt", b"bbb")):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("./l")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/shadow"
        tar.addfile(link)
        dev = tarfile.TarInfo("./dev")
        dev.type = tarfile.CHRTYPE
        tar.addfile(dev)
    assert iso.unpack(archive, "tar", tmp_path / "out", 100, 10) == 2
    assert sorted(p.name for p in (tmp_path / "out").rglob("*")) == ["a.txt", "b.txt", "d"]
    with pytest.raises(iso.IsoError, match="larger"):
        iso.unpack(archive, "tar", tmp_path / "o2", 4, 10)
    with pytest.raises(iso.IsoError, match="more than 1 files"):
        iso.unpack(archive, "tar", tmp_path / "o3", 100, 1)


def test_zip_member_size_is_not_trusted(tmp_path):
    archive = tmp_path / "z.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("big.bin", b"\0" * 100000)  # compresses to almost nothing
    with pytest.raises(iso.IsoError, match="larger"):
        iso.unpack(archive, "zip", tmp_path / "out", 50000, 10)

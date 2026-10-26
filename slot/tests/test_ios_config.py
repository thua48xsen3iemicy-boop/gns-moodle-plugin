"""The ios.config collector: the saved configuration of Cisco IOL nodes (CONTRACTS.md 4.6).

The configurations below are written by hand in the shape IOS saves them. Captures from the images of the
catalog (L2 and L3) come from the stand and are added next to them. NVRAM is exercised with a stand-in for
the iou_export.py of gns3server: its real format is checked on the stand.
"""

import json
import os

import pytest
from api import ios_config
from api.facts import COLLECTORS, IOS_CONFIG
from api.ios_config import ConfigError, collect, parse_ios_config
from test_facts import FakeQga, scenario

PORTS = {f"Ethernet{a}/{p}": {"adapter": a, "port": p} for a in range(2) for p in range(4)}

SECRETS = ("5ecr3t-hash", "w0rd-of-the-user", "key-material")

# What IOL L2 saves: switch ports, an SVI, a banner whose text looks like configuration.
L2 = """\
!
! Last configuration change at 10:41:11 UTC Sun Oct 25 2026
!
version 15.2
service timestamps debug datetime msec
no service password-encryption
!
hostname SW1
!
enable secret 5 $1$5ecr3t-hash
username admin privilege 15 secret 5 $1$w0rd-of-the-user
!
ip cef
no ip domain-lookup
!
spanning-tree mode pvst
spanning-tree extend system-id
!
vlan internal allocation policy ascending
!
interface Ethernet0/0
 switchport access vlan 10
 switchport mode access
!
interface Ethernet0/1
 switchport trunk encapsulation dot1q
 switchport trunk allowed vlan 10,20,30-32
 switchport trunk allowed vlan add 40
 switchport trunk native vlan 99
 switchport mode trunk
!
interface Ethernet0/2
 switchport mode trunk
!
interface Ethernet0/3
 shutdown
!
interface Ethernet1/0
 switchport trunk allowed vlan none
 switchport mode trunk
!
interface Ethernet1/1
 switchport mode access
!
interface Vlan1
 no ip address
 shutdown
!
interface Vlan10
 ip address 10.0.10.2 255.255.255.0
!
banner motd ^C
interface Ethernet9/9
 shutdown
vlan 777
^C
!
line vty 0 4
 password key-material
 login
!
end
"""

# What IOL L3 saves: no spanning-tree line, so no switchport anywhere.
L3 = """\
!
version 15.4
hostname R1
!
interface Loopback0
 ip address 1.1.1.1 255.255.255.255
!
interface Ethernet0/0
 ip address 10.0.12.1 255.255.255.252
 ip ospf 1 area 0
!
interface Ethernet0/1
 no ip address
!
interface Ethernet0/1.10
 encapsulation dot1Q 10
 ip address 10.1.10.1 255.255.255.0
 ip address 10.1.11.1 255.255.255.0 secondary
!
interface Ethernet0/2
 ip address dhcp
!
interface Ethernet0/3
 no ip address
 shutdown
!
interface Serial2/0
 no ip address
 shutdown
 serial restart-delay 0
!
interface Tunnel0
 ip address 10.9.9.1 255.255.255.0
!
router ospf 1
 network 10.0.12.0 0.0.0.3 area 0
!
end
"""


def interfaces_of(text, ports=PORTS):
    return {i["name"]: i for i in parse_ios_config(text, ports)["interfaces"]}


# --- parser: L2 -------------------------------------------------------------------------------


def test_a_switch_is_described_by_its_ports():
    data = parse_ios_config(L2, PORTS)
    assert "vlans" not in data  # VTP server: the VLAN database is in vlan.dat
    by_name = {i["name"]: i for i in data["interfaces"]}
    assert list(by_name) == [
        "Ethernet0/0", "Ethernet0/1", "Ethernet0/2", "Ethernet0/3", "Ethernet1/0", "Ethernet1/1", "Vlan1", "Vlan10",
    ]
    assert by_name["Ethernet0/0"] == {
        "name": "Ethernet0/0", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up", "ipv4": [],
        "switchport": {"mode": "access", "vlan": 10},
    }
    assert by_name["Ethernet0/1"]["switchport"] == {
        "mode": "trunk", "allowed": [10, 20, 30, 31, 32, 40], "native": 99,
    }
    assert by_name["Ethernet0/2"]["switchport"] == {"mode": "trunk", "allowed": "all", "native": 1}
    assert by_name["Ethernet0/3"]["admin"] == "down"
    assert by_name["Ethernet0/3"]["switchport"] == {"mode": "access", "vlan": 1}  # no mode: see SLOT_REQUESTS 8
    assert by_name["Ethernet1/0"]["switchport"] == {"mode": "trunk", "allowed": [], "native": 1}
    assert by_name["Ethernet1/1"]["port"] == {"adapter": 1, "port": 1}
    assert by_name["Vlan1"] == {"name": "Vlan1", "kind": "svi", "vlan": 1, "admin": "down", "ipv4": []}
    assert by_name["Vlan10"] == {"name": "Vlan10", "kind": "svi", "vlan": 10, "admin": "up", "ipv4": ["10.0.10.2/24"]}
    assert all("oper" not in i and "ospf" not in i and "mac" not in i for i in data["interfaces"])
    assert set(data) == {"interfaces"}


def test_the_text_of_a_banner_is_not_configuration():
    # `interface Ethernet9/9` and `vlan 777` sit inside the banner of L2 above
    assert "Ethernet9/9" not in interfaces_of(L2)
    one_line = "banner login #interface Ethernet9/9#\ninterface Ethernet0/0\n shutdown\n"
    assert list(interfaces_of(one_line)) == ["Ethernet0/0"]
    macro = "macro name Edge\ninterface Ethernet9/9\n@\ninterface Ethernet0/0\n"
    assert list(interfaces_of(macro)) == ["Ethernet0/0"]


@pytest.mark.parametrize(
    "lines, expected",
    [
        ([], {"mode": "access", "vlan": 1}),
        (["switchport mode access", "switchport access vlan 30"], {"mode": "access", "vlan": 30}),
        (["switchport mode dynamic desirable", "switchport access vlan 30"], {"mode": "access", "vlan": 30}),
        (["switchport mode dynamic auto"], {"mode": "access", "vlan": 1}),
        (["switchport mode trunk"], {"mode": "trunk", "allowed": "all", "native": 1}),
        (
            ["switchport mode trunk", "switchport trunk allowed vlan 5-7,100", "switchport trunk allowed vlan add 8,9"],
            {"mode": "trunk", "allowed": [5, 6, 7, 8, 9, 100], "native": 1},
        ),
        (
            ["switchport mode trunk", "switchport trunk allowed vlan 5-9", "switchport trunk allowed vlan remove 6-7"],
            {"mode": "trunk", "allowed": [5, 8, 9], "native": 1},
        ),
        (
            ["switchport mode trunk", "switchport trunk allowed vlan none", "switchport trunk allowed vlan add 12"],
            {"mode": "trunk", "allowed": [12], "native": 1},
        ),
        (
            ["switchport mode trunk", "switchport trunk allowed vlan 1-4094"],
            {"mode": "trunk", "allowed": "all", "native": 1},
        ),
        (
            ["switchport mode trunk", "switchport trunk allowed vlan all"],
            {"mode": "trunk", "allowed": "all", "native": 1},
        ),
        (["switchport mode trunk", "switchport trunk native vlan 7"], {"mode": "trunk", "allowed": "all", "native": 7}),
        (["no switchport", "ip address 10.0.0.1 255.255.255.0"], None),  # a routed port
        (["switchport mode dot1q-tunnel"], None),  # not described by ios.config/1
        (["switchport mode private-vlan host"], None),
    ],
)
def test_switchport_follows_the_saved_commands(lines, expected):
    text = "spanning-tree mode pvst\ninterface Ethernet0/0\n" + "".join(f" {line}\n" for line in lines)
    entry = interfaces_of(text)["Ethernet0/0"]
    if expected is None:
        assert "switchport" not in entry
    else:
        assert entry["switchport"] == expected


def test_a_router_has_no_switchport_ever():
    text = "interface Ethernet0/0\n switchport mode trunk\n switchport access vlan 10\n"
    assert "switchport" not in interfaces_of(text)["Ethernet0/0"]  # no `spanning-tree mode`: not a switch
    assert "switchport" not in parse_ios_config(L3, PORTS)["interfaces"][0]


def test_only_physical_ports_of_a_switch_have_a_switchport():
    text = "spanning-tree mode rapid-pvst\ninterface Vlan5\n switchport mode access\ninterface Loopback0\n"
    assert all("switchport" not in i for i in interfaces_of(text).values())


# --- parser: L3 -------------------------------------------------------------------------------


def test_a_router_is_described_by_its_interfaces():
    by_name = interfaces_of(L3)
    assert list(by_name) == [
        "Ethernet0/0", "Ethernet0/1", "Ethernet0/1.10", "Ethernet0/2", "Ethernet0/3", "Loopback0", "Serial2/0",
    ]  # Tunnel0 is not described
    assert by_name["Loopback0"] == {"name": "Loopback0", "kind": "loopback", "admin": "up", "ipv4": ["1.1.1.1/32"]}
    assert by_name["Ethernet0/0"] == {
        "name": "Ethernet0/0", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up",
        "ipv4": ["10.0.12.1/30"],
    }
    assert by_name["Ethernet0/1"]["ipv4"] == []
    assert by_name["Ethernet0/1.10"] == {
        "name": "Ethernet0/1.10", "kind": "subif", "parent": "Ethernet0/1", "dot1q": 10, "admin": "up",
        "ipv4": ["10.1.10.1/24", "10.1.11.1/24"],
    }
    assert "ipv4" not in by_name["Ethernet0/2"]  # DHCP: the configuration does not say the address
    assert by_name["Ethernet0/3"]["admin"] == "down"
    assert by_name["Serial2/0"] == {"name": "Serial2/0", "kind": "physical", "admin": "down", "ipv4": []}  # no port


def test_addresses_come_in_ascending_order_and_a_new_primary_replaces_the_old_one():
    text = (
        "interface Ethernet0/0\n ip address 10.0.0.1 255.255.255.0\n ip address 9.9.9.9 255.255.255.0 secondary\n"
        " ip address 1.2.3.4 255.0.0.0 secondary\n ip address 10.0.1.1 255.255.255.0\n"
    )
    assert interfaces_of(text)["Ethernet0/0"]["ipv4"] == ["1.2.3.4/8", "9.9.9.9/24", "10.0.1.1/24"]


def test_no_ip_address_clears_what_was_set_and_dhcp_is_not_an_address():
    text = (
        "interface Ethernet0/0\n ip address 10.0.0.1 255.255.255.0\n no ip address\n"
        "interface Ethernet0/1\n ip address dhcp\n ip address negotiated\n"
        "interface Ethernet0/2\n ip address dhcp\n ip address 10.0.2.1 255.255.255.0\n"
    )
    by_name = interfaces_of(text)
    assert by_name["Ethernet0/0"]["ipv4"] == []
    assert "ipv4" not in by_name["Ethernet0/1"]
    assert by_name["Ethernet0/2"]["ipv4"] == ["10.0.2.1/24"]


def test_a_port_is_found_only_by_the_exact_name():
    near = {"Ethernet0/00": {"adapter": 0, "port": 0}}
    assert "port" not in interfaces_of("interface Ethernet0/0\n", near)["Ethernet0/0"]
    assert "port" not in interfaces_of("interface Ethernet0/0\n", {})["Ethernet0/0"]
    # a subinterface, an SVI and a loopback never have one
    text = "interface Ethernet0/0.5\n encapsulation dot1Q 5\ninterface Loopback0\ninterface Vlan3\n"
    ports = {name: {"adapter": 0, "port": 0} for name in ("Ethernet0/0.5", "Loopback0", "Vlan3")}
    assert all("port" not in i for i in interfaces_of(text, ports).values())


# --- parser: VLANs ----------------------------------------------------------------------------

TRANSPARENT = """\
vtp mode transparent
vlan 30
!
vlan 10
 name USERS
!
vlan 20
 name SERVERS
vlan 40-41
vlan internal allocation policy ascending
interface Ethernet0/0
"""


def test_vlans_are_listed_in_transparent_mode():
    data = parse_ios_config(TRANSPARENT, {})
    assert data["vlans"] == [
        {"id": 10, "name": "USERS"}, {"id": 20, "name": "SERVERS"}, {"id": 30}, {"id": 40}, {"id": 41},
    ]


def test_vlans_of_mode_off_and_an_empty_database():
    assert parse_ios_config("vtp mode off\nvlan 7\n name SEVEN\n", {})["vlans"] == [{"id": 7, "name": "SEVEN"}]
    assert parse_ios_config("vtp mode transparent\n", {})["vlans"] == []


@pytest.mark.parametrize("mode", ["", "vtp mode server\n", "vtp mode client\n"])
def test_no_vlans_where_the_database_is_vlan_dat(mode):
    assert "vlans" not in parse_ios_config(mode + "vlan 10\n name USERS\n", {})


def test_a_vlan_block_edited_twice_is_one_vlan():
    data = parse_ios_config("vtp mode transparent\nvlan 10\n name A\nvlan 10\n name B\n", {})
    assert data["vlans"] == [{"id": 10, "name": "B"}]


# --- parser: stability, secrets, garbage ------------------------------------------------------


def test_comments_and_the_order_of_blocks_do_not_change_data():
    first = parse_ios_config(L2, PORTS)
    changed = L2.replace("10:41:11 UTC Sun Oct 25 2026", "11:00:00 UTC Mon Oct 26 2026")
    changed = "! NVRAM config last updated at 11:00:01 UTC Mon Oct 26 2026\n" + changed
    assert parse_ios_config(changed, PORTS) == first
    head, _, rest = L2.partition("interface Ethernet0/0")
    body, _, tail = rest.partition("interface Vlan1")
    blocks = ("interface Ethernet0/0" + body).split("!\n")
    reordered = head + "!\n".join(reversed([b for b in blocks if b])) + "!\ninterface Vlan1" + tail
    assert reordered != L2 and parse_ios_config(reordered, PORTS) == first
    assert json.dumps(first) == json.dumps(parse_ios_config(reordered, PORTS))


def test_nothing_secret_leaves_the_parser():
    data = json.dumps(parse_ios_config(L2, PORTS))
    for secret in SECRETS:
        assert secret not in data
    text = "interface Ethernet0/0\n ip address 10.0.0.1 255.0.0.255\nenable secret 5 5ecr3t-hash\n"
    with pytest.raises(ConfigError) as caught:
        parse_ios_config(text, {})
    assert "Ethernet0/0" in str(caught.value)
    assert "5ecr3t-hash" not in str(caught.value) and "255.0.0.255" not in str(caught.value)


def test_an_empty_configuration_is_not_a_failure():
    assert parse_ios_config("", {}) == {"interfaces": []}
    assert parse_ios_config("!\n! just comments\n!\nend\n", {}) == {"interfaces": []}


@pytest.mark.parametrize(
    "line",
    [
        "ip address 10.0.0.1 255.0.255.0",  # holes in the mask
        "ip address 10.0.0.1 0.0.0.255",  # a hostmask
        "ip address 10.0.0.1 255.255.255.256",
        "ip address 10.0.0.1 banana",
        "ip address 10.0.0.300 255.255.255.0",
        "ip address 10.0.0.1",
        "ip address 10.0.0.1 255.255.255.0 tertiary",
        "ip address",
    ],
)
def test_a_broken_address_is_a_failed_collection(line):
    with pytest.raises(ConfigError):
        parse_ios_config(f"interface Ethernet0/0\n {line}\n", {})


@pytest.mark.parametrize(
    "lines",
    [
        ["switchport access vlan 4095"],
        ["switchport access vlan 0"],
        ["switchport access vlan ten"],
        ["switchport access vlan"],
        ["switchport trunk native vlan 5000"],
        ["switchport trunk allowed vlan 10-"],
        ["switchport trunk allowed vlan 20-10"],
        ["switchport trunk allowed vlan add"],
        ["switchport trunk allowed vlan 1,,2"],
        ["switchport trunk allowed vlan 1 2"],
    ],
)
def test_broken_vlan_numbers_are_a_failed_collection(lines):
    text = "spanning-tree mode pvst\ninterface Ethernet0/0\n" + "".join(f" {line}\n" for line in lines)
    with pytest.raises(ConfigError):
        parse_ios_config(text, {})


def test_a_broken_interface_name_or_vlan_block_is_a_failed_collection():
    with pytest.raises(ConfigError):
        parse_ios_config("interface Vlan5000\n", {})
    with pytest.raises(ConfigError):
        parse_ios_config("vlan 10-5\n", {})
    with pytest.raises(ConfigError):
        parse_ios_config("interface Ethernet0/0.1\n encapsulation dot1Q 5000\n", {})


# --- reading the file -------------------------------------------------------------------------

NODE = {"node_id": "n2", "name": "SW1", "node_type": "iou", "status": "started"}


def node_dir(projects_dir, project_id="p1", node_id="n2"):
    path = projects_dir / project_id / "project-files" / "iou" / node_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def test_no_file_is_no_snapshot(projects_dir):
    assert collect("p1", NODE, None) is None
    node_dir(projects_dir)
    assert collect("p1", NODE, None) is None  # the directory is there, GNS3 has written nothing yet


def test_the_file_is_found_in_the_node_directory_of_the_project(projects_dir):
    (node_dir(projects_dir) / "startup-config.cfg").write_text(L3)
    snapshot = collect("p1", NODE, None)
    assert snapshot.error is None and snapshot.data == parse_ios_config(L3, {})
    assert collect("p2", NODE, None) is None  # another project, another directory


def test_the_directory_gns3_names_is_used_when_it_names_one(tmp_path):
    elsewhere = tmp_path / "somewhere" / "n2"
    elsewhere.mkdir(parents=True)
    (elsewhere / "startup-config.cfg").write_text(L3)
    assert collect("p1", NODE, None) is None
    assert collect("p1", {**NODE, "node_directory": str(elsewhere)}, None).data == parse_ios_config(L3, {})


@pytest.mark.parametrize("ident", ["../x", "a/b", "", ".."])
def test_a_node_id_that_climbs_out_of_the_node_directory_is_not_looked_up(projects_dir, ident):
    root = projects_dir / "p1" / "project-files"
    for directory in (root, root / "iou", root / "x", root / "iou" / "a" / "b"):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "startup-config.cfg").write_text(L3)  # where such an id would lead
    assert collect("p1", {**NODE, "node_id": ident}, None) is None


def test_ports_come_from_the_node(projects_dir):
    (node_dir(projects_dir) / "startup-config.cfg").write_text("interface Ethernet0/1\n")
    node = {**NODE, "ports": [
        {"name": "Ethernet0/0", "adapter_number": 0, "port_number": 0},
        {"name": "Ethernet0/1", "adapter_number": 0, "port_number": 1},
        {"name": "broken"},
    ]}
    data = collect("p1", node, None).data
    assert data["interfaces"][0]["port"] == {"adapter": 0, "port": 1}
    twice = {**node, "ports": node["ports"] + [{"name": "Ethernet0/1", "adapter_number": 1, "port_number": 1}]}
    assert "port" not in collect("p1", twice, None).data["interfaces"][0]  # ambiguous: not guessed


def test_the_file_is_parsed_again_only_when_it_or_the_ports_changed(projects_dir, monkeypatch):
    path = node_dir(projects_dir) / "startup-config.cfg"
    path.write_text(L3)
    parsed = []
    real = ios_config.parse_ios_config
    monkeypatch.setattr(ios_config, "parse_ios_config", lambda text, ports: parsed.append(1) or real(text, ports))
    first = collect("p1", NODE, None)
    assert collect("p1", NODE, first) is first and len(parsed) == 1  # same size and time: not read again
    with_ports = {**NODE, "ports": [{"name": "Ethernet0/0", "adapter_number": 0, "port_number": 0}]}
    second = collect("p1", with_ports, first)
    assert second is not first and len(parsed) == 2
    stat = path.stat()
    path.write_text(L3.replace("10.0.12.1", "10.0.12.5"))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    third = collect("p1", with_ports, second)
    assert len(parsed) == 3 and "10.0.12.5/30" in json.dumps(third.data)
    path.write_text(L3.replace("10.0.12.1", "10.0.12.6")[:-1])  # another size, whatever the time
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert len(parsed) == 3 and collect("p1", with_ports, third) is not third and len(parsed) == 4


def test_a_configuration_that_did_not_parse_is_not_parsed_again(projects_dir, monkeypatch):
    path = node_dir(projects_dir) / "startup-config.cfg"
    path.write_text("interface Ethernet0/0\n ip address 10.0.0.1 255.0.255.0\n")
    first = collect("p1", NODE, None)
    assert first.data is None and "Ethernet0/0" in first.error
    monkeypatch.setattr(ios_config, "parse_ios_config", lambda *_: pytest.fail("parsed again"))
    assert collect("p1", NODE, first) is first


def test_what_is_not_a_saved_configuration_is_a_failure(projects_dir):
    directory = node_dir(projects_dir)
    path = directory / "startup-config.cfg"
    path.write_bytes(b"interface Ethernet0/0\n\x00\x01\x02binary\n")
    assert collect("p1", NODE, None).error == "The saved configuration is not text"
    path.write_bytes(b"x" * (ios_config.SIZE_LIMIT + 1))
    assert collect("p1", NODE, None).error == "The saved configuration is too large"
    path.write_bytes(L3.encode() + b"\x00" * 100)  # NVRAM is padded: that is still a configuration
    assert collect("p1", NODE, None).error is None
    path.unlink()
    path.mkdir()
    with pytest.raises(ConfigError, match="not a regular file"):
        collect("p1", NODE, None)
    path.rmdir()
    (directory / "elsewhere").write_text(L3)
    path.symlink_to(directory / "elsewhere")  # GNS3 writes a file, not a link
    with pytest.raises(ConfigError, match="not a regular file"):
        collect("p1", NODE, None)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_an_unreadable_file_is_not_remembered(projects_dir):
    path = node_dir(projects_dir) / "startup-config.cfg"
    path.write_text(L3)
    path.chmod(0)
    with pytest.raises(ios_config.Unreadable):
        collect("p1", NODE, None)
    path.chmod(0o644)
    assert collect("p1", NODE, None).error is None


# --- NVRAM ------------------------------------------------------------------------------------

# The same contract as the nvram_export of gns3server: (startup, private) or ValueError.
EXPORT = """\
def nvram_export(nvram):
    if nvram[:2] != b"\\xab\\xcd":
        raise ValueError("no startup config")
    if nvram[2:3] == b"!":
        raise ValueError("invalid length")
    return bytearray(nvram[3:]), bytearray(b"key-material")
"""


def nvram_of(config):
    return b"\xab\xcd\x01" + config.encode()


@pytest.fixture
def export(tmp_path, monkeypatch):
    path = tmp_path / "bin" / "iou_export.py"
    path.parent.mkdir()
    path.write_text(EXPORT)
    monkeypatch.setenv("SLOT_IOU_EXPORT", str(path))
    return path


def test_a_node_without_a_startup_config_file_is_read_from_its_nvram(projects_dir, export):
    # What GNS3 2.2.61 does to a node created without a configuration: it never writes startup-config.cfg
    (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L3) + b"\x00" * 64)
    snapshot = collect("p1", NODE, None)
    assert snapshot.error is None and snapshot.data == parse_ios_config(L3, {})


def test_an_empty_startup_config_is_never_updated_by_gns3_so_nvram_decides(projects_dir, export):
    directory = node_dir(projects_dir)
    (directory / "startup-config.cfg").write_text("")
    assert collect("p1", NODE, None) is None  # nothing in NVRAM yet
    (directory / "nvram_00001").write_bytes(nvram_of(L3))
    assert collect("p1", NODE, None).data == parse_ios_config(L3, {})


def test_a_startup_config_that_has_text_is_what_the_node_boots_with(projects_dir, export):
    directory = node_dir(projects_dir)
    (directory / "startup-config.cfg").write_text("hostname FILE\ninterface Loopback1\n")
    (directory / "nvram_00001").write_bytes(nvram_of(L3))
    data = collect("p1", NODE, None).data
    assert [i["name"] for i in data["interfaces"]] == ["Loopback1"]  # GNS3 pushes the file into NVRAM at start


@pytest.mark.parametrize("content", [b"", b"\x00" * 4096, b"\x00\x01 never written"])
def test_an_nvram_without_a_saved_configuration_is_nothing_saved(projects_dir, export, content):
    (node_dir(projects_dir) / "nvram_00001").write_bytes(content)
    assert collect("p1", NODE, None) is None


def test_an_nvram_with_an_empty_configuration_is_nothing_saved(projects_dir, export):
    (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(""))
    assert collect("p1", NODE, None) is None


def test_the_nvram_of_the_application_id_is_the_one_read(projects_dir, export):
    directory = node_dir(projects_dir)
    (directory / "nvram_00003").write_bytes(nvram_of("interface Loopback3\n"))
    (directory / "nvram_00004").write_bytes(nvram_of("interface Loopback4\n"))
    names = lambda node: [i["name"] for i in collect("p1", node, None).data["interfaces"]]  # noqa: E731
    assert names({**NODE, "properties": {"application_id": 4}}) == ["Loopback4"]
    assert collect("p1", NODE, None) is None  # two files and no id: not guessed
    (directory / "nvram_00004").unlink()
    assert names(NODE) == ["Loopback3"]  # only one: it is the node's


def test_the_private_configuration_is_not_looked_at(projects_dir, export):
    (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L2))
    assert "key-material" not in json.dumps(collect("p1", NODE, None).data)


def test_nvram_is_exported_again_only_when_it_changed(projects_dir, export, monkeypatch):
    path = node_dir(projects_dir) / "nvram_00001"
    path.write_bytes(nvram_of(L3))
    calls = []
    real = ios_config.read_nvram
    monkeypatch.setattr(ios_config, "read_nvram", lambda p: calls.append(p) or real(p))
    first = collect("p1", NODE, None)
    assert collect("p1", NODE, first) is first and len(calls) == 1
    stat = path.stat()
    path.write_bytes(nvram_of(L3.replace("1.1.1.1", "2.2.2.2")))
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert "2.2.2.2/32" in json.dumps(collect("p1", NODE, first).data) and len(calls) == 2


def test_an_nvram_that_does_not_export_is_a_failed_collection_that_is_not_repeated(projects_dir, export):
    (node_dir(projects_dir) / "nvram_00001").write_bytes(b"\xab\xcd!half a write")
    first = collect("p1", NODE, None)
    assert first.data is None and first.error == "Cannot export the configuration from NVRAM: ValueError"
    assert collect("p1", NODE, first) is first


def test_without_the_export_code_the_slot_says_it_cannot(projects_dir, tmp_path, monkeypatch):
    (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L3))
    monkeypatch.delenv("SLOT_IOU_EXPORT", raising=False)
    with pytest.raises(ios_config.Unsupported) as caught:
        collect("p1", NODE, None)
    assert caught.value.code == "unsupported" and "SLOT_IOU_EXPORT" in str(caught.value)
    monkeypatch.setenv("SLOT_IOU_EXPORT", str(tmp_path / "missing.py"))
    with pytest.raises(ios_config.Unsupported):
        collect("p1", NODE, None)
    broken = tmp_path / "broken.py"
    broken.write_text("def nvram_export(:\n")
    monkeypatch.setenv("SLOT_IOU_EXPORT", str(broken))
    with pytest.raises(ios_config.Unsupported):
        collect("p1", NODE, None)


def test_a_config_file_needs_no_export_code(projects_dir, monkeypatch):
    monkeypatch.delenv("SLOT_IOU_EXPORT", raising=False)
    (node_dir(projects_dir) / "startup-config.cfg").write_text(L3)
    assert collect("p1", NODE, None).error is None


def test_the_export_code_is_found_in_the_installed_gns3server_without_importing_it(
    projects_dir, tmp_path, monkeypatch
):
    package = tmp_path / "site" / "gns3server"
    (package / "compute" / "iou" / "utils").mkdir(parents=True)
    (package / "__init__.py").write_text("raise RuntimeError('the package must not be imported')\n")
    (package / "compute" / "iou" / "utils" / "iou_export.py").write_text(EXPORT)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.delenv("SLOT_IOU_EXPORT", raising=False)
    (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L3))
    assert collect("p1", NODE, None).data == parse_ios_config(L3, {})


# --- the collector in Facts -------------------------------------------------------------------


def config_path(projects_dir, node_id="n2"):
    return node_dir(projects_dir, "p1", node_id) / "startup-config.cfg"


def saved_of(facts, node_id="n2"):
    return facts.snapshot("p1")["nodes"][node_id]["collectors"]["ios.config"]


def test_the_collector_is_registered_for_iou_nodes_only():
    assert IOS_CONFIG in COLLECTORS and IOS_CONFIG.schema == "ios.config/1"
    assert IOS_CONFIG.local is not None
    assert IOS_CONFIG.applies({"node_type": "iou"}, {}) is True
    for kind in ("qemu", "vpcs", "dynamips", None):
        assert IOS_CONFIG.applies({"node_type": kind}, {}) is False


def test_the_saved_configuration_is_published_without_a_guest_or_a_console(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        labs["p1"]["nodes"][1]["ports"] = [{"name": "Ethernet0/0", "adapter_number": 0, "port_number": 0}]
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        record = saved_of(facts)
        assert record["schema"] == "ios.config/1" and record["ok"] is True and record["error"] is None
        assert record["attempted_at"] == record["observed_at"] and record["attempted_at"].endswith("Z")
        assert set(record["data"]) == {"interfaces"}
        assert record["data"]["interfaces"][0] == {
            "name": "Ethernet0/0", "kind": "physical", "port": {"adapter": 0, "port": 0}, "admin": "up",
            "ipv4": ["10.0.12.1/30"],
        }
        assert list(facts.snapshot("p1")["nodes"]["n2"]["collectors"]) == ["ios.config"]
        # the IOU node was not asked what it is, and no command went to it
        assert {call[0] for call in lifecycle.qga.calls} == {"n1"}
        assert {call[0] for call in lifecycle.qga.osinfo_calls} == {"n1"}
        assert (facts.families.keys() & {("p1", "n2")}) == set()

    scenario(monkeypatch, body)


def test_a_stopped_node_has_its_saved_configuration_all_the_same(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        labs["p1"]["nodes"][1]["status"] = "stopped"
        config_path(projects_dir).write_text(L2)
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is True and record["error"] is None and record["data"]["interfaces"]

    scenario(monkeypatch, body)


def test_no_file_no_record_until_there_is_one(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        await facts.cycle()
        assert "n2" not in facts.snapshot("p1")["nodes"]  # not an error: the node has no facts yet
        node_dir(projects_dir)
        await facts.cycle()
        assert "n2" not in facts.snapshot("p1")["nodes"]
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        assert saved_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_an_unchanged_file_is_not_parsed_again_but_the_attempt_is_recorded(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        first = saved_of(facts)
        parsed = []
        monkeypatch.setattr(ios_config, "parse_ios_config", lambda *args: parsed.append(1))
        facts.request_refresh()
        await facts.cycle()
        second = saved_of(facts)
        assert parsed == []
        assert second["data"] == first["data"] and second["ok"] is True
        assert second["attempted_at"] >= first["attempted_at"]

    scenario(monkeypatch, body)


def test_a_changed_file_gives_new_data_and_a_comment_gives_the_same(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        path = config_path(projects_dir)
        path.write_text(L3)
        await facts.cycle()
        first = saved_of(facts)["data"]
        path.write_text(L3 + "! NVRAM config last updated at 11:00:01 UTC Mon Oct 26 2026\n")
        await facts.cycle()
        assert saved_of(facts)["data"] == first
        path.write_text(L3.replace("10.0.12.1 255.255.255.252", "10.0.12.5 255.255.255.252"))
        await facts.cycle()
        assert saved_of(facts)["data"] != first
        assert saved_of(facts)["data"]["interfaces"][0]["ipv4"] == ["10.0.12.5/30"]

    scenario(monkeypatch, body)


def test_a_broken_configuration_keeps_the_last_known_data_and_never_quotes_the_file(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        path = config_path(projects_dir)
        path.write_text(L2)
        await facts.cycle()
        first = saved_of(facts)
        path.write_text(L2.replace("ip address 10.0.10.2 255.255.255.0", "ip address 10.0.10.2 255.0.255.0"))
        await facts.cycle()
        broken = saved_of(facts)
        assert broken["ok"] is False and broken["error"]["code"] == "failed"
        assert "Vlan10" in broken["error"]["message"]
        assert broken["data"] == first["data"] and broken["observed_at"] == first["observed_at"]
        for secret in SECRETS:
            assert secret not in json.dumps(facts.snapshot("p1"))
        path.write_text(L2)
        await facts.cycle()
        assert saved_of(facts)["ok"] is True and saved_of(facts)["error"] is None

    scenario(monkeypatch, body)


def test_a_broken_configuration_is_published_even_before_the_first_success(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text("interface Ethernet0/0\n ip address 10.0.0.1 banana\n")
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "failed"
        assert "data" not in record and "observed_at" not in record

    scenario(monkeypatch, body)


def test_a_file_that_goes_away_leaves_stale_facts_that_say_so(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        path = config_path(projects_dir)
        path.write_text(L3)
        await facts.cycle()
        first = saved_of(facts)
        path.unlink()
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "failed"
        assert record["data"] == first["data"] and record["observed_at"] == first["observed_at"]
        path.write_text(L3)
        await facts.cycle()
        assert saved_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_an_unreadable_file_is_a_failed_collection(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).mkdir()  # not a regular file
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "failed" and "data" not in record

    scenario(monkeypatch, body)


def test_the_collector_does_not_wait_for_a_busy_channel_and_does_not_busy_it(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        from api.qga import QgaBusy

        lifecycle.qga.reply = QgaBusy("student command in flight")
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        assert saved_of(facts)["ok"] is True  # the QGA channel of other nodes is not its business

    scenario(monkeypatch, body)


def test_the_owner_change_drops_the_configuration_and_what_was_remembered_of_it(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        assert "n2" in facts.snapshot("p1")["nodes"] and facts.snapshots
        facts.reset()
        assert facts.snapshot("p1")["nodes"] == {} and facts.snapshots == {}
        await facts.cycle()
        assert saved_of(facts)["ok"] is True  # read again for the new owner

    scenario(monkeypatch, body)


def test_an_attempt_of_the_previous_owner_leaves_nothing_behind(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text(L3)
        real = ios_config.read_text
        reads = []

        def reset_while_reading(path, info):
            reads.append(path)
            facts.reset()  # the owner changes while the file is being read
            return real(path, info)

        monkeypatch.setattr(ios_config, "read_text", reset_while_reading)
        await facts.cycle()
        assert reads and "n2" not in facts.snapshot("p1")["nodes"] and facts.snapshots == {}

    scenario(monkeypatch, body)


def test_results_and_memory_of_a_lab_that_left_ready_are_dropped(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        assert facts.snapshots
        labs["p1"]["state"] = "STOPPED"
        await facts.cycle()
        assert facts.snapshots == {} and facts.snapshot("p1")["nodes"] == {}

    scenario(monkeypatch, body)


def test_other_collectors_are_not_touched_by_the_new_one(monkeypatch, projects_dir):
    async def body(facts, lifecycle, labs):
        config_path(projects_dir).write_text(L3)
        await facts.cycle()
        nodes = facts.snapshot("p1")["nodes"]
        assert list(nodes["n1"]["collectors"]) == ["linux.net"]  # the QEMU node has no ios.config
        assert list(nodes["n2"]["collectors"]) == ["ios.config"]  # the IOU node has no guest collector
        assert isinstance(lifecycle.qga, FakeQga)

    scenario(monkeypatch, body)


def test_a_node_that_gns3_never_wrote_a_file_for_is_read_from_nvram(monkeypatch, projects_dir, export):
    async def body(facts, lifecycle, labs):
        (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L3))
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is True and record["data"] == parse_ios_config(L3, {})
        assert "key-material" not in json.dumps(facts.snapshot("p1"))

    scenario(monkeypatch, body)


def test_a_slot_that_cannot_export_says_unsupported_and_recovers(monkeypatch, projects_dir, tmp_path):
    async def body(facts, lifecycle, labs):
        monkeypatch.delenv("SLOT_IOU_EXPORT", raising=False)
        (node_dir(projects_dir) / "nvram_00001").write_bytes(nvram_of(L3))
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "unsupported" and "data" not in record
        path = tmp_path / "iou_export.py"
        path.write_text(EXPORT)
        monkeypatch.setenv("SLOT_IOU_EXPORT", str(path))
        await facts.cycle()
        assert saved_of(facts)["ok"] is True

    scenario(monkeypatch, body)


def test_an_erased_nvram_leaves_stale_facts_that_say_so(monkeypatch, projects_dir, export):
    async def body(facts, lifecycle, labs):
        path = node_dir(projects_dir) / "nvram_00001"
        path.write_bytes(nvram_of(L3))
        await facts.cycle()
        path.write_bytes(b"\x00" * 4096)  # write erase and a reload
        await facts.cycle()
        record = saved_of(facts)
        assert record["ok"] is False and record["error"]["code"] == "failed" and record["data"]

    scenario(monkeypatch, body)

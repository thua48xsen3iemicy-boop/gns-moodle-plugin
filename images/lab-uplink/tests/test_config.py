"""Generated configuration for 1 and 4 LAN ports (no guest, no privileges)."""

import shutil
import json
import subprocess

import pytest

from uplink_net import config, setup

MACS = [f"52:54:00:12:34:{n:02x}" for n in range(6)]


def ports(count):
    """Out of order on purpose: adapter numbers follow the MACs, not the interface names."""
    cards = [config.Port(f"ens{3 + n}", MACS[n]) for n in range(count)]
    return cards[::-1]


def test_roles_follow_the_mac_order():
    outer, lan = config.split(ports(4))
    assert outer.name == "ens3" and [p.name for p in lan] == ["ens4", "ens5", "ens6"]


def test_a_node_without_cards_is_an_error():
    with pytest.raises(ValueError):
        config.split([])


def test_too_many_lan_ports_are_refused():
    many = [config.Port(f"e{n}", f"52:54:00:00:00:{n:02x}") for n in range(config.MAX_LAN + 2)]
    with pytest.raises(ValueError):
        config.split(many)


def test_networkd_four_lan_ports():
    files = config.networkd(ports(4))
    assert sorted(files) == ["10-lab-uplink-outer.network", "20-lab-uplink-lan1.network",
                             "20-lab-uplink-lan2.network", "20-lab-uplink-lan3.network"]
    outer = files["10-lab-uplink-outer.network"]
    assert f"MACAddress={MACS[0]}" in outer and not any(l.startswith("Address=") for l in outer.splitlines()) and "LinkLocalAddressing=no" in outer
    for n in (1, 2, 3):
        text = files[f"20-lab-uplink-lan{n}.network"]
        assert f"MACAddress={MACS[n]}" in text and f"Address=198.18.{n}.1/24" in text


def test_networkd_one_card_has_only_the_outer_port():
    assert list(config.networkd(ports(1))) == ["10-lab-uplink-outer.network"]


def test_pppd_options():
    text = config.pppd(ports(4))
    lines = text.splitlines()
    for wanted in ("plugin rp-pppoe.so", "nic-ens3", "persist", "maxfail 0", "defaultroute", "usepeerdns",
                   "mtu 1492", "mru 1492", "noauth", "nodetach", "file /etc/lab-uplink/pppoe.options"):
        assert wanted in lines
    assert "password" not in text.replace("hide-password", "")


def test_dnsmasq_four_lan_ports():
    text = config.dnsmasq(ports(4))
    assert "resolv-file=/etc/ppp/resolv.conf" in text and "no-resolv" not in text
    for n, name in ((1, "ens4"), (2, "ens5"), (3, "ens6")):
        assert f"interface={name}" in text
        assert f"listen-address=198.18.{n}.1" in text
        assert f"dhcp-range=set:lan{n},198.18.{n}.100,198.18.{n}.250,255.255.255.0,12h" in text
        assert f"dhcp-option=tag:lan{n},option:router,198.18.{n}.1" in text
        assert f"dhcp-option=tag:lan{n},option:dns-server,198.18.{n}.1" in text
    assert "ens3" not in text and "198.18.4." not in text


def test_dnsmasq_one_lan_port():
    text = config.dnsmasq(ports(2))
    assert [l for l in text.splitlines() if l.startswith("interface=")] == ["interface=ens4"] and "listen-address=198.18.1.1" in text and "198.18.2." not in text


def test_nftables_four_lan_ports():
    text = config.nftables(ports(4))
    assert 'elements = { "ens4", "ens5", "ens6" }' in text
    assert "ens3" not in text  # the outer port is not named: it only carries PPPoE frames, outside nftables' IP path
    assert text.count("policy drop") == 2  # input and forward
    assert 'iifname @lan oifname "ppp0" accept' in text
    assert 'iifname @lan udp dport { 53, 67 } accept' in text and 'iifname @lan tcp dport 53 accept' in text
    assert 'oifname "ppp0" masquerade' in text
    assert text.count("tcp option maxseg size set rt mtu") == 2
    # LAN <-> LAN is not allowed: forward accepts only replies and LAN -> ppp0
    forward = text.split("chain forward")[1].split("chain output")[0]
    assert [l.strip() for l in forward.splitlines() if "accept" in l] == [
        "ct state established,related accept", 'iifname @lan oifname "ppp0" accept']


def test_nftables_one_card_has_an_empty_lan():
    text = config.nftables(ports(1))
    assert 'elements = { "none" }' in text


def test_only_selected_lans_can_initiate_connections_to_each_other():
    text = config.nftables(ports(5), [3, 1])
    forward = text.split("chain forward")[1].split("chain output")[0]
    routed_rules = [line.strip() for line in forward.splitlines() if "oifname {" in line]
    assert routed_rules == ['iifname "ens4" oifname { "ens6" } accept',
                            'iifname "ens6" oifname { "ens4" } accept']
    assert text.count("masquerade") == 1
    assert 'oifname "ppp0" masquerade' in text  # no NAT between LANs
    assert text.split("chain input")[1].split("chain forward")[0] == (
        config.nftables(ports(5)).split("chain input")[1].split("chain forward")[0])


@pytest.mark.parametrize("selected", [[1], [1, 1], [0, 2], [1, 16], [True, 2], "1 2", None])
def test_invalid_routing_policy_is_rejected(selected):
    with pytest.raises(ValueError):
        config.nftables(ports(5), selected)


def test_missing_selected_adapter_is_rejected():
    with pytest.raises(ValueError, match="missing"):
        config.nftables(ports(2), [1, 2])


@pytest.mark.skipif(shutil.which("nft") is None, reason="nft is not installed")
@pytest.mark.parametrize("count", [1, 2, 5])
@pytest.mark.parametrize("routed", [False, True])
def test_nftables_syntax_if_the_kernel_allows_a_check(count, routed, tmp_path):
    if routed and count < 3:
        pytest.skip("routing needs two LAN ports")
    path = tmp_path / "rules.nft"
    path.write_text(config.nftables(ports(count), [1, 2] if routed else []))
    result = subprocess.run(["nft", "-c", "-f", str(path)], capture_output=True, text=True)
    if "Operation not permitted" in result.stderr or "Operation not supported" in result.stderr:
        pytest.skip("this environment cannot check nftables rules")
    assert result.returncode == 0, result.stderr


def fake_sys_net(tmp_path, cards):
    root = tmp_path / "net"
    for name, kind, mac, device in cards:
        d = root / name
        d.mkdir(parents=True)
        (d / "type").write_text(f"{kind}\n")
        (d / "address").write_text(f"{mac.upper()}\n")
        if device:
            (d / "device").mkdir()
    return root


def test_setup_writes_everything_for_the_real_cards_only(tmp_path):
    sys_net = fake_sys_net(tmp_path, [
        ("lo", 772, "00:00:00:00:00:00", False), ("ens4", 1, MACS[1], True), ("ens3", 1, MACS[0], True),
        ("br0", 1, MACS[5], False), ("ens5", 1, MACS[2], True)])
    run, networkd = tmp_path / "run", tmp_path / "networkd"
    setup.main(sys_net, run, networkd)
    assert sorted(p.name for p in networkd.iterdir()) == [
        "10-lab-uplink-outer.network", "20-lab-uplink-lan1.network", "20-lab-uplink-lan2.network"]
    assert "nic-ens3" in (run / "pppd.options").read_text()
    assert "interface=ens4" in (run / "dnsmasq.conf").read_text()
    assert "flush ruleset" in (run / "nftables.nft").read_text()


def test_setup_with_one_card_has_no_dnsmasq_config(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "dnsmasq.conf").write_text("stale")
    setup.main(fake_sys_net(tmp_path, [("ens3", 1, MACS[0], True)]), run, tmp_path / "networkd")
    assert not (run / "dnsmasq.conf").exists()


def test_setup_loads_routing_policy(tmp_path):
    policy = tmp_path / "routing.json"
    policy.write_text(json.dumps({"routed_lan_ports": [1, 2]}))
    cards = [(f"ens{3+n}", 1, MACS[n], True) for n in range(3)]
    run = tmp_path / "run"
    setup.main(fake_sys_net(tmp_path, cards), run, tmp_path / "networkd", policy)
    assert 'iifname "ens4" oifname { "ens5" } accept' in (run / "nftables.nft").read_text()


def test_missing_policy_preserves_isolation(tmp_path):
    assert setup.read_routing(tmp_path / "absent.json") == []


@pytest.mark.parametrize("contents", ['{', '[]', '{}', '{"routed_lan_ports": [1]}',
                                      '{"routed_lan_ports": [1, 2], "typo": true}'])
def test_invalid_policy_fails_before_generating_files(tmp_path, contents):
    policy = tmp_path / "routing.json"
    policy.write_text(contents)
    run, networkd = tmp_path / "run", tmp_path / "networkd"
    cards = [(f"ens{3+n}", 1, MACS[n], True) for n in range(3)]
    with pytest.raises(ValueError):
        setup.main(fake_sys_net(tmp_path, cards), run, networkd, policy)
    assert not run.exists() and not networkd.exists()

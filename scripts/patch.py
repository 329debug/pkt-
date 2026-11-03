"""在已有 .pkt 上做定点修改（保留原拓扑、连线、端口映射）。

补丁 JSON 结构：
{
  "hosts":      {"主机0": {"ip": "...", "mask": "...", "gateway": "...", "dns": "..."}},
  "interfaces": {"路由器1": {"FastEthernet0/0": {"ip": "...", "mask": "...", "shutdown": false, "description": "..."}}},
  "routes":     {"路由器1": ["ip route 172.16.3.0 255.255.255.0 172.16.1.6"]},
  "notes":      {"旧备注文本": "新备注文本"}
}
"""

import copy
import xml.etree.ElementTree as ET


def _engine(dev):
    return dev.find("ENGINE")


def _device_map(root):
    result = {}
    for dev in root.findall(".//DEVICE"):
        result[dev.findtext("./ENGINE/NAME")] = dev
    return result


def _interface_names(engine):
    rc = engine.find("RUNNINGCONFIG")
    if rc is None:
        return []
    names = []
    for line in rc.findall("LINE"):
        text = (line.text or "").strip()
        if text.startswith("interface "):
            name = text[len("interface "):].strip()
            if name.lower().startswith("vlan"):
                continue
            names.append(name)
    return names


def _rc_lines(engine):
    rc = engine.find("RUNNINGCONFIG")
    return rc, [(line.text or "") for line in rc.findall("LINE")]


def _write_rc(rc, items):
    for child in list(rc):
        rc.remove(child)
    for text in items:
        ET.SubElement(rc, "LINE").text = text


def _set_text(parent, tag, value):
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = value


def _rewrite_stanza(items, name, cfg):
    start = None
    for i, line in enumerate(items):
        if line.strip() == "interface " + name:
            start = i
            break
    if start is None:
        return False
    end = start + 1
    while end < len(items) and items[end].strip() != "!" and not items[end].strip().startswith("interface "):
        end += 1

    current_ip = None
    current_mask = None
    current_desc = None
    current_shutdown = False
    keep = []
    for line in items[start + 1:end]:
        stripped = line.strip()
        if stripped.startswith("ip address "):
            parts = stripped.split()
            current_ip = parts[2]
            current_mask = parts[3] if len(parts) > 3 else None
            continue
        if stripped == "no ip address":
            continue
        if stripped in ("duplex auto", "speed auto"):
            continue
        if stripped == "shutdown":
            current_shutdown = True
            continue
        if stripped.startswith("description "):
            current_desc = stripped[len("description "):]
            continue
        keep.append(line)

    ip = cfg["ip"] if "ip" in cfg else current_ip
    mask = cfg.get("mask") or current_mask or ("255.255.255.0" if ip else None)
    desc = cfg.get("description", current_desc)
    shutdown = cfg.get("shutdown", current_shutdown)

    block = ["interface " + name]
    if desc:
        block.append(" description " + desc)
    if ip:
        block.append(" ip address %s %s" % (ip, mask))
        block.append(" duplex auto")
        block.append(" speed auto")
    else:
        block.append(" no ip address")
    block.extend(keep)
    if shutdown:
        block.append(" shutdown")
    items[start:end] = block
    return True


def apply_patch(xml_bytes, spec):
    root = ET.fromstring(xml_bytes)
    devices = _device_map(root)

    for name, cfg in (spec.get("hosts") or {}).items():
        if name not in devices:
            raise ValueError("补丁里的主机 %r 不存在，可用设备: %s" % (name, ", ".join(devices)))
        engine = _engine(devices[name])
        ports = engine.findall(".//PORT")
        if not ports:
            raise ValueError("%s 没有端口" % name)
        port = ports[0]
        if cfg.get("ip"):
            _set_text(port, "IP", cfg["ip"])
            _set_text(port, "SUBNET", cfg.get("mask") or "255.255.255.0")
        if cfg.get("gateway") is not None:
            _set_text(engine, "GATEWAY", cfg["gateway"])
        if cfg.get("dns"):
            dns = engine.find("DNS_CLIENT")
            if dns is None:
                dns = ET.SubElement(engine, "DNS_CLIENT")
            _set_text(dns, "SERVER_IP", cfg["dns"])

    for name, ifaces in (spec.get("interfaces") or {}).items():
        if name not in devices:
            raise ValueError("补丁里的设备 %r 不存在" % name)
        engine = _engine(devices[name])
        names = _interface_names(engine)
        ports = engine.findall(".//PORT")
        if len(names) != len(ports):
            raise ValueError("%s 的端口名与 RUNNINGCONFIG 不一致，无法按名定位" % name)
        rc, items = _rc_lines(engine)
        for port_name, cfg in ifaces.items():
            if port_name not in names:
                raise ValueError("%s 没有接口 %s，可用: %s" % (name, port_name, ", ".join(names)))
            port = ports[names.index(port_name)]
            if cfg.get("ip"):
                _set_text(port, "IP", cfg["ip"])
                _set_text(port, "SUBNET", cfg.get("mask") or "255.255.255.0")
            elif "ip" in cfg:
                _set_text(port, "IP", None)
                _set_text(port, "SUBNET", None)
            _set_text(port, "POWER", "false" if cfg.get("shutdown") else "true")
            _rewrite_stanza(items, port_name, cfg)
        _write_rc(rc, items)

    for name, routes in (spec.get("routes") or {}).items():
        if name not in devices:
            raise ValueError("补丁里的设备 %r 不存在" % name)
        engine = _engine(devices[name])
        rc, items = _rc_lines(engine)
        items = [line for line in items if not line.strip().startswith("ip route ")]
        insert_at = next((i for i, line in enumerate(items) if line.strip() == "ip classless"), None)
        if insert_at is None:
            insert_at = next((i - 1 for i, line in enumerate(items) if line.strip() == "line con 0"), len(items) - 1)
        for route in reversed([r for r in routes if r.strip()]):
            items.insert(insert_at, route.strip())
        _write_rc(rc, items)

    notes = spec.get("notes") or {}
    if notes:
        for note in root.findall(".//PHYSICALWORKSPACE/NOTES/NOTE"):
            text_el = note.find("TEXT")
            if text_el is None:
                continue
            current = (text_el.text or "").strip()
            if current in notes:
                text_el.text = notes[current]

    return ET.tostring(root, encoding="utf-8")

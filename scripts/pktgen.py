"""根据蓝图（JSON）生成 Packet Tracer 5.x .pkt 实验文件。

用法：
    python pktgen.py blueprint.json -o out.pkt [--xml out.xml]

蓝图结构见 SKILL.md 或 examples/ 目录。
"""

import argparse
import copy
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkt_codec import encode

SKILL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TPL_DIR = os.path.join(SKILL_ROOT, "templates")

TYPE_ALIAS = {
    "router": "Router-PT",
    "switch": "2960-24TT",
    "pc": "PC-PT",
    "laptop": "Laptop-PT",
    "server": "Server-PT",
}

HOST_TYPES = ("Pc", "Laptop", "Server", "Printer", "WiredEndDevice")
L3_TYPES = ("Router", "MultiLayerSwitch")

CABLES = {
    "straight": "eStraightThrough",
    "cross": "eCrossOver",
    "rollover": "eRollOver",
    "fiber": "eFiber",
}

DEFAULT_LAYOUT_Y = {"Router": 150, "MultiLayerSwitch": 150, "Switch": 330}


def load_lib():
    with open(os.path.join(TPL_DIR, "manifest.json"), encoding="utf-8") as fh:
        manifest = json.load(fh)
    skeleton = ET.parse(os.path.join(TPL_DIR, manifest["skeleton"])).getroot()
    templates = {}
    for model, info in manifest["models"].items():
        templates[model] = ET.parse(os.path.join(TPL_DIR, info["file"])).getroot()
    return manifest, skeleton, templates


def child_nodes(node):
    found = node.findall("NODE") + node.findall("CHILDREN/NODE")
    return found


def find_rack(node):
    if (node.findtext("TYPE") or "").strip() == "4":
        return node
    for ch in child_nodes(node):
        got = find_rack(ch)
        if got is not None:
            return got
    return None


def find_type(node, type_id):
    if (node.findtext("TYPE") or "").strip() == type_id:
        return node
    for ch in child_nodes(node):
        got = find_type(ch, type_id)
        if got is not None:
            return got
    return None


def clear_device_nodes(node):
    children = node.find("CHILDREN")
    if children is None:
        return
    for child in list(children):
        if (child.findtext("TYPE") or "").strip() == "6":
            children.remove(child)
        else:
            clear_device_nodes(child)


def physical_prefix(phys, target):
    names = []

    def walk(node, chain):
        chain = chain + [(node.findtext("NAME") or "").strip()]
        if node is target:
            names.extend(chain)
            return True
        for ch in child_nodes(node):
            if walk(ch, chain):
                return True
        return False

    for top in child_nodes(phys):
        if walk(top, []):
            break
    return ",".join(names)


def prefix_len(mask):
    if not mask:
        return 24
    try:
        value = 0
        for part in mask.split("."):
            value = (value << 8) + int(part)
        return bin(value).count("1")
    except ValueError:
        return 24


def add_note(notes_el, x, y, text, index):
    if notes_el is None:
        return
    note = ET.SubElement(notes_el, "NOTE")
    ET.SubElement(note, "X").text = "%g" % x
    ET.SubElement(note, "Y").text = "%g" % y
    ET.SubElement(note, "Z").text = str(40000 + index)
    ET.SubElement(note, "TEXT", {"translate": "true"}).text = text
    ET.SubElement(note, "NOTECLUSTERID").text = "1-1"


def mac_for(dev_idx, port_idx):
    return "0060.%02X%02X.%04X" % (0x70 + dev_idx % 16, 0xED, 0x1000 + dev_idx * 64 + port_idx)


def link_local_from_mac(mac):
    digits = mac.replace(".", "")
    h = [digits[i:i + 2] for i in range(0, 12, 2)]
    return "FE80::%s%s:%sFF:FE%s:%s%s" % (h[0], h[1], h[2], h[3], h[4], h[5])


def default_position(device_type, index_in_type):
    y = DEFAULT_LAYOUT_Y.get(device_type, 500)
    return 200 + index_in_type * 150, y


def build_running_config(hostname, device_type, port_names, ifaces, extra_lines, vlans=None):
    lines = ["!", "version 12.2",
             "no service timestamps log datetime msec",
             "no service timestamps debug datetime msec",
             "no service password-encryption",
             "!", "hostname %s" % hostname]

    vlan_names = {}
    for v in (vlans or []):
        vlan_names[int(v["id"])] = v.get("name", "VLAN%04d" % int(v["id"]))
    for vid, vname in vlan_names.items():
        lines += ["!", "vlan %d" % vid]
        if vname:
            lines.append(" name %s" % vname)

    for pname in port_names:
        cfg = (ifaces or {}).get(pname, {}) or {}
        lines += ["!", "interface %s" % pname]
        if cfg.get("description"):
            lines.append(" description %s" % cfg["description"])
        ip, mask = cfg.get("ip"), cfg.get("mask")
        if ip:
            lines.append(" ip address %s %s" % (ip, mask or "255.255.255.0"))
        else:
            lines.append(" no ip address")
        if pname.lower().startswith("serial"):
            if cfg.get("clock"):
                lines.append(" clock rate %s" % cfg["clock"])
            elif cfg.get("dce"):
                lines.append(" clock rate 64000")
        elif ip:
            lines += [" duplex auto", " speed auto"]
        for extra in cfg.get("lines", []) or []:
            lines.append(" " + extra.strip())
        if cfg.get("shutdown"):
            lines.append(" shutdown")

    if extra_lines:
        lines += ["!"]
        lines += [l.rstrip() for l in extra_lines]

    if device_type in ("Switch", "MultiLayerSwitch"):
        lines += ["!", "!", "line con 0", "!", "line vty 0 4", " login",
                  "line vty 5 15", " login", "!", "!", "end"]
    else:
        lines += ["!", "ip classless", "!", "!", "!", "!", "!", "!", "!",
                  "line con 0", "!", "line aux 0", "!", "line vty 0 4", " login",
                  "!", "!", "!", "end"]
    return lines


def set_text(parent, tag, value):
    if parent is None:
        return
    el = parent.find(tag)
    if el is None:
        el = ET.SubElement(parent, tag)
    el.text = value


def build(blueprint):
    manifest, skeleton, templates = load_lib()
    root = copy.deepcopy(skeleton)

    devices_el = root.find("NETWORK/DEVICES")
    links_el = root.find("NETWORK/LINKS")
    for parent in (devices_el, links_el):
        for child in list(parent):
            parent.remove(child)

    phys = root.find("PHYSICALWORKSPACE")
    rack = find_rack(phys) if phys is not None else None
    table = find_type(phys, "5") if phys is not None else None
    if phys is not None:
        for top in phys.findall("NODE"):
            clear_device_nodes(top)
    rack_children = rack.find("CHILDREN") if rack is not None else None
    table_children = table.find("CHILDREN") if table is not None else None
    notes_el = phys.find("NOTES") if phys is not None else None
    if notes_el is not None:
        for child in list(notes_el):
            notes_el.remove(child)
    annotate = blueprint.get("annotate_ips", True)
    note_index = 0
    desc_el = root.find("NETWORK/DESCRIPTION")
    if desc_el is not None:
        desc_el.text = blueprint.get("description")
    rack_prefix = physical_prefix(phys, rack) if rack is not None else "城市间,城市家园,公司办公室,主要的工作橱,机柜"
    table_prefix = physical_prefix(phys, table) if table is not None else rack_prefix

    specs = blueprint.get("devices", [])
    if not specs:
        raise ValueError("蓝图里没有 devices")

    type_counters = {}
    resolved = []
    for spec in specs:
        model = spec.get("model") or TYPE_ALIAS.get((spec.get("type") or "").lower(), "")
        if model not in templates:
            raise ValueError("不支持的机型 %r，可用机型: %s" % (model, ", ".join(templates)))
        info = manifest["models"][model]
        dtype = info["type"]
        if "x" in spec and "y" in spec:
            x, y = spec["x"], spec["y"]
        else:
            n = type_counters.get(dtype, 0)
            type_counters[dtype] = n + 1
            x, y = default_position(dtype, n)
        resolved.append((spec, model, info, dtype, float(x), float(y)))

    addr_counter = [300000000]
    device_addrs = []
    node_counters = {}

    def next_addr():
        addr_counter[0] += 8
        return addr_counter[0]

    for di, (spec, model, info, dtype, x, y) in enumerate(resolved):
        dev = copy.deepcopy(templates[model])
        engine = dev.find("ENGINE")
        name = spec["name"]
        engine.find("NAME").text = name
        set_text(engine, "POWER", "true")
        if spec.get("description") and engine.find("DESCRIPTION") is not None:
            engine.find("DESCRIPTION").text = spec["description"]

        dev_mem = next_addr()
        dev_addr = next_addr()
        logical = dev.find("WORKSPACE/LOGICAL")
        set_text(logical, "X", ("%g" % x))
        set_text(logical, "Y", ("%g" % y))
        set_text(logical, "MEM_ADDR", str(dev_mem))
        set_text(logical, "DEV_ADDR", str(dev_addr))
        set_text(logical, "DEVCLUSTERID", "1-1")
        phys_el = dev.find("WORKSPACE/PHYSICAL")
        if phys_el is not None:
            use_table = dtype in HOST_TYPES and table_children is not None
            phys_el.text = (table_prefix if use_table else rack_prefix) + "," + name

        ports = engine.findall(".//PORT")
        port_addrs = []
        for pi, port in enumerate(ports):
            port_addrs.append(next_addr())
            mac = mac_for(di, pi)
            set_text(port, "MACADDRESS", mac)
            set_text(port, "BIA", mac)
            set_text(port, "IPV6_DEFAULT_LINK_LOCAL", link_local_from_mac(mac))
            set_text(port, "POWER", "false" if spec.get("power") is False else "true")

        ifaces = spec.get("interfaces") or {}
        unknown = [p for p in ifaces if p not in info["ports"]]
        if unknown:
            raise ValueError("%s(%s) 没有端口 %s，可用端口: %s"
                             % (name, model, unknown, ", ".join(info["ports"])))

        for pname, cfg in ifaces.items():
            port = ports[info["ports"].index(pname)]
            set_text(port, "IP", cfg.get("ip"))
            set_text(port, "SUBNET", cfg.get("mask"))
            if cfg.get("shutdown"):
                set_text(port, "POWER", "false")
            if cfg.get("description") and port.find("DESCRIPTION") is not None:
                port.find("DESCRIPTION").text = cfg["description"]

        # 主机类设备（PC/笔记本/服务器）的 IP、网关、DNS
        pc = spec.get("pc") or {}
        if dtype in HOST_TYPES and pc:
            if pc.get("ip"):
                port = ports[0]
                set_text(port, "IP", pc.get("ip"))
                set_text(port, "SUBNET", pc.get("mask") or "255.255.255.0")
                set_text(engine, "GATEWAY", pc.get("gateway"))
            if pc.get("dns"):
                dns = engine.find("DNS_CLIENT")
                if dns is not None:
                    set_text(dns, "SERVER_IP", pc["dns"])

        if dtype not in HOST_TYPES:
            vl = engine.find("VLANS")
            if vl is not None:
                keep = [v for v in vl.findall("VLAN") if v.get("number") in ("1", "1002", "1003", "1004", "1005")]
                for v in list(vl):
                    vl.remove(v)
                for v in keep:
                    vl.append(v)
                for v in spec.get("vlans", []) or []:
                    ET.SubElement(vl, "VLAN", {"number": str(int(v["id"]))})
                    vl[-1].set("name", v.get("name", "VLAN%04d" % int(v["id"])))
            hostname = spec.get("hostname") or name
            rc_lines = build_running_config(hostname, dtype, info["ports"],
                                            ifaces, spec.get("config"), spec.get("vlans"))
            rc = engine.find("RUNNINGCONFIG")
            if rc is not None:
                for child in list(rc):
                    rc.remove(child)
                for line in rc_lines:
                    ET.SubElement(rc, "LINE").text = line
            st = engine.find("STARTUPCONFIG")
            if st is not None:
                for child in list(st):
                    st.remove(child)
            bi = engine.find("BUILD_IN_ADDR")
            if bi is not None:
                bi.text = "0001.%04X.%04X" % (0x1000 + di, addr_counter[0] & 0xFFFF)

        if annotate and notes_el is not None:
            note_y = y + 55 if dtype in HOST_TYPES else y - 40
            for pname, cfg in ifaces.items():
                if cfg.get("ip"):
                    add_note(notes_el, x, note_y, "%s/%d" % (cfg["ip"], prefix_len(cfg.get("mask"))), note_index)
                    note_index += 1
                    note_y += 22
            if dtype in HOST_TYPES and pc.get("ip"):
                add_note(notes_el, x, y + 55, "%s/%d" % (pc["ip"], prefix_len(pc.get("mask"))), note_index)
                note_index += 1

        devices_el.append(dev)
        device_addrs.append((dev_mem, dev_addr, port_addrs, info["ports"], dtype, name))

        if rack_children is not None or table_children is not None:
            container = table_children if (dtype in HOST_TYPES and table_children is not None) else rack_children
            slot = node_counters.get(id(container), 0)
            node_counters[id(container)] = slot + 1
            node = ET.Element("NODE")
            X = ET.SubElement(node, "X")
            X.text = str(slot * 8)
            ET.SubElement(node, "Y").text = "0"
            ET.SubElement(node, "TYPE").text = "6"
            nm = ET.SubElement(node, "NAME", {"translate": "true"})
            nm.text = name
            ET.SubElement(node, "SX").text = "1"
            ET.SubElement(node, "SY").text = "1"
            ET.SubElement(node, "W").text = "0"
            ET.SubElement(node, "H").text = "0"
            ET.SubElement(node, "PATH").text = "../art/Background/grid_100x100.png"
            ET.SubElement(node, "CHILDREN")
            ET.SubElement(node, "MANUAL_SCALING").text = "false"
            ET.SubElement(node, "SCALED_PIXMAP_WIDTH").text = "0"
            ET.SubElement(node, "SCALED_PIXMAP_HEIGHT").text = "0"
            ET.SubElement(node, "INIT_WIDTH").text = "0"
            ET.SubElement(node, "INIT_HEIGHT").text = "0"
            ET.SubElement(node, "INIT_SX").text = "1"
            ET.SubElement(node, "INIT_SY").text = "0"
            ET.SubElement(node, "BG_TILED").text = "false"
            ET.SubElement(node, "CUSTOM_IMAGE_WIDTH").text = "-1"
            ET.SubElement(node, "CUSTOM_IMAGE_HEIGHT").text = "-1"
            container.append(node)

    name_to_index = {}
    for idx, (spec, *_rest) in enumerate(resolved):
        name_to_index[spec["name"]] = idx
        if spec.get("id"):
            name_to_index[spec["id"]] = idx

    for link in blueprint.get("links", []):
        a_dev, a_port = _split_endpoint(link["from"], name_to_index, resolved, device_addrs)
        b_dev, b_port = _split_endpoint(link["to"], name_to_index, resolved, device_addrs)
        cable = (link.get("cable") or "auto").lower()
        serial = a_port.lower().startswith("serial") or b_port.lower().startswith("serial")
        if cable == "auto":
            cable = "serial" if serial else (
                "cross" if device_addrs[a_dev][4] in HOST_TYPES and device_addrs[b_dev][4] in HOST_TYPES
                else "straight")
        link_el = ET.SubElement(links_el, "LINK")
        ET.SubElement(link_el, "TYPE").text = "eSerial" if cable == "serial" else "eCopper"
        cab = ET.SubElement(link_el, "CABLE")
        ET.SubElement(cab, "LENGTH").text = str(link.get("length", 0))
        ET.SubElement(cab, "FROM").text = str(a_dev)
        ET.SubElement(cab, "PORT").text = a_port
        ET.SubElement(cab, "TO").text = str(b_dev)
        ET.SubElement(cab, "PORT").text = b_port
        ET.SubElement(cab, "FROM_DEVICE_MEM_ADDR").text = str(device_addrs[a_dev][0])
        ET.SubElement(cab, "TO_DEVICE_MEM_ADDR").text = str(device_addrs[b_dev][0])
        a_pi = device_addrs[a_dev][3].index(a_port)
        b_pi = device_addrs[b_dev][3].index(b_port)
        ET.SubElement(cab, "FROM_PORT_MEM_ADDR").text = str(device_addrs[a_dev][2][a_pi])
        ET.SubElement(cab, "TO_PORT_MEM_ADDR").text = str(device_addrs[b_dev][2][b_pi])
        ET.SubElement(cab, "TYPE").text = CABLES.get(cable, "eStraightThrough")
        if cable == "serial":
            dce = link.get("dce", "from").lower()
            if dce == "to":
                ET.SubElement(cab, "DCEDEV").text = str(b_dev)
                ET.SubElement(cab, "DCEPORT").text = b_port
            else:
                ET.SubElement(cab, "DCEDEV").text = str(a_dev)
                ET.SubElement(cab, "DCEPORT").text = a_port

    return root


def _split_endpoint(text, name_to_index, resolved, device_addrs):
    if ":" in text:
        dev_name, port = text.split(":", 1)
    else:
        dev_name, port = text, None
    if dev_name not in name_to_index:
        raise ValueError("连线引用了未知设备 %r" % dev_name)
    idx = name_to_index[dev_name]
    ports = device_addrs[idx][3]
    if port is None:
        port = ports[0]
    if port not in ports:
        raise ValueError("%s 没有端口 %s，可用端口: %s" % (dev_name, port, ", ".join(ports)))
    return idx, port


def main():
    ap = argparse.ArgumentParser(description="根据蓝图生成 Packet Tracer 5.x .pkt")
    ap.add_argument("blueprint", help="蓝图 JSON 文件")
    ap.add_argument("-o", "--output", required=True, help="输出 .pkt 路径")
    ap.add_argument("--xml", help="同时输出解包后的 XML（调试用）")
    args = ap.parse_args()

    with open(args.blueprint, encoding="utf-8") as fh:
        blueprint = json.load(fh)
    root = build(blueprint)

    body = ET.tostring(root, encoding="utf-8")
    xml = b'<?xml version="1.0" ?>\n' + body
    with open(args.output, "wb") as fh:
        fh.write(encode(xml))
    if args.xml:
        with open(args.xml, "wb") as fh:
            fh.write(xml)
    print("已生成 %s（%d 台设备，%d 条连线）"
          % (args.output, len(blueprint.get("devices", [])), len(blueprint.get("links", []))))


if __name__ == "__main__":
    main()

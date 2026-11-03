"""从已有的 PT 5.x .pkt 中提取设备模板和文档骨架。

用法：
    python harvest.py --donor 实验.pkt 另一个.pkt --out templates

产物（写入 --out 目录）：
    skeleton.xml     去掉所有设备/连线/画图后的文档骨架
    <model>.xml      每种设备的完整 <DEVICE> 子树（已清空 IP/配置/坐标）
    manifest.json    机型 -> {type, ports(端口名,按 XML 里 PORT 出现顺序), file}
"""

import argparse
import copy
import json
import os
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pkt_codec import decode

# 没有 RUNNINGCONFIG 的设备（PC/服务器/AP）的端口默认名
DEFAULT_PORTS = {
    "Pc": ["FastEthernet"],
    "Laptop": ["FastEthernet"],
    "Server": ["FastEthernet"],
    "Printer": ["FastEthernet"],
    "WiredEndDevice": ["FastEthernet"],
    "AccessPoint": ["Ethernet", "Wireless"],
    "Pda": ["Wireless"],
}

ART_PATH = "../art/Background/"


def port_names_from_config(engine):
    nports = len(engine.findall(".//PORT"))
    rc = engine.find("RUNNINGCONFIG")
    if rc is None:
        return None, nports
    names = []
    for line in rc.findall("LINE"):
        text = (line.text or "").strip()
        if text.startswith("interface "):
            nm = text[len("interface "):].strip()
            if nm.lower().startswith("vlan"):
                continue
            names.append(nm)
    return names, len(engine.findall(".//PORT"))


def sanitize(device, port_names):
    dev = copy.deepcopy(device)
    engine = dev.find("ENGINE")
    engine.find("NAME").text = "设备"
    if engine.find("POWER") is not None:
        engine.find("POWER").text = "true"

    for i, port in enumerate(engine.findall(".//PORT")):
        for tag in ("IP", "SUBNET", "PORT_GATEWAY", "PORT_DNS"):
            el = port.find(tag)
            if el is not None:
                el.text = None
        pwr = port.find("POWER")
        if pwr is not None:
            pwr.text = "true"
        extra_name = port.find("NAME")
        if extra_name is not None:
            extra_name.text = port_names[i] if i < len(port_names) else ""

    for tag, subs in (("GATEWAY", ()), ("DNS_CLIENT", ("SERVER_IP", "SERVER_IPV6"))):
        el = engine.find(tag)
        if el is not None:
            if not subs:
                el.text = None
            for s in subs:
                if el.find(s) is not None:
                    el.find(s).text = None

    for tag in ("RUNNINGCONFIG", "STARTUPCONFIG"):
        el = engine.find(tag)
        if el is not None:
            for child in list(el):
                el.remove(child)

    ws = dev.find("WORKSPACE")
    if ws is not None:
        logical = ws.find("LOGICAL")
        if logical is not None:
            for tag in ("X", "Y"):
                if logical.find(tag) is not None:
                    logical.find(tag).text = "0"
            for tag in ("MEM_ADDR", "DEV_ADDR"):
                if logical.find(tag) is not None:
                    logical.find(tag).text = "0"
            if logical.find("DEVCLUSTERID") is not None:
                logical.find("DEVCLUSTERID").text = "1-1"
        if ws.find("PHYSICAL") is not None:
            ws.find("PHYSICAL").text = None
    return dev


def find_rack(node):
    if (node.findtext("TYPE") or "").strip() == "4":
        return node
    for ch in node.findall("NODE") + node.findall("CHILDREN/NODE"):
        found = find_rack(ch)
        if found is not None:
            return found
    return None


def clear_container(parent):
    if parent is not None:
        for child in list(parent):
            parent.remove(child)


def clear_device_nodes(node):
    """删除物理视图里所有 TYPE=6（设备）节点，保留机柜/桌子等容器。"""
    for children in (node.find("CHILDREN"), node):
        if children is None:
            continue
        for child in list(children.findall("NODE")):
            if (child.findtext("TYPE") or "").strip() == "6":
                children.remove(child)
            else:
                clear_device_nodes(child)


def build_skeleton(root):
    sk = copy.deepcopy(root)
    network = sk.find("NETWORK")
    clear_container(network.find("DEVICES"))
    clear_container(network.find("LINKS"))
    for tag in ("RECTANGLES", "ELLIPSES", "LINES", "POLYGONS"):
        clear_container(sk.find(tag))
    phys = sk.find("PHYSICALWORKSPACE")
    if phys is not None:
        for top in phys.findall("NODE"):
            clear_device_nodes(top)
        clear_container(phys.find("NOTES"))
        rack = find_rack(phys)
        for node in phys.iter("PATH"):
            low = (node.text or "").lower()
            if "ggeoviewintercity" in low:
                node.text = ART_PATH + "gGeoViewInterCity.png"
            elif "ggeoviewcity" in low:
                node.text = ART_PATH + "gGeoViewCity.png"
            elif "ggeoviewbuilding" in low:
                node.text = ART_PATH + "gGeoViewBuilding.png"
            else:
                node.text = ART_PATH + "grid_100x100.png"
    return sk


def harvest(donors, out_dir):
    os.makedirs(out_dir, exist_ok=True)
    models = {}
    skeleton_root = None

    for path in donors:
        raw = open(path, "rb").read()
        xml = decode(raw)
        root = ET.fromstring(xml)
        if skeleton_root is None:
            skeleton_root = root
        for dev in root.findall(".//DEVICE"):
            engine = dev.find("ENGINE")
            ty = engine.find("TYPE")
            model = ty.get("model")
            if model in models:
                continue
            names, nports = port_names_from_config(engine)
            if nports == 0:
                print(f"跳过 {model}: 无端口（空平台机型）")
                continue
            if not names or len(names) != nports:
                names = DEFAULT_PORTS.get(ty.text, names or [])
            if len(names) != nports:
                print(f"跳过 {model}: 端口名无法确定 ({len(names)}/{nports})")
                continue
            clean = sanitize(dev, names)
            ET.ElementTree(clean).write(
                os.path.join(out_dir, f"{model}.xml"), encoding="utf-8", xml_declaration=True
            )
            models[model] = {"type": ty.text, "ports": names, "file": f"{model}.xml"}
            print(f"提取模板 {model} ({ty.text}) 端口 {len(names)} 个")

    ET.ElementTree(build_skeleton(skeleton_root)).write(
        os.path.join(out_dir, "skeleton.xml"), encoding="utf-8", xml_declaration=True
    )
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump({"models": models, "skeleton": "skeleton.xml"}, fh, ensure_ascii=False, indent=2)
    print(f"骨架 + {len(models)} 个模板已写入 {out_dir}")
    return models


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--donor", nargs="+", required=True, help="PT 5.x .pkt 文件")
    ap.add_argument("--out", required=True, help="模板输出目录")
    args = ap.parse_args()
    harvest(args.donor, args.out)


if __name__ == "__main__":
    main()

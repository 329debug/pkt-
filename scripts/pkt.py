"""Packet Tracer 5.x .pkt 统一命令行入口。

用法示例：
    python pkt.py generate blueprint.json -o lab.pkt [--xml lab.xml]
    python pkt.py decode lab.pkt [-o lab.xml]
    python pkt.py inspect lab.pkt
    python pkt.py patch lab.pkt -o lab2.pkt --spec patch.json
    python pkt.py check lab.pkt
    python pkt.py harvest --donor a.pkt b.pkt --out templates
"""

import argparse
import json
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import check as checker
import harvest as harvester
import patch as patcher
import pktgen
from pkt_codec import decode, encode, version_of

DECL = b'<?xml version="1.0" ?>\n'


def cmd_generate(args):
    with open(args.blueprint, encoding="utf-8") as fh:
        blueprint = json.load(fh)
    root = pktgen.build(blueprint)
    xml = DECL + ET.tostring(root, encoding="utf-8")
    with open(args.output, "wb") as fh:
        fh.write(encode(xml))
    if args.xml:
        with open(args.xml, "wb") as fh:
            fh.write(xml)
    print("已生成 %s（%d 台设备，%d 条连线）"
          % (args.output, len(blueprint.get("devices", [])), len(blueprint.get("links", []))))


def cmd_decode(args):
    xml = decode(open(args.input, "rb").read())
    out = args.output or os.path.splitext(args.input)[0] + ".xml"
    with open(out, "wb") as fh:
        fh.write(xml)
    print("已解出 %s（版本 %s，%d 字节）" % (out, version_of(xml), len(xml)))


def cmd_inspect(args):
    ok, lines = checker.analyze(decode(open(args.input, "rb").read()), args.src, args.dst)
    for line in lines:
        print(line)
    return 0 if ok else 2


def cmd_check(args):
    ok, lines = checker.analyze(decode(open(args.input, "rb").read()), args.src, args.dst)
    for line in lines:
        if line.startswith("[通]") or line.startswith("[不通]") or line.startswith("[错误]"):
            print(line)
    print("结论: %s" % ("主机间三层可达" if ok else "存在不可达路径"))
    return 0 if ok else 2


def cmd_patch(args):
    with open(args.spec, encoding="utf-8") as fh:
        spec = json.load(fh)
    xml = patcher.apply_patch(decode(open(args.input, "rb").read()), spec)
    with open(args.output, "wb") as fh:
        fh.write(encode(DECL + xml))
    print("已修改 %s -> %s（版本 %s）" % (args.input, args.output, version_of(xml)))


def cmd_harvest(args):
    harvester.harvest(args.donor, args.out)


def main():
    parser = argparse.ArgumentParser(description="Packet Tracer 5.x .pkt 生成 / 修改 / 检查工具")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("generate", help="按蓝图生成新 .pkt")
    p.add_argument("blueprint")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--xml", help="同时导出解包后的 XML")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("decode", help=".pkt 解包为 XML")
    p.add_argument("input")
    p.add_argument("-o", "--output")
    p.set_defaults(func=cmd_decode)

    p = sub.add_parser("inspect", help="打印设备/地址/路由与可达性")
    p.add_argument("input")
    p.add_argument("--from", dest="src", help="只检查指定源主机")
    p.add_argument("--to", dest="dst", help="只检查指定目的主机")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("check", help="只做三层可达性检查")
    p.add_argument("input")
    p.add_argument("--from", dest="src", help="只检查指定源主机")
    p.add_argument("--to", dest="dst", help="只检查指定目的主机")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("patch", help="定点修改已有 .pkt（保留原拓扑）")
    p.add_argument("input")
    p.add_argument("-o", "--output", required=True)
    p.add_argument("--spec", required=True, help="补丁 JSON")
    p.set_defaults(func=cmd_patch)

    p = sub.add_parser("harvest", help="从 .pkt 提取机型模板与骨架")
    p.add_argument("--donor", nargs="+", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(func=cmd_harvest)

    args = parser.parse_args()
    code = args.func(args)
    sys.exit(code or 0)


if __name__ == "__main__":
    main()

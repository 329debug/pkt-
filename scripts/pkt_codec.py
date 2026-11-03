"""Packet Tracer 5.x .pkt 编解码（XOR + qCompress 封装）。

文件结构：
    明文字节 = qCompress(XML) = 4 字节大端未压缩长度 + zlib 流
    磁盘字节 = 明文[i] ^ ((len - i) & 0xFF)      # len 为磁盘文件长度

该格式由 PT 5.2/5.3 使用（<VERSION>5.x</VERSION>）。
PT 6.2+ 使用 Twofish/EAX，本模块不支持，遇到会抛 ValueError。
"""

import struct
import zlib

MAGIC_ROOT = b"<PACKETTRACER5"


def decode(raw: bytes) -> bytes:
    """把 .pkt 字节解成 XML 文本（bytes）。"""
    n = len(raw)
    plain = bytes(raw[i] ^ ((n - i) & 0xFF) for i in range(n))
    if len(plain) < 8:
        raise ValueError("文件过短，不是 Packet Tracer 5.x 文件")
    size = struct.unpack(">I", plain[:4])[0]
    try:
        xml = zlib.decompress(plain[4:])
    except zlib.error as exc:
        raise ValueError(
            "解压失败：该文件可能不是 PT 5.x 格式（6.2+ 使用 Twofish/EAX，本工具不支持）"
        ) from exc
    xml = xml[:size]
    head = xml.lstrip()
    if head.startswith(b"<?xml"):
        end = head.find(b"?>")
        head = head[end + 2:].lstrip() if end >= 0 else head
    if not head.startswith(MAGIC_ROOT):
        raise ValueError("解出的内容不是 <PACKETTRACER5> 根节点")
    return xml


def encode(xml: bytes) -> bytes:
    """把 XML 文本打包成 PT 5.x .pkt 字节。"""
    if isinstance(xml, str):
        xml = xml.encode("utf-8")
    xml = xml.lstrip(b"\xef\xbb\xbf")
    xml = xml.replace(b"\r\n", b"\n")
    payload = struct.pack(">I", len(xml)) + zlib.compress(xml)
    n = len(payload)
    return bytes(payload[i] ^ ((n - i) & 0xFF) for i in range(n))


def version_of(xml: bytes) -> str:
    start = xml.find(b"<VERSION>")
    if start < 0:
        return ""
    end = xml.find(b"</VERSION>", start)
    return xml[start + 9:end].decode("utf-8", "replace").strip()

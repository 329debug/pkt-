"""不打开 Packet Tracer，静态检查 .pkt 中的地址配置与三层可达性。

做法：解析各路由器的接口与 ip route，从每台主机按默认网关出发逐跳做最长前缀匹配，
验证下一跳是否落在某台路由器直连接口上，直到目的主机所在网段。
只做三层逻辑检查，不模拟交换机二层/生成树，也不验证 ARP。
"""

import ipaddress
import xml.etree.ElementTree as ET

ROUTER_TYPES = ("Router", "MultiLayerSwitch")
HOST_TYPES = ("Pc", "Laptop", "Server", "Printer", "WiredEndDevice")


def _network(ip, mask):
    if not ip or not mask:
        return None
    try:
        return ipaddress.IPv4Network("%s/%s" % (ip, mask), strict=False)
    except ValueError:
        return None


def _parse_routers(root):
    routers = {}
    for dev in root.findall(".//DEVICE"):
        engine = dev.find("ENGINE")
        name = engine.findtext("NAME")
        typ = (engine.find("./TYPE").text or "").strip()
        if typ not in ROUTER_TYPES:
            continue
        rc = engine.find("RUNNINGCONFIG")
        ifaces, routes = [], []
        current = None
        for line in (rc.findall("LINE") if rc is not None else []):
            text = (line.text or "").strip()
            if text.startswith("interface "):
                current = {"name": text[len("interface "):].strip(), "ip": None,
                           "mask": None, "shutdown": False}
                ifaces.append(current)
            elif text.startswith("ip address ") and current is not None:
                parts = text.split()
                current["ip"] = parts[2]
                current["mask"] = parts[3] if len(parts) > 3 else None
            elif text == "no ip address" and current is not None:
                current["ip"] = None
            elif text == "shutdown" and current is not None:
                current["shutdown"] = True
            if text.startswith("ip route "):
                parts = text.split()
                if len(parts) >= 5:
                    routes.append({"dest": parts[2], "mask": parts[3], "next": parts[4]})
        routers[name] = {"name": name, "ifaces": ifaces, "routes": routes}
    return routers


def _parse_hosts(root):
    hosts = {}
    for dev in root.findall(".//DEVICE"):
        engine = dev.find("ENGINE")
        name = engine.findtext("NAME")
        typ = (engine.find("./TYPE").text or "").strip()
        if typ not in HOST_TYPES:
            continue
        ports = engine.findall(".//PORT")
        if not ports:
            continue
        ip = ports[0].findtext("IP")
        mask = ports[0].findtext("SUBNET")
        hosts[name] = {"name": name, "ip": ip, "mask": mask,
                       "gateway": engine.findtext("GATEWAY")}
    return hosts


def _router_with_ip(routers, ip):
    for router in routers.values():
        for iface in router["ifaces"]:
            if iface["ip"] == ip and not iface["shutdown"]:
                return router
    return None


def _iface_network(iface):
    return _network(iface["ip"], iface["mask"])


def _best_match(router, dest_ip):
    candidates = []
    for iface in router["ifaces"]:
        net = _iface_network(iface)
        if net is not None and not iface["shutdown"]:
            candidates.append({"prefix": net.prefixlen, "kind": "connected",
                               "next": None, "net": net})
    for route in router["routes"]:
        net = _network(route["dest"], route["mask"])
        if net is not None:
            candidates.append({"prefix": net.prefixlen, "kind": "static",
                               "next": route["next"], "net": net})
    hits = [c for c in candidates if ipaddress.IPv4Address(dest_ip) in c["net"]]
    if not hits:
        return None
    return max(hits, key=lambda c: c["prefix"])


def trace(hosts, routers, src_name, dst_ip):
    src = hosts[src_name]
    try:
        dst = ipaddress.IPv4Address(dst_ip)
    except ValueError:
        return False, ["目的地址非法"]
    if not src["ip"]:
        return False, ["源主机未配置 IP"]
    src_net = _network(src["ip"], src["mask"])
    if src_net is None:
        return False, ["源主机掩码缺失"]
    if dst in src_net:
        return True, ["同网段（未验证交换机二层）"]
    if not src["gateway"]:
        return False, ["源主机没有默认网关"]
    if ipaddress.IPv4Address(src["gateway"]) not in src_net:
        return False, ["默认网关不在本网段"]
    router = _router_with_ip(routers, src["gateway"])
    if router is None:
        return False, ["找不到网关所在路由器：%s" % src["gateway"]]

    path = [router["name"]]
    for _ in range(25):
        for iface in router["ifaces"]:
            net = _iface_network(iface)
            if net is not None and not iface["shutdown"] and dst in net:
                return True, [" -> ".join(path) + " -> " + str(dst) + "（目的网段直连）"]
        match = _best_match(router, str(dst))
        if match is None:
            return False, [" -> ".join(path) + " 上无匹配路由（%s）" % dst]
        if match["kind"] == "connected":
            return False, [" -> ".join(path) + " 命中直连但目的不在该网段"]
        next_ip = match["next"]
        reachable = any(
            (_iface_network(i) is not None and ipaddress.IPv4Address(next_ip) in _iface_network(i))
            for i in router["ifaces"] if not i["shutdown"]
        )
        if not reachable:
            return False, [" -> ".join(path) + " 的下一跳 %s 不在任何直连网段" % next_ip]
        nxt = _router_with_ip(routers, next_ip)
        if nxt is None or nxt["name"] == router["name"]:
            return False, [" -> ".join(path) + " 的下一跳 %s 无法到达" % next_ip]
        router = nxt
        path.append(router["name"])
    return False, ["超过 25 跳，疑似路由环路"]


def analyze(xml_bytes, src=None, dst=None):
    root = ET.fromstring(xml_bytes)
    hosts = _parse_hosts(root)
    routers = _parse_routers(root)
    lines = []
    lines.append("版本: %s" % (root.findtext("VERSION") or "?"))
    lines.append("设备: %d 台（主机 %d，路由器 %d）"
                 % (len(root.findall(".//DEVICE")), len(hosts), len(routers)))

    for host in hosts.values():
        if host["ip"]:
            lines.append("主机 %s: %s/%s 网关 %s"
                         % (host["name"], host["ip"], host["mask"], host["gateway"] or "-"))
        else:
            lines.append("主机 %s: 未配置 IP" % host["name"])

    for router in routers.values():
        ips = ["%s:%s/%s" % (i["name"], i["ip"], i["mask"])
               for i in router["ifaces"] if i["ip"] and not i["shutdown"]]
        lines.append("路由 %s 接口: %s" % (router["name"], ", ".join(ips) or "-"))
        for r in router["routes"]:
            lines.append("    ip route %s %s %s" % (r["dest"], r["mask"], r["next"]))

    ok_all = True
    configured = [h for h in hosts.values() if h["ip"]]
    sources = [h for h in configured if src is None or h["name"] == src]
    targets = [h for h in configured if dst is None or h["name"] == dst]
    if src is not None and not sources:
        return False, lines + ["[错误] 找不到已配置 IP 的主机 %r" % src]
    if dst is not None and not targets:
        return False, lines + ["[错误] 找不到已配置 IP 的主机 %r" % dst]
    for s in sources:
        for d in targets:
            if s["name"] == d["name"]:
                continue
            ok, path = trace(hosts, routers, s["name"], d["ip"])
            ok_all = ok_all and ok
            lines.append("%s %s -> %s: %s"
                         % ("[通]" if ok else "[不通]", s["name"], d["name"], path[0]))
    return ok_all, lines

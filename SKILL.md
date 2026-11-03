---
name: pkt-generator
description: 生成或直接修改 Cisco Packet Tracer 5.x（5.2/5.3，如 PacketTracer5.exe）的 .pkt 实验文件。当用户要求"生成 pkt""做实验拓扑""按指定布局生成网络实验""给路由器配静态路由/默认路由""往已有 pkt 里加配置""检查实验拓扑能不能通"时使用。纯文件操作，不依赖 Packet Tracer 界面。
---

# Packet Tracer .pkt 生成 / 修改 / 检查（PT 5.x）

把 `.pkt` 当成可读写的 XML 容器处理：解码 → 改 XML → 重新编码。全程纯 Python 标准库，
不需要打开 Packet Tracer，也不需要任何第三方依赖（Python 3.8+）。

两种工作方式：

| 方式 | 适用场景 | 特点 |
|---|---|---|
| `generate` | 从零做拓扑 | 设备/坐标/连线/配置全由蓝图 JSON 定义 |
| `patch` | 修改老师给的 .pkt | 完整保留原拓扑、连线、端口映射，只改地址/路由/备注 |

## 快速开始

```powershell
# 生成
python scripts/pkt.py generate examples/实验五_静态路由.json -o out.pkt --xml out.xml

# 修改已有文件（保留原拓扑）
python scripts/pkt.py patch 静态路由.pkt -o 静态路由_已配置.pkt --spec examples/patch_示例.json

# 解码查看
python scripts/pkt.py decode lab.pkt -o lab.xml

# 检查主机间三层是否互通
python scripts/pkt.py check lab.pkt --from 主机0 --to 主机2
```

所有命令均输出机器可读的简短结果，退出码 `0` 成功 / `1` 错误 / `2` 检查不通过，便于其他 agent 调用。

## 适用版本

- ✅ Packet Tracer 5.2 / 5.3（`<VERSION>5.x.x.xxxx</VERSION>`）
- ❌ 6.2 及以上（Twofish/EAX 加密），`decode` 会明确报错

## 蓝图（generate）

```json
{
  "name": "实验名称",
  "description": "画布顶部说明（可选）",
  "annotate_ips": true,
  "devices": [
    {
      "name": "路由器1", "model": "Router-PT", "hostname": "Router1",
      "x": 377, "y": 270,
      "interfaces": {
        "FastEthernet0/0": {"ip": "172.16.2.254", "mask": "255.255.255.0"},
        "FastEthernet1/0": {"ip": "172.16.1.5", "mask": "255.255.255.252"},
        "Serial3/0": {"shutdown": true}
      },
      "config": [
        "ip route 172.16.1.0 255.255.255.252 172.16.1.6",
        "ip route 172.16.3.0 255.255.255.0 172.16.1.6"
      ]
    },
    {"name": "主机0", "model": "PC-PT", "x": 283, "y": 501,
     "pc": {"ip": "172.16.2.10", "mask": "255.255.255.0", "gateway": "172.16.2.254"}}
  ],
  "links": [
    {"from": "主机0:FastEthernet", "to": "交换机0:FastEthernet0/1"},
    {"from": "路由器1:FastEthernet1/0", "to": "路由器2:FastEthernet0/0", "cable": "cross"}
  ]
}
```

| 字段 | 说明 |
|---|---|
| `devices[].model` | 机型，见下表；也可用 `type`: router/switch/pc/laptop/server |
| `devices[].x/y` | 逻辑画布坐标；省略时按类型自动分层排布 |
| `devices[].interfaces` | 端口名 → `ip`/`mask`/`shutdown`/`description`/`lines`（接口下附加命令） |
| `devices[].config` | 全局 CLI 命令数组，原样写入 running-config（静态路由、默认路由、OSPF、NAT…） |
| `devices[].pc` | 主机地址：`ip`/`mask`/`gateway`/`dns` |
| `devices[].vlans` | 交换机 VLAN：`[{"id":10,"name":"Finance"}]` |
| `annotate_ips` | 默认 `true`，在画布上标注每个接口的 `IP/前缀` |
| `links[].cable` | `auto`(默认)/`straight`/`cross`/`serial`/`rollover`/`fiber` |

## 补丁（patch）

只写需要改的部分，未提到的内容一律保持原样：

```json
{
  "hosts": {
    "主机0": {"ip": "172.16.2.10", "mask": "255.255.255.0", "gateway": "172.16.2.254", "dns": "8.8.8.8"}
  },
  "interfaces": {
    "路由器1": {
      "FastEthernet0/0": {"ip": "172.16.2.254", "mask": "255.255.255.0", "shutdown": false}
    }
  },
  "routes": {
    "路由器1": ["ip route 0.0.0.0 0.0.0.0 172.16.1.6"]
  },
  "notes": {"172.16.2.1/24": "172.16.2.10/24"}
}
```

规则：

- `routes` 会**先删除该设备原有的 `ip route` 行**再写入新路由，不会叠加；
- `interfaces` 同时更新接口对象和 running-config 里的 `interface` 段；
- `notes` 用于同步画布上的地址标注文字；
- 输出必须用 `-o` 指定，不会静默覆盖原文件。

## 检查（check / inspect）

`check` 不启动 Packet Tracer，按路由表做三层逐跳模拟：从源主机查默认网关 → 每台路由器做最长前缀匹配
→ 校验下一跳是否落在直连接口 → 直至目的主机网段。

- 输出形如 `[通] 主机0 -> 主机2: 路由器1 -> 路由器2 -> 路由器3 -> 172.16.3.10`
- 仅验证三层路由逻辑，**不验证交换机二层/生成树/ARP**；
- `--from` / `--to` 可只测一对主机；`inspect` 额外打印接口与路由明细。

## 支持的机型与端口名

| 类型 | 机型 |
|---|---|
| Router | `Router-PT`、`2811`、`1841`、`2620XM`、`2621XM` |
| Switch | `2960-24TT`、`2950-24`、`2950T-24`、`Switch-PT` |
| MultiLayerSwitch | `3560-24PS` |
| 主机 | `PC-PT`、`Laptop-PT`、`Server-PT`、`Printer-PT`、`WiredEndDevice-PT` |
| AccessPoint | `AccessPoint-PT`、`AccessPoint-PT-A`、`AccessPoint-PT-N` |

各机型端口名见 `templates/manifest.json`，例如：

- `Router-PT`：FastEthernet0/0, FastEthernet1/0, Serial2/0, Serial3/0, FastEthernet4/0, FastEthernet5/0
- `1841` / `2621XM`：FastEthernet0/0, FastEthernet0/1
- `2620XM`：FastEthernet0/0
- `2960-24TT` / `2950T-24`：FastEthernet0/1…0/24, GigabitEthernet1/1, 1/2
- `2950-24`：FastEthernet0/1…0/24
- `Switch-PT`：FastEthernet0/1, 1/1, 2/1, 3/1, 4/1, 5/1
- `3560-24PS`：FastEthernet0/1…0/24, GigabitEthernet0/1, 0/2
- 主机统一为 `FastEthernet`；AP 为 `Ethernet`、`Wireless`

`templates/` 存放机型模板，`skeleton.xml` 是空白文档骨架。
新增机型：`python scripts/pkt.py harvest --donor 任意5.x.pkt --out templates`（会跳过无法确定端口名的机型）。

暂不支持（PT 不保存端口名，需要逐个手工标定）：`Hub-PT`、`Bridge-PT`、`Repeater-PT`、`Cloud-PT`、`DSL/Cable-Modem-PT`、`7960` IP 电话、`Linksys-WRT300N`、`PDA/Tablet/WirelessEndDevice`、`TV-PT`、`CoAxialSplitter-PT`、`*-Empty` 空平台。

## 文件结构

```
pkt-generator/
├── SKILL.md
├── scripts/
│   ├── pkt.py         # 统一 CLI：generate/decode/inspect/check/patch/harvest
│   ├── pkt_codec.py   # 5.x .pkt <-> XML
│   ├── pktgen.py      # 蓝图 -> 新 .pkt
│   ├── patch.py       # 定点修改已有 .pkt
│   ├── check.py       # 三层可达性静态检查
│   └── harvest.py     # 抽取机型模板/骨架
├── templates/         # 9 种机型模板 + skeleton + manifest
└── examples/          # 蓝图与补丁示例
```

## 限制

- 只支持 PT 5.x；6.2+ 文件会报 "解压失败/不是 5.x 格式"。
- 主机 DHCP 模式未实现（`pc` 仅静态地址）。
- `check` 不模拟交换机二层与 ARP，不能替代 Packet Tracer 里的实际 ping。
- 生成的是新文件；修改用 `patch`，且必须显式指定输出路径。

## 故障排查

| 现象 | 处理 |
|---|---|
| `解压失败` | 文件是 6.2+ 格式，换对应版本 PT 或另存为 5.x |
| `不支持的机型` | 用 `harvest` 从含该机型的 5.x 文件提取模板 |
| `没有端口 xxx` | 对照 `manifest.json` 的端口名（Router-PT 第二个以太口是 `FastEthernet1/0`） |
| patch 后 PT 打不开 | 确认输入文件本身能在 PT 打开；patch 只改文本字段，不动拓扑结构 |
| ping 不通但 check 通 | 属二层/生成树/端口未起问题，在 PT 里看 `show ip interface brief` |

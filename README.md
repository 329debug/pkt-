# pkt-generator

Cisco Packet Tracer **5.x（5.2 / 5.3）** `.pkt` 实验文件的**生成、修改、检查**技能（Skill）。

纯 Python 标准库实现，直接把 `.pkt` 当作可读写的 XML 容器：解码 → 修改 → 重新编码。**不需要打开 Packet Tracer，也不需要任何第三方依赖（Python 3.8+）**。

> 适用于课程实验、批量出题、批量改配置、自动判卷前的连通性自检。

## 功能

| 命令 | 作用 |
|---|---|
| `pkt.py generate 蓝图.json -o out.pkt` | 按 JSON 蓝图生成新拓扑：设备/坐标/连线/线缆/接口 IP/静态路由/默认路由/OSPF/VLAN/PC 地址 |
| `pkt.py patch in.pkt -o out.pkt --spec 补丁.json` | **定点修改已有 .pkt**，完整保留原拓扑、连线、端口映射，只改指定字段 |
| `pkt.py check in.pkt [--from 主机A --to 主机B]` | 不启动 PT，按路由表逐跳模拟三层可达性 |
| `pkt.py inspect in.pkt` | 打印设备清单、主机地址、路由器接口/路由、可达性结果 |
| `pkt.py decode in.pkt` | `.pkt` 解包为可读 XML |
| `pkt.py harvest --donor x.pkt --out templates` | 从任意 5.x `.pkt` 抽取机型模板，扩展支持机型 |

能力细节：

- **布局**：每台设备 `x/y` 手动定位，或按路由器/交换机/主机自动分层；设备连接支持直通/交叉/串行/光纤/console
- **配置**：接口 IP、掩码、description、shutdown、串口 clock rate；任意 CLI 行（静态路由、默认路由、OSPF、NAT、ACL…）
- **主机**：静态 IP / 网关 / DNS
- **画布**：自动为每个接口生成 `IP/前缀` 标注
- **检查**：最长前缀匹配 + 下一跳可达性校验，输出逐跳路径

## 安装

技能目录的本质是：一个包含 `SKILL.md` 的文件夹。把本仓库内容放进 agent 的 skills 目录下的 `pkt-generator/` 即可。

### 方式一：git clone（推荐）

```powershell
# Windows PowerShell
git clone https://github.com/329debug/pkt-.git "$env:TEMP\pkt-"
New-Item -ItemType Directory -Force "$env:USERPROFILE\.trae-cn\builtin\global\skills\pkt-generator" | Out-Null
Copy-Item "$env:TEMP\pkt-\*" "$env:USERPROFILE\.trae-cn\builtin\global\skills\pkt-generator" -Recurse -Force
```

```bash
# macOS / Linux（Claude Code 等）
git clone https://github.com/329debug/pkt-.git /tmp/pkt-
mkdir -p ~/.claude/skills/pkt-generator
cp -R /tmp/pkt-/. ~/.claude/skills/pkt-generator/
```

常见 skills 目录：

| Agent | 全局目录 | 项目级目录 |
|---|---|---|
| TRAE / TraeCode（Windows） | `%USERPROFILE%\.trae-cn\builtin\global\skills\` | `.trae/skills/` |
| Claude Code | `~/.claude/skills/` | `.claude/skills/` |
| 其他兼容 Agent Skills 的客户端 | 其文档指定的 skills 目录 | 项目内 `.skills/` 等 |

### 方式二：下载 zip

下载仓库 zip（Code → Download ZIP），解压后把内部文件放到 `<skills目录>/pkt-generator/`。

### 验证安装

```powershell
python <skills目录>/pkt-generator/scripts/pkt.py --help
python <skills目录>/pkt-generator/scripts/pkt.py generate examples/实验五_静态路由.json -o demo.pkt
python <skills目录>/pkt-generator/scripts/pkt.py check demo.pkt --from 主机0 --to 主机2
# 期望：[通] 主机0 -> 主机2 ... 结论: 主机间三层可达
```

## 快速开始

生成（蓝图）：

```powershell
python scripts/pkt.py generate examples/实验五_静态路由.json -o 实验五_静态路由.pkt --xml out.xml
```

修改已有文件（补丁）：

```powershell
python scripts/pkt.py patch 静态路由.pkt -o 静态路由_已配置.pkt --spec examples/patch_示例.json
```

检查：

```powershell
python scripts/pkt.py check 静态路由_已配置.pkt --from 主机0 --to 主机2
```

## 蓝图格式（generate）

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

## 补丁格式（patch）

只写要改的部分，未提到的内容保持原样：

```json
{
  "hosts": {"主机0": {"ip": "172.16.2.10", "mask": "255.255.255.0", "gateway": "172.16.2.254"}},
  "interfaces": {"路由器3": {"FastEthernet0/0": {"shutdown": true}}},
  "routes": {"路由器1": ["ip route 0.0.0.0 0.0.0.0 172.16.1.6"]},
  "notes": {"172.16.2.1/24": "172.16.2.10/24"}
}
```

- `routes` 会先删除该设备原有 `ip route` 行再写入，不会叠加
- `interfaces` 同时更新接口对象和 `running-config` 的 `interface` 段
- `notes` 用于同步画布上的地址标注

## 支持机型（18 种）

| 类型 | 机型 |
|---|---|
| Router | `Router-PT`、`2811`、`1841`、`2620XM`、`2621XM` |
| Switch | `2960-24TT`、`2950-24`、`2950T-24`、`Switch-PT` |
| MultiLayerSwitch | `3560-24PS` |
| 主机 | `PC-PT`、`Laptop-PT`、`Server-PT`、`Printer-PT`、`WiredEndDevice-PT` |
| AccessPoint | `AccessPoint-PT`、`AccessPoint-PT-A`、`AccessPoint-PT-N` |

端口名见 `templates/manifest.json`。新增机型：

```powershell
python scripts/pkt.py harvest --donor 含新机型的5.x.pkt --out templates
```

不支持的设备（PT 不保存真实端口名，需逐个手工标定）：`Hub-PT`、`Bridge-PT`、`Repeater-PT`、`Cloud-PT`、`DSL/Cable-Modem-PT`、`7960` IP 电话、`Linksys-WRT300N`、`PDA/Tablet/WirelessEndDevice`、`TV-PT`、`CoaxialSplitter-PT`、`*-Empty`。

## 目录结构

```
pkt-/
├── SKILL.md              # 技能定义（frontmatter: name / description）
├── scripts/
│   ├── pkt.py            # 统一 CLI
│   ├── pkt_codec.py      # 5.x .pkt <-> XML
│   ├── pktgen.py         # 蓝图 -> 新 .pkt
│   ├── patch.py          # 定点修改已有 .pkt
│   ├── check.py          # 三层可达性静态检查
│   └── harvest.py        # 抽取机型模板/骨架
├── templates/            # 18 种机型模板 + skeleton + manifest
├── examples/             # 蓝图与补丁示例
├── LICENSE
└── README.md
```

## 限制

- 仅支持 PT 5.x；6.2+ 使用 Twofish/EAX 加密，会报“解压失败/不是 5.x 格式”
- 主机仅静态地址，DHCP 客户端模式未实现
- `check` 只模拟三层路由，不模拟交换机二层/生成树/ARP，不能替代实际 ping
- `patch` 必须显式指定输出路径，不会覆盖原文件

## 一键部署提示词（复制给任意 agent）

```text
请为我安装 GitHub 技能仓库 https://github.com/329debug/pkt-.git（技能名 pkt-generator）。

要求：
1. 克隆仓库到临时目录，保留全部文件。
2. 将其中的 SKILL.md、scripts/、templates/、examples/、LICENSE、README.md 复制到你的技能目录下的 pkt-generator 子目录（例如 ~/.claude/skills/pkt-generator 或 Windows 下的 %USERPROFILE%\.trae-cn\builtin\global\skills\pkt-generator；如目录不存在请创建）。
3. 不要修改模板内容，不要删除 examples。
4. 安装后执行验证：
   - python scripts/pkt.py --help
   - python scripts/pkt.py generate examples/实验五_静态路由.json -o /tmp/pkt_demo.pkt
   - python scripts/pkt.py check /tmp/pkt_demo.pkt --from 主机0 --to 主机2
5. 报告安装路径、机器上识别到的技能名、以及验证输出；若 check 输出“[通]”则安装成功。
6. 顺便告诉我该技能支持哪些机型、有哪些命令。
```

## License

MIT，见 [LICENSE](LICENSE)。

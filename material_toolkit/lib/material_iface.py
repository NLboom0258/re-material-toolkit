"""从模板 PS 反汇编自动生成 HLSL 接口声明(cbuffer / 贴图 / sampler), 并支持扩展。

用途: pass 模板里的接口块不再写死某个 master; 由模板 mmtr 的 Deferred PS 生成,
既方便接新 master, 也是"材质自带参数/贴图"的基础。

只依赖 D3DDisassemble 的注释头(Buffer Definitions / Resource Bindings), 与项目其它
反汇编解析同源。
"""
import re

from . import mmtr_blobs as B

_CBUF_RE = re.compile(r"^//\s*cbuffer\s+(\w+)")
_MEM_RE = re.compile(
    r"^//\s+(.+?)\s+(\w+)(\[\d+\])?;\s*//\s*Offset:\s*(\d+)\s+Size:\s*(\d+)")
_HEADER_SKIP = ("Name", "----")

# 材质参数类型 -> 字节大小
TYPE_SIZE = {"float": 4, "int": 4, "uint": 4, "bool": 4,
             "float2": 8, "float3": 12, "float4": 16,
             "float2x2": 16, "float3x3": 48, "float4x4": 64,
             "row_major float4x4": 64, "row_major float3x4": 48}


def _bindings(txt):
    """{名字: (类型, 寄存器)} ← 'Resource Bindings' 表。"""
    out = {}
    in_b = False
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("// Resource Bindings:"):
            in_b = True
            continue
        if not in_b:
            continue
        if not s.startswith("//"):
            in_b = False
            continue
        body = s[2:].strip()
        if not body or body.startswith(_HEADER_SKIP):
            continue
        parts = body.split()
        if len(parts) >= 5 and parts[-1].isdigit():
            out[parts[0]] = (parts[1], parts[-2])
    return out


def _cbuffers(txt, binds):
    out = []
    lines = txt.splitlines()
    i = 0
    while i < len(lines):
        m = _CBUF_RE.match(lines[i].strip())
        if not m:
            i += 1
            continue
        name = m.group(1)
        members = []
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("// }"):
            mm = _MEM_RE.match(lines[i].strip())
            if mm:
                mtype, mname, arr, off, size = mm.groups()
                members.append({"type": mtype.strip(), "name": mname + (arr or ""),
                                "offset": int(off), "size": int(size)})
            i += 1
        out.append({"name": name,
                    "reg": re.sub(r"^cb", "b", binds.get(name, ("", "?"))[1]),
                    "members": members})
        i += 1
    return out


def iface_from_dxbc(dxbc):
    """解析 PS 反汇编 -> {'cbuffers':[...], 'textures':[...], 'samplers':[...]}。"""
    txt = B.disassemble_dxbc(dxbc)
    binds = _bindings(txt)
    cbuffers = _cbuffers(txt, binds)

    textures, samplers = [], []
    for name, (typ, reg) in binds.items():
        if typ == "sampler":
            samplers.append({"name": name, "reg": reg})
        elif typ == "texture":
            fmt = "?"
            for line in txt.splitlines():
                s = line.strip()
                if s.startswith("//") and s[2:].strip().startswith(name + " "):
                    parts = s[2:].split()
                    if len(parts) >= 3:
                        fmt = parts[2]
                    break
            textures.append({"name": name, "reg": reg, "fmt": fmt})

    def _key(d):
        return int(re.sub(r"\D", "", d["reg"]) or 0)

    return {"cbuffers": cbuffers,
            "textures": sorted(textures, key=_key),
            "samplers": sorted(samplers, key=_key)}


def next_offset(members, size):
    """按 HLSL cbuffer 打包规则算出下一个成员的偏移(不跨 16 字节行)。"""
    if not members:
        return 0
    last = members[-1]
    off = last["offset"] + last["size"]
    if (off % 16) + size > 16:
        off = (off + 15) & ~15
    return off


def extend(iface, cbuffer="UserMaterial", params=(), textures=()):
    """在接口上新增材质参数/贴图(供 ⑤b 用)。返回 (新 iface, 新增参数 [(name,size,offset)])。

    params: [(name, type)]; textures: [name]。参数追加到 cbuffer 成员末尾(按 HLSL 打包)。
    """
    import copy
    out = copy.deepcopy(iface)
    added = []
    cb = next((c for c in out["cbuffers"] if c["name"] == cbuffer), None)
    if cb is not None:
        for name, typ in params:
            size = TYPE_SIZE.get(typ, 4)
            off = next_offset(cb["members"], size)
            cb["members"].append({"type": typ, "name": name,
                                  "offset": off, "size": size})
            added.append((name, size, off))
    # 贴图: 新 t 号 = 现有最大 + 1
    tmax = max([int(re.sub(r"\D", "", t["reg"]) or -1)
                for t in out["textures"]] or [-1])
    for name in textures:
        tmax += 1
        out["textures"].append({"name": name, "reg": "t%d" % tmax, "fmt": "float4"})
    return out, added


def hlsl_of(iface):
    """接口 -> HLSL 声明文本。"""
    out = ["// ===== 自动生成: 引擎/材质接口(来自模板 mmtr 的 Deferred PS) ====="]
    for cb in iface["cbuffers"]:
        out.append("cbuffer %s : register(%s)" % (cb["name"], cb["reg"]))
        out.append("{")
        for m in cb["members"]:
            out.append("    %s %s;" % (m["type"], m["name"]))
        out.append("};")
        out.append("")
    for t in iface["textures"]:
        if t["fmt"] == "byte":
            out.append("ByteAddressBuffer %s : register(%s);" % (t["name"], t["reg"]))
        elif t["fmt"] == "float4":
            out.append("Texture2D<float4> %s : register(%s);" % (t["name"], t["reg"]))
        else:
            out.append("// [跳过] %s : %s (fmt=%s)"
                       % (t["name"], t["reg"], t["fmt"]))
    for s in iface["samplers"]:
        out.append("SamplerState %s : register(%s);" % (s["name"], s["reg"]))
    return "\n".join(out) + "\n"

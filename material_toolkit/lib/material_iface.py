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
    lines = txt.splitlines()
    for name, (typ, reg) in binds.items():
        if typ in ("sampler", "sampler_c"):
            samplers.append({"name": name, "reg": reg, "cmp": typ == "sampler_c"})
        elif typ == "texture":
            fmt, dim = "?", "2d"
            for line in lines:
                s = line.strip()
                if s.startswith("//") and s[2:].strip().startswith(name + " "):
                    parts = s[2:].split()
                    if len(parts) >= 3:
                        fmt = parts[2]
                    if len(parts) >= 4:
                        dim = parts[3]
                    break
            textures.append({"name": name, "reg": reg, "fmt": fmt, "dim": dim})

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
    if cb is None and params:              # 基础接口未含 UserMaterial 时按需创建
        used_b = {_regnum(c.get("reg", "b0")) for c in out["cbuffers"]}
        n = 0
        while n in used_b:
            n += 1
        cb = {"name": cbuffer, "reg": "b%d" % n, "members": []}
        out["cbuffers"].append(cb)
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


def _regnum(reg):
    return int(re.sub(r"\D", "", reg) or 0)


def _material_cbuffer(iface, name="UserMaterial"):
    for cb in iface["cbuffers"]:
        if cb["name"] == name:
            return cb
    return None


def _flat_cbuffer(out, cb):
    out.append("cbuffer %s : register(%s)" % (cb["name"], cb["reg"]))
    out.append("{")
    for m in cb["members"]:
        out.append("    %s %s;" % (m["type"], m["name"]))
    out.append("};")
    out.append("")


# 元素类型 / 维度 -> HLSL 资源声明
_ELEM = {"float": "float", "float2": "float2", "float3": "float3", "float4": "float4",
         "int": "int", "uint": "uint", "half": "float", "min16float": "float"}
_DIMTYPE = {"2d": "Texture2D", "2darray": "Texture2DArray", "1d": "Texture1D",
            "1darray": "Texture1DArray", "cube": "TextureCube", "3d": "Texture3D",
            "cubearray": "TextureCubeArray", "2dms": "Texture2DMS",
            "2dmsarray": "Texture2DMSArray"}


def _texture_decl(t, reg=None):
    reg = reg or t["reg"]
    fmt = t.get("fmt")
    dim = t.get("dim") or "2d"
    if fmt == "byte":
        return "ByteAddressBuffer %s : register(%s);" % (t["name"], reg)
    elem = _ELEM.get(fmt)
    base = _DIMTYPE.get(dim)
    if elem is None or base is None:
        # 结构化缓冲(fmt=struct)等需配套 struct 定义, 这里暂不声明(不用即可)
        return "// [跳过] %s : %s (fmt=%s dim=%s)" % (t["name"], reg, fmt, dim)
    if fmt == "uint" and dim == "3d":
        elem = "uint4"
    return "%s<%s> %s : register(%s);" % (base, elem, t["name"], reg)


def hlsl_of(iface, style="cbuffer", material_cbuffer="UserMaterial",
            struct_name="UMParams", buffer_name="UserMaterialInstances",
            load_fn="LoadMaterialParams"):
    """接口 -> HLSL 声明文本。

    style="cbuffer"  = 材质参数走 cbuffer(裸名 VAR_*);
    style="instance" = 材质参数走结构化缓冲(per-instance); 生成 结构体 + StructuredBuffer
                       + `static` 全局(裸名 VAR_*) + 加载函数 LoadMaterialParams(_mu)。
                       纹理寄存器: 非 byte 纹理整体 +1(structured 占最小非 byte 纹理位)。
    """
    out = ["// ===== 自动生成: 引擎/材质接口(来自模板 mmtr 的 Deferred PS) ====="]
    if style == "cbuffer":
        for cb in iface["cbuffers"]:
            _flat_cbuffer(out, cb)
        for t in iface["textures"]:
            out.append(_texture_decl(t))
        for s in iface["samplers"]:
            ty = "SamplerComparisonState" if s.get("cmp") else "SamplerState"
            out.append("%s %s : register(%s);" % (ty, s["name"], s["reg"]))
        return "\n".join(out) + "\n"

    # ---- style == "instance" ----
    mc = _material_cbuffer(iface, material_cbuffer)
    for cb in iface["cbuffers"]:
        if cb is not mc:
            _flat_cbuffer(out, cb)

    # 非 byte 纹理的最小寄存器 = structured 缓冲的寄存器; 非 byte 纹理整体 +1
    nonbyte = [t for t in iface["textures"] if t["fmt"] != "byte"]
    struct_reg = "t%d" % min(_regnum(t["reg"]) for t in nonbyte) if nonbyte else "t0"

    if mc is not None:
        out.append("struct %s" % struct_name)
        out.append("{")
        for m in mc["members"]:
            out.append("    %s %s;" % (m["type"], m["name"]))
        out.append("};")
        out.append("")
        out.append("StructuredBuffer<%s> %s : register(%s);"
                   % (struct_name, buffer_name, struct_reg))
        out.append("")
        # 裸名 static 全局(供材质函数直接用 VAR_*)
        for m in mc["members"]:
            out.append("static %s %s;" % (m["type"], m["name"]))
        out.append("")
        out.append("void %s(%s _mu)" % (load_fn, struct_name))
        out.append("{")
        for m in mc["members"]:
            out.append("    %s = _mu.%s;" % (m["name"], m["name"]))
        out.append("}")
        out.append("")

    for t in iface["textures"]:
        if t["fmt"] == "byte":
            out.append(_texture_decl(t))
        else:
            out.append(_texture_decl(t, "t%d" % (_regnum(t["reg"]) + 1)))
    for s in iface["samplers"]:
        ty = "SamplerComparisonState" if s.get("cmp") else "SamplerState"
        out.append("%s %s : register(%s);" % (ty, s["name"], s["reg"]))
    return "\n".join(out) + "\n"

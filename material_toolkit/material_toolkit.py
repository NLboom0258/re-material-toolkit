#!/usr/bin/env python3
"""material_toolkit CLI — mdf2 / mmtr 材质接口工具。

用法:
  python material_toolkit.py mdf2-dump    <file.mdf2>
  python material_toolkit.py mmtr-dump    <file.mmtr>
  python material_toolkit.py mmtr-add-param <in.mmtr> <cbuffer> <name> <size> <offset> -o <out.mmtr>
  python material_toolkit.py sync <in.mmtr> <in.mdf2> -o <out.mdf2> [--cbuffer UserMaterial] [--prune]
  python material_toolkit.py tex-list   <in.mmtr> <blob_idx>
  python material_toolkit.py tex-add    <in.mmtr> <blob_idx> <name> [--slot N] [--no-rdef] -o <out.mmtr>
  python material_toolkit.py tex-rename <in.mmtr> <blob_idx> <slot> <new_name> -o <out.mmtr>
  python material_toolkit.py mdf2-set-texture <in.mdf2> <material> <type> [path] [-o <out.mdf2>] [--null <path>]
  python material_toolkit.py model-info  <in.mmtr> [--blob N] [--variants]
  python material_toolkit.py model-verify <in.mmtr>
  python material_toolkit.py model-skeleton <in.mmtr> -o <out.bin>
  python material_toolkit.py blob-list    <in.mmtr>
  python material_toolkit.py blob-extract <in.mmtr> <idx> -o <out.dxbc>
  python material_toolkit.py blob-disasm  <dxbc> -o <out.asm.txt>
  python material_toolkit.py blob-asm     <asm.txt> [--ref <dxbc>] -o <out.dxbc>
  python material_toolkit.py blob-verify  <dxbc>

说明:
  - mdf2 结构/读写见 lib/mdf2.py; mmtr 头部/参数表见 lib/mmtr.py;
  - sync 同步层见 lib/sync.py(mdf2 参数集合跟随 mmtr);
  - 纹理槽(资源绑定)见 lib/binding.py: 自动对该 blob 的**所有**绑定组操作
    (同一 shader 有多组"池+描述符", 必须全做); 绑定键=header 池名。
  - 完整解析模型见 lib/mmtr_model.py(头部/程序表/1083 变体记录/绑定组/参数表)。
  - hash = murmur3(名字, 0xFFFFFFFF),见 lib/hashes.py。
  - blob 来源统一入口见 lib/mmtr_blobs.py(枚举/规范化/校验/asm), 见其 docstring。
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib.mdf2 import Mdf2      # noqa: E402
from lib.mmtr import Mmtr      # noqa: E402
from lib.sync import sync_mdf2  # noqa: E402
from lib.binding import add_texture_slot, rename_slot, group_summary  # noqa: E402
from lib.mmtr_model import MmtrModel, stage_label  # noqa: E402
from lib.mmtr_build import MmtrTemplate, content_count  # noqa: E402
from lib.mmtr_info import blob_info  # noqa: E402
from lib import mmtr_blobs as B  # noqa: E402


def _selfcheck_mmtr(data: bytes):
    """编辑 mmtr 后自动跑头部一致性自检; 有问题则告警(不阻断)。见 CLI model-verify。"""
    issues = MmtrModel(bytes(data)).validate()
    if issues:
        print(f"  [WARN] 头部自检: {len(issues)} 处问题(可用 model-verify 查看详情)")
        for s in issues[:5]:
            print("     -", s)
    else:
        print("  [OK] 头部自检通过")
    return issues


def main():
    if len(sys.argv) < 2:
        print(__doc__); return
    cmd = sys.argv[1]
    a = sys.argv[2:]

    if cmd == "mdf2-dump":
        Mdf2.load(a[0]).dump()
    elif cmd == "mmtr-dump":
        Mmtr.load(a[0]).dump()
    elif cmd == "mmtr-add-param":
        src, cbuffer, name, size, offset = a[0], a[1], a[2], int(a[3]), int(a[4])
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        m = Mmtr.load(src)
        data = m.add_cbuffer_param(cbuffer, name, size, offset)
        open(out, "wb").write(data)
        print(f"OK: {src} + param {name}(size={size},off={offset}) -> {out} "
              f"({len(m.data)} -> {len(data)} bytes)")
        _selfcheck_mmtr(data)
    elif cmd == "sync":
        src_mmtr, src_mdf2 = a[0], a[1]
        out = a[a.index("-o") + 1] if "-o" in a else "out.mdf2.10"
        cbuffer = a[a.index("--cbuffer") + 1] if "--cbuffer" in a else "UserMaterial"
        prune = "--prune" in a
        mm = Mmtr.load(src_mmtr)
        mf = Mdf2.load(src_mdf2)
        added, pruned, report = sync_mdf2(mf, mm, cbuffer=cbuffer, prune=prune)
        n = mf.save(out)
        print(f"OK: sync {cbuffer} -> {out} ({n} bytes) added={added} pruned={pruned}")
        for mat_name, new_names in report:
            if new_names:
                print(f"  {mat_name}: +{', '.join(new_names)}")
    elif cmd == "tex-list":
        data = open(a[0], "rb").read()
        tyname = {0x02: "tex2d", 0x80: "raw", 0x00: "sampler", 0xff: "cbuffer"}
        for gi, g in enumerate(group_summary(data, int(a[1]))):
            print(f"group[{gi}] desc@0x{g['desc']:x} pool@0x{g['pool']:x} "
                  f"n_rec={g['n_rec']} a4={g['a4']} ac={g['ac']} b8={g['b8']} cc={g['cc']}")
            for s in g["srvs"]:
                tn = tyname.get(s["type"], f"0x{s['type']:02x}")
                print(f"    [{s['idx']}] t{s['slot']:<3} {tn:<7} {s['name']:<40} 0x{s['hash']:08x}")
    elif cmd == "tex-add":
        src, blob_idx, name = a[0], int(a[1]), a[2]
        slot = int(a[a.index("--slot") + 1]) if "--slot" in a else None
        rdef = "--no-rdef" not in a
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        data = open(src, "rb").read()
        nd = add_texture_slot(data, blob_idx, name, slot=slot, rdef=rdef)
        open(out, "wb").write(nd)
        print(f"OK: +tex {name!r} (blob {blob_idx}, rdef={rdef}) -> {out} "
              f"({len(data)} -> {len(nd)} bytes)")
        _selfcheck_mmtr(nd)
    elif cmd == "tex-rename":
        src, blob_idx, slot, new_name = a[0], int(a[1]), int(a[2]), a[3]
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        data = open(src, "rb").read()
        nd = rename_slot(data, blob_idx, slot, new_name)
        open(out, "wb").write(nd)
        print(f"OK: rename t{slot} -> {new_name!r} (blob {blob_idx}) -> {out} "
              f"({len(data)} -> {len(nd)} bytes)")
        _selfcheck_mmtr(nd)
    elif cmd == "mdf2-set-texture":
        src, mat_name, tex_type = a[0], a[1], a[2]
        path = a[3] if len(a) > 3 and not a[3].startswith("-") else None
        out = a[a.index("-o") + 1] if "-o" in a else "out.mdf2.10"
        nullp = a[a.index("--null") + 1] if "--null" in a else "Null.tex"
        mf = Mdf2.load(src)
        tb = mf.set_texture(mat_name, tex_type, path, default_path=nullp)
        n = mf.save(out)
        print(f"OK: {mat_name} texture {tex_type!r} -> {tb.texture_path!r} -> {out} ({n} bytes)")
    elif cmd == "model-info":
        path = a[0]
        m = MmtrModel.load(path)
        m.dump()
        print("  blobs (idx: role, n_records_using):")
        for i in range(m.blob_count()):
            print(f"    blob[{i}] 0x{m.blob_off(i):x}  role={m.blob_role(i)}  "
                  f"n_rec={len(m.records_using(i))}")
        if "--blob" in a:
            bi = int(a[a.index("--blob") + 1])
            print(f"  groups of blob[{bi}]:")
            for gi, g in enumerate(m.groups(bi)):
                print(f"    g{gi} desc@0x{g.desc:x} pool@0x{g.pool:x} "
                      f"n_srv={len(g.srvs)} stage={stage_label(g.stage_mask)}")
                for s in g.srvs:
                    print(f"        [{s['idx']}] t{s['slot']:<3} type=0x{s['type']:02x} {s['name']}")
        if "--variants" in a:
            for r in m.iter_records():
                print(f"    {r.name:40s} blob=0x{r.blob_off:x} sz={r.blob_size}")
    elif cmd == "model-verify":
        m = MmtrModel.load(a[0])
        issues = m.validate()
        if not issues:
            print(f"OK: {a[0]} 头部自洽")
        else:
            print(f"发现问题 {len(issues)} 处:")
            for s in issues[:50]:
                print("  -", s)
            if len(issues) > 50:
                print(f"  ...(共 {len(issues)})")
            sys.exit(1)
    elif cmd == "model-skeleton":
        out = a[a.index("-o") + 1] if "-o" in a else "skeleton.bin"
        sk = MmtrTemplate.load(a[0]).skeleton
        open(out, "wb").write(sk)
        print(f"OK: 版本骨架 {len(sk)} 字节 -> {out} "
              f"(内容字段 {content_count()} 字节已清零)")
    elif cmd == "blob-list":
        data = open(a[0], "rb").read()
        m = MmtrModel(data)
        for i in range(m.blob_count()):
            info = blob_info(data, i)
            print(f"blob[{i:2d}] @0x{m.blob_off(i):06x} size={m.blob_size(i):6d} "
                  f"role={m.blob_role(i):3s} stage={info['stage']:3s} "
                  f"n_cb={info['n_cb']} n_br={info['n_br']}")
    elif cmd == "blob-extract":
        src, idx = a[0], int(a[1])
        out = a[a.index("-o") + 1] if "-o" in a else f"blob_{idx}.dxbc"
        data = open(src, "rb").read()
        blob = B.extract_blob(data, idx)
        open(out, "wb").write(blob)
        print(f"OK: blob[{idx}] {len(blob)}B -> {out} fingerprint={blob[4:12].hex()}...")
    elif cmd == "blob-disasm":
        src = a[0]
        out = a[a.index("-o") + 1] if "-o" in a else "blob.asm.txt"
        asm = B.disassemble_dxbc(open(src, "rb").read())
        open(out, "w", encoding="utf-8").write(asm)
        print(f"OK: {src} -> {out} ({len(asm)} chars)")
    elif cmd == "blob-asm":
        src = a[0]
        ref = a[a.index("--ref") + 1] if "--ref" in a else None
        out = a[a.index("-o") + 1] if "-o" in a else "out.dxbc"
        asm = open(src, encoding="utf-8").read()
        ref_dxbc = open(ref, "rb").read() if ref else None
        dxbc = B.assemble_asm(asm, ref_dxbc=ref_dxbc)
        open(out, "wb").write(dxbc)
        v = B.verify_dxbc(dxbc)
        print(f"OK: {src} -> {out} ({len(dxbc)}B) stage={v['stage']} "
              f"disasm={'OK' if v['disasm_ok'] else 'FAIL'} "
              f"strip={'OK' if v['strip_ok'] else 'FAIL'} "
              f"reflect={'OK' if v['reflect_ok'] else 'FAIL'}")
    elif cmd == "blob-verify":
        dxbc = open(a[0], "rb").read()
        v = B.verify_dxbc(dxbc)
        print(f"{a[0]}: {len(dxbc)}B stage={v['stage']} n_cb={v['n_cb']} n_br={v['n_br']}")
        print(f"  D3DDisassemble: {'OK' if v['disasm_ok'] else 'FAIL'}")
        print(f"  D3DStripShader : {'OK' if v['strip_ok'] else 'FAIL'}")
        print(f"  D3DReflect     : {'OK' if v['reflect_ok'] else 'FAIL'}")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

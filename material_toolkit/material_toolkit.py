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
  python material_toolkit.py pool-list   <in.mmtr>
  python material_toolkit.py pool-rename <in.mmtr> <old_name> <new_name> -o <out.mmtr>
  python material_toolkit.py mdf2-set-texture <in.mdf2> <material> <type> [path] [-o <out.mdf2>] [--null <path>]
  python material_toolkit.py mdf2-new    <master.mmtr> -o <out.mdf2.10> [--name M] [--mmtr-path MasterMaterial/Master/X.mmtr] [--shading-type Standard] [--game-version 10] [--from <src.mdf2> --material M]
  python material_toolkit.py model-info  <in.mmtr> [--blob N] [--variants]
  python material_toolkit.py model-verify <in.mmtr>
  python material_toolkit.py variant-map <in.mmtr> [--tech NAME] [--slots] [--by-pass]
  python material_toolkit.py variant-diff <in.mmtr> [--tech NAME] [--instr]
  python material_toolkit.py model-skeleton <in.mmtr> -o <out.bin>
  python material_toolkit.py mmtr-new    <template.mmtr> -o <out.mmtr>
  python material_toolkit.py mat-gen    <template.mmtr> [material.hlsl] -o <out.mmtr> [--template deferred_env] [--pass Deferred]
  python material_toolkit.py mat-std    <in.mmtr> [other.mmtr]
  python material_toolkit.py mat-skeleton <donor.mmtr> [material.hlsl] -o <out.mmtr> [--template deferred_env] [--pass Deferred]
  python material_toolkit.py mat-synth  <in.mmtr> [other.mmtr]
  python material_toolkit.py mat-rebuild <in.mmtr> -o <out.mmtr>
  python material_toolkit.py mat-self   <base.mmtr> [material.hlsl] -o <out.mmtr> [--template deferred_bare] [--pass Deferred]
  python material_toolkit.py mmtr-assemble <template.mmtr> --spec <spec.json> -o <out.mmtr>
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
  - mmtr 装配器见 lib/mmtr_assemble.py: 模板 + 程序安装规格 -> 新 mmtr。spec.json:
    {"installs":[{"role":"PS","src_blob":33,"source":{"kind":"asm","path":"x.asm"}},
                   {"role":"PS","src_blob":34,"source":{"kind":"dxbc","path":"y.dxbc"}},
                   {"role":"PS","src_blob":35,"source":{"kind":"blob","mmtr":"other.mmtr","idx":10}}]}
    (source.kind=asm 时, ref 默认取自 src_blob; 可选 sync=true 从同组 donor 同步绑定)
    (可选 in_place=true: 就地改写 src_blob 本身(blob 数不变/无死 blob); 默认追加+重指)
"""
import json
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from lib.mdf2 import Mdf2      # noqa: E402
from lib.mmtr import Mmtr      # noqa: E402
from lib.sync import sync_mdf2  # noqa: E402
from lib.binding import (add_texture_slot, rename_slot, group_summary,  # noqa: E402
                         name_vocabulary, rename_name_global)
from lib.mmtr_model import MmtrModel, stage_label  # noqa: E402
from lib.mmtr_material import MaterialModel  # noqa: E402
from lib.mmtr_variant_diff import VariantDiffer  # noqa: E402
from lib.mmtr_build import MmtrTemplate, content_count, new_from_template  # noqa: E402
from lib.mmtr_info import blob_info  # noqa: E402
from lib import mmtr_blobs as B  # noqa: E402
from lib import mmtr_assemble as aset  # noqa: E402
from lib import material_gen as mgen  # noqa: E402
from lib import material_instance as minst  # noqa: E402
from lib import material_pass as mpass  # noqa: E402
from lib import mmtr_standard as std  # noqa: E402
from lib import mmtr_synth as synth  # noqa: E402
from lib import mmtr_tail as mtail  # noqa: E402
from lib import mmtr_selfgen as selfgen  # noqa: E402


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
    elif cmd == "mdf2-new":
        src = a[0]                       # master mmtr(bytes 来源)
        out = a[a.index("-o") + 1] if "-o" in a else "out.mdf2.10"
        stem = re.sub(r"\.mmtr(\.\d+)?$", "", os.path.basename(src))
        name = a[a.index("--name") + 1] if "--name" in a else stem
        mpath = (a[a.index("--mmtr-path") + 1] if "--mmtr-path" in a
                 else "MasterMaterial/Master/%s.mmtr" % stem)
        stype = a[a.index("--shading-type") + 1] if "--shading-type" in a else "Standard"
        gv = int(a[a.index("--game-version") + 1]) if "--game-version" in a else 10
        if "--from" in a:                # 克隆现有 mdf2, 把某材质改指到该 mmtr
            mat_name = a[a.index("--material") + 1] if "--material" in a else None
            if not mat_name:
                print("ERR: --from 需配合 --material <name>")
                return
            mf = Mdf2.load(a[a.index("--from") + 1])
            minst.repoint(mf, mat_name, mpath, open(src, "rb").read())
            print(f"OK: mdf2-new(--from) repoint {mat_name!r} -> {mpath}")
        else:
            mf = minst.from_mmtr(open(src, "rb").read(), mpath, name,
                                 shading_type=stype, game_version=gv)
        n = mf.save(out)
        print(f"OK: mdf2-new {src} -> {out} ({n} bytes) materials={len(mf.materials)}")
        mf.dump()
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
    elif cmd == "variant-map":
        mm = MaterialModel.load(a[0])
        tech = a[a.index("--tech") + 1] if "--tech" in a else None
        mm.dump(tech, show_slots="--slots" in a, by_pass="--by-pass" in a)
    elif cmd == "variant-diff":
        vd = VariantDiffer(open(a[0], "rb").read())
        tech = a[a.index("--tech") + 1] if "--tech" in a else "DeferredStatic"
        vd.dump_tech(tech, show_instr="--instr" in a)
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
    elif cmd == "pool-list":
        data = open(a[0], "rb").read()
        vocab = name_vocabulary(data)
        print(f"{len(vocab)} 个贴图名(池):")
        for nm in sorted(vocab, key=lambda n: (-vocab[n]["groups"], n)):
            d = vocab[nm]
            print(f"  {nm:44s} groups={d['groups']:3d} blobs={len(d['blobs'])}")
    elif cmd == "pool-rename":
        src, old, new = a[0], a[1], a[2]
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        data = open(src, "rb").read()
        nd = rename_name_global(data, old, new)
        open(out, "wb").write(nd)
        print(f"OK: 全局改名 {old!r} -> {new!r} -> {out} ({len(data)} -> {len(nd)} bytes)")
        _selfcheck_mmtr(nd)
    elif cmd == "mmtr-new":
        out = a[a.index("-o") + 1] if "-o" in a else "new.mmtr.1808168797"
        data = new_from_template(open(a[0], "rb").read())
        open(out, "wb").write(data)
        print(f"OK: 从模板新建 {a[0]} -> {out} ({len(data)} bytes) "
              f"(当前=克隆; 将来=骨架+规格装配)")
        _selfcheck_mmtr(data)
    elif cmd == "mat-gen":
        src = a[0]
        mat = a[1] if len(a) > 1 and not a[1].startswith("-") else None
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr.1808168797"
        tpl = a[a.index("--template") + 1] if "--template" in a else "deferred_env"
        pname = a[a.index("--pass") + 1] if "--pass" in a else "Deferred"
        material_src = open(mat, encoding="utf-8").read() if mat else None
        data, rep = mgen.generate(open(src, "rb").read(), material_src, tpl, pname)
        open(out, "wb").write(data)
        print(f"OK: mat-gen {src} + {mat or '(默认材质)'} -> {out} "
              f"({len(data)} bytes, PS {rep['ps_size']}B)")
        print(f"  替换 blobs={rep['replaced']}  实例(cbuffer/per-instance)={rep['replaced_instance']}  "
              f"跳过(签名不符)={rep['skipped']}  坏={rep['bad']}")
        _selfcheck_mmtr(data)
    elif cmd == "mat-std":
        data = open(a[0], "rb").read()
        other = (open(a[1], "rb").read()
                 if len(a) > 1 and not a[1].startswith("-") else None)
        std.report(data, other)
    elif cmd == "mat-skeleton":
        src = a[0]
        mat = a[1] if len(a) > 1 and not a[1].startswith("-") else None
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr.1808168797"
        tpl = a[a.index("--template") + 1] if "--template" in a else "deferred_env"
        pname = a[a.index("--pass") + 1] if "--pass" in a else "Deferred"
        material_src = open(mat, encoding="utf-8").read() if mat else None
        ps, err = mpass.compile_shading(material_src, tpl)
        if err:
            print("编译失败:\n%s" % err)
            sys.exit(1)
        data, rep = std.generate(open(src, "rb").read(), ps, pname)
        open(out, "wb").write(data)
        print(f"OK: mat-skeleton {src} + {mat or '(默认材质)'} -> {out} "
              f"({len(data)} bytes, PS {rep['ps_size']}B)")
        print(f"  目标={rep['targets']}  替换={rep['replaced']}  跳过={rep['skipped']}  "
              f"坏={rep['bad']}  标准集保留={rep['standard_preserved']}")
        _selfcheck_mmtr(data)
    elif cmd == "mat-synth":
        data = open(a[0], "rb").read()
        synth.report(data)
        if len(a) > 1 and not a[1].startswith("-"):
            other = open(a[1], "rb").read()
            print("  同版本结构 vs %s: %s" % (a[1], synth.same_version(data, other)))
    elif cmd == "mat-rebuild":
        src = a[0]
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr.1808168797"
        data = open(src, "rb").read()
        new = mtail.rebuild_canonical(data)
        if new is None:
            print("FAIL: 无法规范重建(非主版本或尾段过大)")
            return
        open(out, "wb").write(new)
        n, bad = mtail.verify_rebuild(data, new)
        print(f"OK: mat-rebuild {src} -> {out} ({len(data)}=={len(new)} bytes) "
              f"槽={n} 组不匹配={len(bad)}")
        _selfcheck_mmtr(new)
    elif cmd == "mat-self":
        src = a[0]
        mat = a[1] if len(a) > 1 and not a[1].startswith("-") else None
        out = a[a.index("-o") + 1] if "-o" in a else "self.mmtr.1808168797"
        tpl = a[a.index("--template") + 1] if "--template" in a else "deferred_bare"
        pname = a[a.index("--pass") + 1] if "--pass" in a else "Deferred"
        material_src = open(mat, encoding="utf-8").read() if mat else None
        data, rep = selfgen.build(open(src, "rb").read(), material_src, pname, tpl)
        open(out, "wb").write(data)
        print(f"OK: mat-self {src} + {mat or '(默认材质)'} -> {out} "
              f"({len(data)} bytes, 零声明 PS {rep['ps_size']}B) 规范化尾段={rep['canonical']}")
        print(f"  替换={rep['replaced']}  跳过={rep['skipped']}  坏={rep['bad']}  "
              f"重算计数槽={rep['recounted']}")
        _selfcheck_mmtr(data)
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
    elif cmd == "mmtr-assemble":
        src = a[0]
        spec_path = a[a.index("--spec") + 1] if "--spec" in a else a[1]
        out = a[a.index("-o") + 1] if "-o" in a else "out.mmtr"
        template = open(src, "rb").read()
        spec = json.load(open(spec_path, encoding="utf-8"))
        installs = []
        for it in spec.get("installs", []):
            s = it["source"]
            kind = s["kind"]
            if kind == "asm":
                ref = (B.extract_blob(template, it["src_blob"])
                       if it.get("src_blob") is not None else None)
                source = B.BlobSource.from_asm(
                    open(s["path"], encoding="utf-8").read(), ref_dxbc=ref)
            elif kind == "dxbc":
                source = B.BlobSource.from_dxbc(open(s["path"], "rb").read())
            elif kind == "blob":
                mm = open(s["mmtr"], "rb").read() if s.get("mmtr") else template
                source = B.BlobSource.transport(mm, int(s["idx"]))
            else:
                raise ValueError(f"未知 source kind: {kind!r}")
            installs.append(aset.ProgramInstall(
                it["role"], source, src_blob=it.get("src_blob"),
                slots=it.get("slots"), sync=it.get("sync", False),
                in_place=it.get("in_place", False)))
        out_data = aset.assemble(template, installs)
        open(out, "wb").write(out_data)
        print(f"OK: assemble {src} + {len(installs)} installs -> {out} "
              f"({len(template)} -> {len(out_data)} bytes)")
        _selfcheck_mmtr(out_data)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()

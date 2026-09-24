#!/usr/bin/env python3
"""变体差异刻画(L2): 把同技术下的"标志变体"PS/VS 自动聚成"**特性族**"并刻画族内/族间差异。

结论形态(env DeferredStatic 实测):
  - 4 个 PS = **2 个族**(族内差 ~3 行 = "两面法线翻转" `is_front_face`+`movc ...,-n`);
  - 族间差 = 指令数(84 vs 141) + cbuffer 数(4 vs 7) + 是否使用 alpha/dissolve 参数。

四层比对: 指令体 / 材质参数(用或不用) / 资源绑定 / 输入签名。
库: `VariantDiffer`; CLI: `material_toolkit.py variant-diff <in.mmtr> [--tech NAME] [--instr]`
"""
import difflib
import os
import re
import sys

try:
    from . import mmtr_blobs as B
    from .mmtr_material import MaterialModel
except ImportError:  # 允许脚本直接 import
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    from mmtr_material import MaterialModel

PROFILE_RE = re.compile(r"^(ps|vs|gs|cs|hs|ds)_\d_\d$")
_RE_CBUF = re.compile(r"^//\s*cbuffer\s+(\w+)")
_RE_MEMBER = re.compile(r"^//\s*(.+?)\s+(\w+)\s*;")
_RE_BIND = re.compile(r"\b(cb|t|s|u)(\d+)\b")
_SAME_FAMILY_MAX = 12     # 指令差异 <= 该值 ⇒ 同族(实测族内 3 行, 族间 >100 行)


def parse_asm(asm):
    """拆解 3Dmigoto 反汇编: 结构化字段 + 指令体(剔除注释)。"""
    out = {"body": [], "used": set(), "unused": set(), "resources": set(),
           "inputs": set(), "outputs": set(), "cbuffers": set(), "profile": "",
           "slots": 0}
    cur_cb = None
    section = None
    m = re.search(r"Approximately (\d+) instruction slots", asm)
    if m:
        out["slots"] = int(m.group(1))
    for ln in asm.splitlines():
        s = ln.strip()
        if not s:
            continue
        if PROFILE_RE.match(s):
            out["profile"] = s
            section = "body"
            out["body"].append(s)
            continue
        if section == "body":
            if not s.startswith("//"):
                out["body"].append(s)
            continue
        if not s.startswith("//"):
            continue
        raw = s[2:].strip()
        if raw.startswith("cbuffer "):
            mm = _RE_CBUF.match(s)
            cur_cb = mm.group(1) if mm else None
            if cur_cb:
                out["cbuffers"].add(cur_cb)
            continue
        if raw in ("{", "}"):
            if raw == "}":
                cur_cb = None
            continue
        if "Offset:" in raw and ";" in raw and cur_cb:
            mm = _RE_MEMBER.match(s)
            if mm:
                key = "%s.%s" % (cur_cb, mm.group(2))
                (out["unused"] if "[unused]" in raw else out["used"]).add(key)
            continue
        toks = raw.split()
        if len(toks) >= 6 and _RE_BIND.search(toks[-2]):
            out["resources"].add((toks[0], toks[1], toks[-2], toks[-1]))
            continue
        if len(toks) >= 7 and toks[1].isdigit():
            key = tuple(toks[:7])
            if section == "out":
                out["outputs"].add(key)
            else:
                out["inputs"].add(key)
    return out


def _sig_sections(asm):
    inc, outp, section = [], [], None
    for ln in asm.splitlines():
        s = ln.strip()
        if s == "// Input signature:":
            section = "in"
            continue
        if s == "// Output signature:":
            section = "out"
            continue
        if s.startswith("// cbuffer ") or s == "// Resource Bindings:":
            section = None
            continue
        if section == "in":
            inc.append(s)
        elif section == "out":
            outp.append(s)
    return inc, outp


def _sig_entries(block):
    r = set()
    for s in block:
        if not s.startswith("//"):
            continue
        toks = s[2:].split()
        if len(toks) >= 7 and toks[1].isdigit():
            r.add(tuple(toks[:7]))
    return r


class VariantDiffer(object):
    """单 mmtr 的变体差异分析(带反汇编缓存)。"""

    def __init__(self, data):
        self.data = data
        self.mm = MaterialModel(data)
        self._p = {}

    def parsed(self, idx):
        if idx not in self._p:
            asm = B.disassemble_dxbc(B.extract_blob(self.data, idx))
            p = parse_asm(asm)
            inc, outp = _sig_sections(asm)
            p["inputs"], p["outputs"] = _sig_entries(inc), _sig_entries(outp)
            self._p[idx] = p
        return self._p[idx]

    def diff_blobs(self, ia, ib):
        pa, pb = self.parsed(ia), self.parsed(ib)
        d = [x for x in difflib.unified_diff(pa["body"], pb["body"], lineterm="", n=0)
             if x[:1] in "+-" and x[:3] not in ("+++", "---")]
        return {
            "blobs": (ia, ib), "instr_changed": len(d),
            "added": [x[1:] for x in d if x[0] == "+"],
            "removed": [x[1:] for x in d if x[0] == "-"],
            "params_only_b": sorted(pa["unused"] & pb["used"]),
            "params_only_a": sorted(pb["unused"] & pa["used"]),
            "res_only_b": sorted(x[0] for x in pb["resources"] - pa["resources"]),
            "res_only_a": sorted(x[0] for x in pa["resources"] - pb["resources"]),
            "inputs_only_b": sorted(x[0] for x in pb["inputs"] - pa["inputs"]),
            "slots": (pa["slots"], pb["slots"]),
        }

    def families(self, blobs):
        """按指令体相似度把 blob 聚成族(并查集)。"""
        parent = {b: b for b in blobs}

        def find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(blobs):
            for b in blobs[i + 1:]:
                if self.diff_blobs(a, b)["instr_changed"] <= _SAME_FAMILY_MAX:
                    parent[find(b)] = find(a)
        fam = {}
        for b in blobs:
            fam.setdefault(find(b), []).append(b)
        return [sorted(v) for v in fam.values()]

    def tech_report(self, tech):
        sets = self.mm.program_sets(tech)
        ps = sorted({g["programs"][0] for g in sets if g["programs"][0] >= 0})
        vs = sorted({g["programs"][1] for g in sets if g["programs"][1] >= 0})
        pre_of = {}
        for g in sets:
            for pre in g["prefixes"]:
                pre_of.setdefault(g["programs"], []).append(pre)
        out = {"tech": tech, "ps": ps, "vs": vs, "prefix_of": pre_of,
               "ps_families": self.families(ps) if len(ps) > 1 else [ps],
               "vs_families": self.families(vs) if len(vs) > 1 else [vs]}
        return out

    def dump_tech(self, tech, show_instr=False):
        r = self.tech_report(tech)
        print("== 技术 %s ==  PS 族=%d(%s)  VS 族=%d(%s)"
              % (tech, len(r["ps_families"]), r["ps_families"],
                 len(r["vs_families"]), r["vs_families"]))
        for label, fams in (("PS", r["ps_families"]), ("VS", r["vs_families"])):
            for fi, fam in enumerate(fams):
                pres = []
                for b in fam:
                    for key, ps in r["prefix_of"].items():
                        if (label == "PS" and key[0] == b) or (label == "VS" and key[1] == b):
                            pres += ps
                p0 = self.parsed(fam[0])
                print("   %s族%d: blobs=%s  前缀=%s  指令~%d cbuffers=%d"
                      % (label, fi + 1, fam, sorted(set(pres)), p0["slots"], len(p0["cbuffers"])))
        # 族内差(每族取首个与其余各一个)
        for label, fams in (("PS", r["ps_families"]), ("VS", r["vs_families"])):
            for fam in fams:
                for b in fam[1:]:
                    d = self.diff_blobs(fam[0], b)
                    print("   %s族内 %d->%d: +%d/-%d  (+%s)"
                          % (label, fam[0], b, len(d["added"]), len(d["removed"]),
                             "; ".join(d["added"][:4]) if d["added"] else "-"))
        # 族间差
        for label, fams in (("PS", r["ps_families"]), ("VS", r["vs_families"])):
            if len(fams) > 1:
                d = self.diff_blobs(fams[0][0], fams[1][0])
                print("   %s族间 %d->%d: +%d/-%d  slots=%s  新增资源=%s  新启用参数=%s  新增输入=%s"
                      % (label, fams[0][0], fams[1][0], len(d["added"]), len(d["removed"]),
                         d["slots"], d["res_only_b"][:6], d["params_only_b"][:6],
                         d["inputs_only_b"][:6]))
                if show_instr:
                    for x in d["added"][:10]:
                        print("        + %s" % x)


def main():
    m = VariantDiffer(open(sys.argv[1], "rb").read())
    tech = sys.argv[2] if len(sys.argv) > 2 else "DeferredStatic"
    m.dump_tech(tech, show_instr="--instr" in sys.argv)


if __name__ == "__main__":
    main()

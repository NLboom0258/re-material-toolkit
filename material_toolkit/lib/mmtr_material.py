#!/usr/bin/env python3
"""材质本体模型(只读): 把 1083 个变体槽还原成"技术 × 标志 -> 程序集"的材质视图。

背景(2026-09-25 实测; 详见 analysis/mmtr_variant_model.md):
  - 变体名 = [标志前缀 A/TS/ATS/ADirect/ATSDirect][技术名];
  - **技术** = 管线结构(Deferred/Shadow/ZPrePass/DepthWrite/… × Static/Skinning/Instancing/
    PreTransform/… × Clip);
  - **技术内**: 真 VS(rec-0x20) 按"有无标志"分 1~2 种; PS 按具体标志分 1~4 种;
  - **前缀 -> (PS,VS) 小矩阵**: 实测 `A`≡`ADirect`、`ATS`≡`ATSDirect`(程序完全相同
    ⇒ "Direct" 只影响引擎/RS 侧, 不改 shader); 无前缀('')通常用另一套"最简 pass"程序。
  - 用法: 编辑时可按"材质(技术)"而非"1083 个槽"来理解; 同技术下多个槽可批量应用同一改动。
"""
import struct

try:
    from .mmtr_model import MmtrModel, split_variant_name
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from mmtr_model import MmtrModel, split_variant_name

# 引擎已知的标志前缀(顺序 = 语义扩张: 无 -> alpha -> 两面 -> 两者)
KNOWN_PREFIXES = ("", "A", "ADirect", "TS", "ATS", "ATSDirect")

# 技术名“管线维度”(2026-09-25 实测): [Pass] + [Input] + 修饰符{Instancing/WithNorm/Clip/LW} + 数码标记 + [CS]
TECH_PASSES = ("PreTransform", "DepthWrite", "Deferred", "ZPrePass", "Shadow", "Forward", "Pick")
TECH_INPUTS = ("PreTransform", "Static", "Skinning")   # 顶点输入模式
TECH_MODIFIERS = ("Instancing", "WithNorm", "Clip", "LW")
_TECH_TOKENS = tuple(sorted(set(TECH_INPUTS) | set(TECH_MODIFIERS), key=len, reverse=True))


def parse_technology(name):
    """技术名 -> 管线维度 dict。

    {name, pass, input, instancing, clip, lw, with_norm, cs, variant_tag, unknown}
    `unknown` = 无法识别的片段(正常应为空; 非空=出现新关键字, 提示需补词典)。
    例: 'ATSDeferredStaticInstancing2Clip' 的 tech 部分 'DeferredStaticInstancing2Clip'
        -> pass=Deferred, input=Static, instancing=True, variant_tag='2', clip=True
    """
    out = {"name": name, "pass": None, "input": None, "instancing": False,
           "clip": False, "lw": False, "with_norm": False, "cs": False,
           "variant_tag": "", "unknown": []}
    rest = name
    for p in TECH_PASSES:
        if rest.startswith(p):
            out["pass"] = p
            rest = rest[len(p):]
            break
    while rest:
        hit = None
        if rest[0].isdigit():
            j = 0
            while j < len(rest) and rest[j].isdigit():
                j += 1
            out["variant_tag"] += rest[:j]
            hit = rest[:j]
        elif rest.startswith("CS"):
            out["cs"] = True
            hit = "CS"
        else:
            for tok in _TECH_TOKENS:
                if rest.startswith(tok):
                    hit = tok
                    if tok in TECH_INPUTS:
                        out["input"] = tok
                    elif tok == "Instancing":
                        out["instancing"] = True
                    elif tok == "Clip":
                        out["clip"] = True
                    elif tok == "LW":
                        out["lw"] = True
                    elif tok == "WithNorm":
                        out["with_norm"] = True
                    break
        if hit is None:
            out["unknown"].append(rest[0])
            rest = rest[1:]
        else:
            rest = rest[len(hit):]
    return out


class MaterialModel(object):
    """mmtr 的"材质"视图: 技术 -> 变体(前缀) -> (PS,VS,CS) 程序。只读。"""

    def __init__(self, data):
        self.data = data
        self.model = MmtrModel(data)
        self._idx_of = {o: i for i, (o, _s) in enumerate(self.model.blobs)}
        self._variants = None  # [{slot,name,prefix,tech,ps,vs,cs}](ps/vs/cs 为 blob 序号, 无=-1)

    @classmethod
    def load(cls, path):
        return cls(open(path, "rb").read())

    # ---- 变体 ----
    def variants(self):
        if self._variants is None:
            out = []
            d = self.data
            for i, r in enumerate(self.model.parse_records()):
                hs = struct.unpack_from("<I", d, r.off - 0x18)[0]
                ds = struct.unpack_from("<I", d, r.off - 0x10)[0]
                gs = struct.unpack_from("<I", d, r.off - 0x08)[0]
                # 记录非空 = 6 个程序槽任一存在(不可只看 P0: CS/HS/DS/GS-only 也算)
                if not (r.blob_off or r.vs_blob or r.cs_blob or hs or ds or gs):
                    continue
                pre, tech = split_variant_name(r.name)
                idx = self._idx_of
                out.append({
                    "slot": i, "name": r.name, "prefix": pre, "tech": tech,
                    "ps": idx.get(r.blob_off, -1), "vs": idx.get(r.vs_blob, -1),
                    "cs": idx.get(r.cs_blob, -1),
                    "hs": idx.get(hs, -1), "ds": idx.get(ds, -1), "gs": idx.get(gs, -1),
                })
            self._variants = out
        return self._variants

    def prefix_counts(self):
        from collections import Counter
        return Counter(v["prefix"] for v in self.variants())

    def technologies(self):
        """{技术名: [variants...]}(按技术名排序)。"""
        out = {}
        for v in self.variants():
            out.setdefault(v["tech"], []).append(v)
        return {k: out[k] for k in sorted(out)}

    def program_sets(self, tech):
        """该技术的"程序集": [(ps,vs,hs,ds,gs,cs) -> [prefix...]](按程序序)。

        ⇒ 前缀里程序相同者会并到一项(实测 `A` 与 `ADirect` 并、`ATS` 与 `ATSDirect` 并)。
        """
        groups = {}

        def key_of(v):
            return (v["ps"], v["vs"], v["hs"], v["ds"], v["gs"], v["cs"])

        for v in self.technologies().get(tech, []):
            groups.setdefault(key_of(v), []).append(v["prefix"] or "-")
        out = []
        for key in sorted(groups):
            out.append({"programs": key, "prefixes": sorted(set(groups[key])),
                        "slots": [v["slot"] for v in self.technologies()[tech]
                                  if key_of(v) == key]})
        return out

    def shared_vs(self, tech):
        """该技术的**不同真 VS 数**(实测 1~2: '无标志' 与 '有标志' 两族)。"""
        return sorted({v["vs"] for v in self.technologies().get(tech, []) if v["vs"] >= 0})

    def shared_ps(self, tech):
        return sorted({v["ps"] for v in self.technologies().get(tech, []) if v["ps"] >= 0})

    # ---- 管线维度(技术名结构化; 2026-09-25) ----
    def tech_dims(self):
        """{技术名: parse_technology(...)}(跳过空技术名)。"""
        return {t: parse_technology(t) for t in self.technologies() if t}

    def by_pass(self):
        """{pass: [技术名...]}(按 pass 名排序; pass=None 归到 '(none)')。"""
        out = {}
        for t, d in self.tech_dims().items():
            out.setdefault(d["pass"] or "(none)", []).append(t)
        return {k: sorted(out[k]) for k in sorted(out)}

    def select(self, pass_=None, input_=None, clip=None, instancing=None):
        """按管线维度筛选变体(返回 variant dict 列表)。None = 不限制。

        供 L3“批量应用”定位目标(如 pass_='Shadow', input_='Static')。
        """
        out = []
        for v in self.variants():
            d = parse_technology(v["tech"])
            if pass_ is not None and d["pass"] != pass_:
                continue
            if input_ is not None and d["input"] != input_:
                continue
            if clip is not None and d["clip"] != clip:
                continue
            if instancing is not None and d["instancing"] != instancing:
                continue
            out.append(v)
        return out

    # ---- 汇总 ----
    def summary(self):
        vs = self.variants()
        techs = self.technologies()
        sets = {t: self.program_sets(t) for t in techs}
        import statistics
        per_tech_sets = [len(s) for s in sets.values()]
        return {
            "version": "0x%08x" % self.model.version,
            "blobs": self.model.blob_count(),
            "records": self.model.record_count(),
            "empty_records": self.model.empty_record_count(),
            "variant_records": len(vs),
            "technologies": len(techs),
            "prefix_counts": dict(self.prefix_counts()),
            "program_sets_per_tech": {
                "min": min(per_tech_sets) if per_tech_sets else 0,
                "max": max(per_tech_sets) if per_tech_sets else 0,
                "median": int(statistics.median(per_tech_sets)) if per_tech_sets else 0,
            },
        }

    def dump(self, only=None, show_slots=False, by_pass=False):
        s = self.summary()
        print("材质本体: ver=%s blobs=%d 记录=%d(PS空/仅VS=%d) 有程序=%d 技术=%d"
              % (s["version"], s["blobs"], s["records"], s["empty_records"],
                 s["variant_records"], s["technologies"]))
        print("  前缀分布: %s" % s["prefix_counts"])
        print("  每技术程序集数: min=%d max=%d median=%d"
              % (s["program_sets_per_tech"]["min"], s["program_sets_per_tech"]["max"],
                 s["program_sets_per_tech"]["median"]))
        if by_pass:
            print("  按 pass 分组:")
            for p, techs in self.by_pass().items():
                print("    [%s] %d 个技术: %s" % (p, len(techs), ", ".join(techs)))
            return
        for tech in self.technologies():
            if only and only not in tech:
                continue
            sets = self.program_sets(tech)
            print("  [%s]  程序集=%d" % (tech, len(sets)))
            for g in sets:
                ps, vs, hs, ds, gs, cs = g["programs"]
                parts = ["PS=%d" % ps, "VS=%d" % vs]
                for lab, val in (("HS", hs), ("DS", ds), ("GS", gs), ("CS", cs)):
                    if val >= 0:
                        parts.append("%s=%d" % (lab, val))
                print("       %-40s  <- %s%s"
                      % (" ".join(parts), ",".join(g["prefixes"]),
                         ("  slots=%s" % g["slots"]) if show_slots else ""))


def main():
    import sys
    m = MaterialModel.load(sys.argv[1])
    only = sys.argv[2] if len(sys.argv) > 2 else None
    m.dump(only, show_slots="--slots" in sys.argv, by_pass="--by-pass" in sys.argv)


if __name__ == "__main__":
    main()

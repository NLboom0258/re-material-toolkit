"""材质资产(Material Asset) —— 材质系统的“作者源”层(见 analysis/mmtr_input_boundary.md §9)。

分层(参照 UE):
- 本资产 = 一组“材质函数”(表3a: BaseColor/…)+ 输入注册(preset/engine/param/tex)+ 两个着色选项;
- pass 模板(系统)调用材质函数并做各 pass 的打包/输出;
- 本模块只做【模型 + JSON + 校验】, 不含编译/装配(M2/M3)。

两个**正交**选项(可任意组合, 无非法组合):
- lighting_mode: ``default``(材质只给 PBR; 光照/打包由模板按引擎逻辑做) / ``custom``(材质**直控输出**: 前向=写最终色、延迟=写原始 GBuffer; 最终输出前的后处理也交给材质);
- shading_type:  ``deferred`` / ``forward``。

输入注册(唯一真源, 2026-10-01 重构):
- `inputs` = `material_inputs_model` 的结构化 per-pass 注册(preset/engine/param/tex);
- `shading.source` = **纯 HLSL**(不再含 `//!` 注释声明)。
"""
import json

from . import material_inputs_model as MIM

FORMAT = "mmat/2"

LIGHTING_MODES = ("default", "custom")
SHADING_TYPES = ("deferred", "forward")

# custom 下“输出/后处理”由材质控制的契约(shading_type -> [(名, 说明)])
CUSTOM_OUTPUTS = {
    "forward": [("Color", "最终输出色(o0; 雾/曝光等后处理也由材质写)")],
    "deferred": [("RT0", "原始 GBuffer RT0(Emissive)"),
                 ("RT1", "原始 GBuffer RT1(BaseColorMetallic)"),
                 ("RT2", "原始 GBuffer RT2(NormalRoughnessMisc)"),
                 ("RT3", "原始 GBuffer RT3(OcclusionVelocitySubSurface)")],
}
# 最终输出前的“后处理”步骤(custom 下交给材质; AA 动量/速度固定, 不暴露)
POST_EXPOSED = [
    ("EmissiveScale", "自发光曝光/detone 缩放(延迟)"),
    ("Fog", "雾/吸收(前向)"),
]

# 表 3a: 语义输出。基准落点 = 默认光照 + 延迟(GBuffer)。(name, type, target)
SEMANTIC_OUTPUTS = [
    ("BaseColor", "float3", "RT1.xyz"),
    ("Metallic", "float", "RT1.w"),
    ("Roughness", "float", "RT2.z"),
    ("Normal", "float3", "RT2.xy"),
    ("Emissive", "float3", "RT0.xyz"),
    ("Occlusion", "float", "RT3.x"),
    ("SubSurface", "float", "RT3.w"),
    ("Translucency", "float", "RT1.w(半透明, 4bit)"),
    ("Reflectance", "float", "RT1.w(半透明, 4bit)"),
]
# 互斥项(共用同一 GBuffer 通道)
MUTEX = [
    ("Metallic", "Translucency", "RT1.w"),
    ("Metallic", "Reflectance", "RT1.w"),
]

PARAM_TYPES = ("float", "float2", "float3", "float4")


def _norm_extra(d):
    """规范化 per-pass 附加内容 -> {"main":str, "depth":str, "vertex":str}。"""
    src = d or {}
    return {p: str(src.get(p) or "") for p in MIM.PASSES}


class MaterialAsset(object):
    """材质资产(源)。字段: 两个选项 + 输入注册(preset/engine/param/tex)+ 材质函数(HLSL)。"""

    def __init__(self, name="NewMaterial", lighting_mode="default",
                 shading_type="deferred", template=None, inputs=None,
                 shading_source="", shading_extra=None):
        self.name = name
        self.lighting_mode = lighting_mode
        self.shading_type = shading_type
        self.template = template or {}          # {"pass_template": "deferred_std", "mmtr_path": "MasterMaterial/..."}
        self.inputs = MIM.from_dict(inputs)     # 结构化输入注册(唯一真源)
        self.shading_source = shading_source    # 用户写的材质函数(**纯 HLSL**, 无 `//!`)
        # 每 pass 的“附加内容”(拼在材质源之前的 HLSL: struct/typedef/helper 函数)。
        #   模板**不主动使用**; 由用户在入口函数里调用; 可引用接口作用域内的引擎资源/参数/贴图。
        self.shading_extra = _norm_extra(shading_extra)

    @classmethod
    def new_default(cls, name="NewMaterial"):
        return cls(name=name)

    # ---- 选项 ----
    def combo(self):
        return (self.lighting_mode, self.shading_type)

    def combo_problem(self):
        """组合是否非法。两选项正交, **无非法组合** ⇒ 恒 None。"""
        return None

    def output_specs(self):
        """当前选项下的输出契约。default → 表3a(PBR 语义); custom → 直控输出。"""
        if self.lighting_mode == "custom":
            return list(CUSTOM_OUTPUTS[self.shading_type])
        return list(SEMANTIC_OUTPUTS)

    def post_steps(self):
        """custom 下交给材质的“最终输出前后处理”步骤(default 下由模板/引擎做)。"""
        return list(POST_EXPOSED) if self.lighting_mode == "custom" else []

    def extra_for(self, pass_name):
        """该 pass 的附加内容(拼在材质源之前的 HLSL; 空则 "")。"""
        return self.shading_extra.get(pass_name, "")

    # ---- 校验 ----
    def validate(self):
        """返回 [(level, msg)], level ∈ {'error', 'warn'}。非阻断。"""
        out = []
        if self.lighting_mode not in LIGHTING_MODES:
            out.append(("error", "未知 lighting_mode: %r" % self.lighting_mode))
        if self.shading_type not in SHADING_TYPES:
            out.append(("error", "未知 shading_type: %r" % self.shading_type))
        for (name, typ) in MIM.params(self.inputs):
            if typ not in PARAM_TYPES:
                out.append(("error", "参数 %s 类型未知: %r" % (name, typ)))
        return out

    def is_ok(self):
        return not any(lv == "error" for lv, _ in self.validate())

    # ---- 序列化 ----
    def to_dict(self):
        return {
            "format": FORMAT,
            "name": self.name,
            "lighting_mode": self.lighting_mode,
            "shading_type": self.shading_type,
            "template": dict(self.template),
            "inputs": MIM.to_dict(self.inputs),
            "shading": {"language": "hlsl", "source": self.shading_source,
                        "extra": dict(self.shading_extra)},
        }

    @classmethod
    def from_dict(cls, d):
        return cls(name=d.get("name", "NewMaterial"),
                   lighting_mode=d.get("lighting_mode", "default"),
                   shading_type=d.get("shading_type", "deferred"),
                   template=dict(d.get("template") or {}),
                   inputs=MIM.from_dict(d.get("inputs")),
                   shading_source=(d.get("shading") or {}).get("source", ""),
                   shading_extra=_norm_extra((d.get("shading") or {}).get("extra")))

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

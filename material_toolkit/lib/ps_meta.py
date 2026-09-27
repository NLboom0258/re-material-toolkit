#!/usr/bin/env python3
"""PS 元数据嫁接: 用参考 Deferred PS 的 RDEF/ISGN/OSGN 覆盖我们编译出的 PS 的对应块。

背景(2026-09-27 实机定位): 自建 mmtr 在 DX11 正常、**DX12 下该材质 pass 被静默跳过**
(不崩不报错, 角色区域像素不更新)。根因 = 我们**新编译的 PS** 的**元数据(RDEF/ISGN/OSGN)
与引擎期望的参考 PS 不一致**(DX12 建 PSO 比 DX11 严)。实机关键实验: 参考 blob 的
RDEF/ISGN/OSGN + 我们 PS 的 SHEX/STAT ⇒ **DX12 正常渲染**。

做法: 仅当三块(RDEF/ISGN/OSGN)**尺寸与参考一致**时**原地覆盖**(SHEX/STAT 保持我们的,
最后 finalize 重算指纹)。尺寸不一致(如自定义参数/贴图改变了 RDEF)时**保持原样**
(不改现有行为)。参考元数据由预设(`presets/<ver>/ps_meta.{rdef,isgn,osgn}`)提供,
由 `mmtr_presets.extract()` 从参考 Deferred PS 抽出。
"""
import struct

try:
    from . import mmtr_blobs as B
    from . import mmtr_presets as P
except ImportError:  # 允许脚本直接 import
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import mmtr_blobs as B
    import mmtr_presets as P

META_CHUNKS = ("RDEF", "ISGN", "OSGN")


def _chunk_loc(dxbc, name):
    """返回 (payload 偏移, payload 长度); 无该块返回 None。"""
    if not dxbc or len(dxbc) < 32 or dxbc[:4] != b"DXBC":
        return None
    n = struct.unpack_from("<I", dxbc, 28)[0]
    for i in range(n):
        o = struct.unpack_from("<I", dxbc, 32 + 4 * i)[0]
        if dxbc[o:o + 4] == name.encode():
            return o + 8, struct.unpack_from("<I", dxbc, o + 4)[0]
    return None


def graft(dxbc, meta=None):
    """把参考 PS 的 RDEF/ISGN/OSGN 嫁接进 dxbc。返回 (dxbc, info)。

    info = {"applied": bool, "reason": str, "chunks": [已嫁接的块名]}
    尺寸不一致/缺块/无预设时原样返回(applied=False), 保证不破坏结果。
    """
    if meta is None:
        meta = P.ps_meta()
    if not meta:
        return dxbc, {"applied": False, "reason": "无参考元数据预设", "chunks": []}
    b = bytearray(dxbc)
    done = []
    for nm in META_CHUNKS:
        ref = meta.get(nm)
        if not ref:
            return dxbc, {"applied": False, "reason": "预设缺 %s" % nm, "chunks": []}
        loc = _chunk_loc(dxbc, nm)
        if loc is None:
            return dxbc, {"applied": False, "reason": "PS 无 %s 块" % nm, "chunks": []}
        off, sz = loc
        if sz != len(ref):
            return dxbc, {"applied": False,
                          "reason": "%s 尺寸不同(我们 %d / 参考 %d)" % (nm, sz, len(ref)),
                          "chunks": done}
        b[off:off + sz] = ref
        done.append(nm)
    return B.finalize(bytes(b)), {"applied": True, "reason": "ok", "chunks": done}

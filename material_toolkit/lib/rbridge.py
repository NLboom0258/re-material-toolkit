# -*- coding: utf-8 -*-
"""REF C# 桥的 HTTP 客户端（对应 analysis/runtime_preview_plan.md §10）。

游戏内跑的是 `<游戏根>/reframework/plugins/source/mmtr_bridge.cs`，它用
REFramework.NET 起了个 HttpListener（默认 :8899），暴露：

    GET /ping                  存活
    GET /api                   反射出 REFrameworkNET 的公开 API（排查用）
    GET /probe?what=<...>      探测：materials / params / meshptr ...
    GET /set?go=&mat=&var=&value=    REF setter 路径（float 可用；float3 无效）
    GET /set3?go=&mat=&var=&value=   直写内存路径，float/float3/float4 全支持

本模块只负责“发请求 + 解析 JSON”，不碰 REF 本身。
所有函数在连不上时抛 BridgeError，GUI 侧统一提示。
"""
from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_PORT = 8899
DEFAULT_TIMEOUT = 20.0


class BridgeError(RuntimeError):
    """REF 桥不可用 / 返回错误。"""


def _base(port=DEFAULT_PORT):
    return "http://localhost:%d" % port


def _get(path, params=None, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    url = _base(port) + path
    if params:
        q = {k: v for k, v in params.items() if v is not None and v != ""}
        if q:
            url += "?" + urllib.parse.urlencode(q)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            body = r.read()
    except urllib.error.HTTPError as e:
        raise BridgeError("HTTP %s: %s" % (e.code, e.reason)) from e
    except urllib.error.URLError as e:
        raise BridgeError(
            "连不上 REF 桥（%s）\n"
            "游戏是否在运行？C# 插件是否加载？" % (e.reason,)) from e
    except OSError as e:
        raise BridgeError(str(e)) from e
    try:
        return json.loads(body.decode("utf-8"))
    except ValueError as e:
        raise BridgeError("返回不是 JSON：%s" % body[:200]) from e


def alive(port=DEFAULT_PORT, timeout=3.0):
    """桥是否在线。"""
    try:
        d = _get("/ping", port=port, timeout=timeout)
        return bool(d.get("ok"))
    except BridgeError:
        return False


def ping(port=DEFAULT_PORT):
    return _get("/ping", port=port, timeout=5.0)


def api_dump(port=DEFAULT_PORT):
    """反射出 REFramework.NET 的公开 API（调试用）。"""
    return _get("/api", port=port)


def probe(what, go="", limit=0, port=DEFAULT_PORT, timeout=DEFAULT_TIMEOUT):
    """通用探测入口：what = materials / params / methods / obj ..."""
    return _get("/probe", {"what": what, "go": go, "limit": limit or None},
                port=port, timeout=timeout)


def list_meshes(go="", limit=60, port=DEFAULT_PORT):
    """列出带材质的 mesh。

    返回 [{i, go, matCount, mats[]}] —— i 是组件索引，go 是 GameObject 名。
    同一个 go 可能出现多条（一个模型被拆成多个 mesh 组件），调用方自行分组。
    """
    d = probe("materials", go=go, limit=limit, port=port)
    lst = d.get("list") or {}
    if lst.get("err"):
        raise BridgeError(lst["err"])
    return lst.get("meshes") or []


def identity(go="", limit=40, names=False, port=DEFAULT_PORT):
    """mesh 身份：meshPath / mdf2Path / materialNames / mgrBases。

    工具侧用前两者算 meshHash / mdf2Hash（见 lib/psession.py）；
    mgrBases 用于判断「两个 mesh 挂同一份 mdf2 时是否共用参数数据块」。
    返回 {count, meshes:[{i, go, matCount, addr, meshPath, mdf2Path, ...}], sharedMdf2}
    """
    d = _get("/identity", {"go": go, "limit": limit, "names": 1 if names else 0},
             port=port, timeout=60.0)
    if d.get("err") and not (d.get("meshes") or []):
        raise BridgeError(d["err"])
    return d


def list_params(go, limit=1, port=DEFAULT_PORT):
    """读某个 GameObject 下 mesh 的材质参数。

    返回 {meshes: [{i, go, mats: [{name, varCount, vars: [{j,name,type,value}]}]}]}
    type = 分量数（1=float, 2=float2, 3=float3, 4=float4）；
    float2/3/4 的 value 已是解包后的 "x, y, z" 形式（老版本会是 REFrameworkNET.ValueType）。
    """
    d = probe("params", go=go, limit=limit, port=port, timeout=40.0)
    p = d.get("params") or {}
    if p.get("err"):
        raise BridgeError(p["err"])
    return p


def set_param(go, mat, var, value, idx=None, port=DEFAULT_PORT):
    """写一个参数。value 传字符串：float 用 "1.5"，float3 用 "x,y,z"。

    走 `/set3`（超集）：
      - float   -> REF 原生 setMaterialFloat
      - float3/4 -> 直写引擎内存 + 自动 flush（preFlush 激活 mgr + 版本号自增）

    idx：mesh 组件索引。同名 GameObject 下可能有多个 mesh（如 pl0800 名下的分身/本体/头/发，
    各自 mdf2 不同），不传则写全部同名 mesh。

    返回 {ok, touched, results:[{mesh, matIdx, matName, varIdx, type, before, after,
                                via, preFlush, flush, err?}]}
    其中 via = "setMaterialFloat" / "mem+flush" / "mem-only"（mem-only 表示找不到
    可用来 flush 的 float 参数，画面不会刷新）。
    """
    return _get("/set3", {"go": go, "mat": mat, "var": var, "value": value,
                          "i": idx}, port=port, timeout=40.0)


def apply_slot(go, mat, var, value, idx=None, port=DEFAULT_PORT):
    """set_param 的严格版：只要有一个 mesh 报错就抛异常。"""
    r = set_param(go, mat, var, value, idx=idx, port=port)
    if not r.get("ok"):
        raise BridgeError(r.get("err") or "set 失败：%s" % (r,))
    bad = [x for x in (r.get("results") or []) if x.get("err")]
    if bad:
        raise BridgeError("; ".join("%s: %s" % (x.get("matName"), x["err"]) for x in bad))
    return r


def material(go, action="info", mdf=None, idx=None, keep=None, port=DEFAULT_PORT):
    """独立 mdf2 预览：info / apply / restore / keep。

    apply  需要 mdf（相对 natives/x64、不带 .10 后缀的路径）
    keep   仅 apply 用：是否每帧保持（None = 用服务端默认，即保持）。
           游戏会主动换 mdf2（湿身/燃烧变体），保持后永远以我们指定的为准。
    restore 换回首次 apply 时按 go#i 记录的原始 mdf2，并解除保持
    """
    return _get("/material", {"go": go, "action": action, "mdf": mdf, "i": idx,
                              "keep": None if keep is None else (1 if keep else 0)},
                port=port, timeout=60.0)

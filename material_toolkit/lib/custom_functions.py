"""用户自定义函数库(纯函数, 每函数一文件)。

设计(2026-10-02):
- **存储**: `tools/material_toolkit/functions/<name>.hlsl`, 每文件含**一个顶层函数**(可含同名重载),
  文件基名 = 函数名 ⇒ 跨文件天然不重名; 撞名保存时**自动改名**(并应由调用方告警)。
- **纯函数**: 只吃形参; 不得引用引擎资源/预设输入/材质参数 —— 由"隔离编译"天然挡住
  (检查时只带函数本身, 任何未声明的全局都会编译失败)。
- **依赖**: 函数可互相调用; 调用关系由**源码扫描**确定, 规则(保证稳健):
  去注释 ⇒ 只认"调用语法"`名字(` 且名字**精确等于**某库函数名 ⇒ 排除成员访问(前导 `.`)
  与更长子串。因此注释/字符串里的名字不会误命中。
- **编译检查**: 分 `ps_5_0` / `vs_5_0` 两阶段。**必须合成一个"使用其结果"的调用者**:
  否则 FXC 死代码消除, 阶段限制(如隐式 LOD `Sample` 在 VS 的 X4532)测不出(见 scripts 实验)。
- **可达性**: 从某 pass 入口出发, 递归扫描被调用的库函数 ⇒ 只注入"真的用到"的。

本模块不 import 其它项目模块(只依赖 mmtr_blobs 的 compile_hlsl)。
"""
import hashlib
import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
# tools/material_toolkit/lib -> tools/material_toolkit/functions
FUNCS_DIR = os.path.normpath(os.path.join(_HERE, "..", "functions"))
_EXT = ".hlsl"


def lib_dir():
    """函数库目录(可用 FUNCS_DIR 覆盖, 便于测试)。"""
    return FUNCS_DIR


def ensure_dir():
    d = lib_dir()
    if not os.path.isdir(d):
        os.makedirs(d, exist_ok=True)
    return d


# ---------------------------------------------------------------- 存储
def _path(name):
    return os.path.join(lib_dir(), name + _EXT)


def list_names():
    """库内函数名(文件基名)排序列表。"""
    d = lib_dir()
    if not os.path.isdir(d):
        return []
    out = []
    for fn in os.listdir(d):
        if fn.lower().endswith(_EXT):
            out.append(fn[:-len(_EXT)])
    return sorted(out)


def list_functions():
    """[{name, path, source}]。"""
    out = []
    for n in list_names():
        try:
            with open(_path(n), encoding="utf-8") as f:
                out.append({"name": n, "path": _path(n), "source": f.read()})
        except OSError:
            pass
    return out


def get(name):
    p = _path(name)
    if not os.path.isfile(p):
        return None
    with open(p, encoding="utf-8") as f:
        return f.read()


def save(name, source):
    ensure_dir()
    with open(_path(name), "w", encoding="utf-8") as f:
        f.write(source if source.endswith("\n") else source + "\n")
    _cache.pop(name, None)
    return _path(name)


def delete(name):
    p = _path(name)
    if os.path.isfile(p):
        os.remove(p)
        _cache.pop(name, None)
        return True
    return False


def unique_name(base):
    """不冲突的名字(base 已被占用则加 _2/_3...)。"""
    base = (base or "").strip() or "func"
    if base not in set(list_names()):
        return base
    k = 2
    while ("%s_%d" % (base, k)) in set(list_names()):
        k += 1
    return "%s_%d" % (base, k)


def rename(old, new):
    """改名; new 冲突则自动加后缀。返回实际新名。"""
    new = (new or "").strip() or old
    if new == old:
        return old
    actual = new if new not in set(list_names()) else unique_name(new)
    os.replace(_path(old), _path(actual))
    _cache.pop(old, None)
    return actual


# ---------------------------------------------------------------- 源码扫描
_COMMENT_BLOCK = re.compile(r"/\*.*?\*/", re.S)
_COMMENT_LINE = re.compile(r"//[^\n]*")
# 调用语法: 名字后紧跟 '('; 前导不能是 标识符字符/'.'/'>'(排除成员访问与更长标识符)
_CALL_RE = re.compile(r"(?<![\w.>])([A-Za-z_]\w*)\s*\(")
# 顶层函数定义: 行首 `TYPE NAME(`...`)` `{`
_FUNC_DEF_RE = re.compile(
    r"(?m)^[ \t]*([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*\{")


def strip_comments(src):
    """去 `/* */` 与 `//` 注释(保留换行数量, 避免行号漂移)。"""
    s = _COMMENT_BLOCK.sub(lambda m: "\n" * m.group(0).count("\n"), src or "")
    s = _COMMENT_LINE.sub("", s)
    return s


def _fn_body(src, start):
    """从函数定义的 `{`(start 指向定义起始) 起, 返回函数体文本与结束位置。"""
    i = src.index("{", start)
    d = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            d += 1
        elif src[j] == "}":
            d -= 1
            if d == 0:
                return src[i + 1:j], j + 1
    return src[i + 1:], len(src)


def parse_signature(src):
    """解析**首个**顶层函数 -> {ret, name, params:[{mod,type,name}], body} 或 None。

    参数: `[in|out|inout] <type> <name>`(类型限单 token; 复杂类型暂不支持, 返回时尽力而为)。
    """
    m = _FUNC_DEF_RE.search(src or "")
    if not m:
        return None
    ret, name, params_str = m.group(1), m.group(2), m.group(3)
    body, _ = _fn_body(src, m.start())
    params = []
    for part in _split_top(params_str):
        toks = part.replace("\t", " ").split()
        if not toks:
            continue
        mod = "in"
        if toks[0] in ("in", "out", "inout"):
            mod = toks[0]
            toks = toks[1:]
        if len(toks) < 2:
            continue
        params.append({"mod": mod, "type": toks[0], "name": toks[-1]})
    return {"ret": ret, "name": name, "params": params, "body": body}


def _split_top(s):
    """按顶层逗号切分(忽略括号内逗号)。"""
    out, cur, depth = [], "", 0
    for c in s:
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        if c == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += c
    if cur.strip():
        out.append(cur)
    return out


def calls_in(src, known):
    """src 里**调用**到的 known 函数名集合(去注释 + 精确名 + 调用语法)。"""
    known = set(known)
    if not known:
        return set()
    text = strip_comments(src or "")
    out = set()
    for m in _CALL_RE.finditer(text):
        nm = m.group(1)
        if nm in known:
            out.add(nm)
    return out


def function_calls(name, known=None):
    """某库函数(其函数体)直接调用的库函数集合(排除它自己的名字)。"""
    src = get(name) or ""
    sig = parse_signature(src)
    body = sig["body"] if sig else src
    known = set(list_names()) if known is None else set(known)
    known.discard(name)
    return calls_in(body, known)


def closure(names, resolver=None):
    """依赖闭包: names ∪ 其递归依赖。resolver(name) -> 直接依赖集合。"""
    resolver = resolver or (lambda n: function_calls(n))
    seen, stack = set(), list(names)
    while stack:
        n = stack.pop()
        if n in seen:
            continue
        seen.add(n)
        for d in resolver(n):
            if d not in seen:
                stack.append(d)
    return seen


def topo_order(names):
    """names ∪ 依赖闭包, **依赖在前**(HLSL 须先声明后调用); 确定性(依赖按名排序)。"""
    seen, out = set(), []

    def visit(n):
        if n in seen:
            return
        seen.add(n)
        for d in sorted(function_calls(n)):
            visit(d)
        out.append(n)

    for n in names:
        visit(n)
    return out


def missing(names, known=None):
    """names 里不在库中的(供告警)。"""
    have = set(list_names()) if known is None else set(known)
    return sorted(n for n in names if n not in have)


# ---------------------------------------------------------------- 编译检查
# —— 合成调用者: 结果必须**用于输出**, 否则被 DCE, 阶段限制测不出(scripts/_func_check_exp.py)。
_SCAL = {"float": "0.0", "float2": "float2(0,0)", "float3": "float3(0,0,0)",
         "float4": "float4(0,0,0,0)", "int": "0", "uint": "0", "bool": "false",
         "min16float": "0.0", "half": "0.0"}
_RES = {"Texture1D", "Texture1DArray", "Texture2D", "Texture2DArray", "Texture2DMS",
        "Texture2DMSArray", "Texture3D", "TextureCube", "TextureCubeArray", "Buffer"}
_SMP = {"SamplerState", "SamplerComparisonState"}
ENTRY_SEM = {"ps_5_0": "SV_Target", "vs_5_0": "SV_Position"}


def _use(type_name, var):
    """把某类型的值折成一个 float4(用于强制消费, 抵御 DCE)。"""
    t = type_name
    if t == "float":
        return "float4(%s,0,0,0)" % var
    if t == "float2":
        return "float4(%s,0,0)" % var
    if t == "float3":
        return "float4(%s,1)" % var
    if t == "float4":
        return var
    if t in ("int", "uint", "min16float", "half"):
        return "float4((float)%s,0,0,0)" % var
    if t == "bool":
        return "float4((%s)?1.0:0.0,0,0,0)" % var
    return "float4(0,0,0,0)"


def build_harness(name, source, target):
    """合成"注册函数 + 一个使用其结果的入口"。返回 (harness_src, unsupported:bool)。

    unsupported=True 表示参数含无法合成的类型(如 struct/数组) ⇒ 该阶段只能"尽力"检查。
    """
    sig = parse_signature(source)
    if not sig:
        return None, True
    lines = [source.rstrip(), ""]
    args, outs, locals_ = [], [], []
    unsup = False
    for i, p in enumerate(sig["params"]):
        t, nm, mod = p["type"], p["name"], p["mod"]
        if mod in ("out", "inout"):
            locals_.append("%s _a%d;" % (t, i))   # 须在 main 内(全局不可作 out 实参)
            args.append("_a%d" % i)
            outs.append((t, "_a%d" % i))
        elif t in _RES or t in _SMP:
            lines.append("%s _p%d;" % (t, i))     # 资源做全局
            args.append("_p%d" % i)
        elif t in _SCAL:
            args.append(_SCAL[t])
        else:
            unsup = True
            args.append("(%s)0" % t)          # 尽力而为
    call = "%s(%s)" % (sig["name"], ", ".join(args))
    body = list(locals_)
    if sig["ret"] == "void":
        body.append(call + ";")
        expr = "float4(0,0,0,0)"
        for (t, v) in outs:
            expr += " + " + _use(t, v)
    else:
        body.append("%s _r = %s;" % (sig["ret"], call))
        expr = _use(sig["ret"], "_r")
    lines.append("float4 main() : %s" % ENTRY_SEM[target])
    lines.append("{")
    for b in body:
        lines.append("    " + b)
    lines.append("    return %s;" % expr)
    lines.append("}")
    return "\n".join(lines) + "\n", unsup


_cache = {}      # name -> (content_hash, {"ps":{...},"vs":{...}})


def check(name, source=None):
    """分阶段编译检查 -> {"ps":{"ok","err"},"vs":{"ok","err"},"unsupported":bool}。

    结果按**内容哈希**缓存; 内容一变即失效。
    """
    src = get(name) if source is None else source
    if src is None:
        return {"ps": {"ok": False, "err": "函数不存在"}, "vs": {"ok": False, "err": "函数不存在"},
                "unsupported": False}
    h = hashlib.md5(src.encode("utf-8")).hexdigest()
    hit = _cache.get(name)
    if hit and hit[0] == h:
        return hit[1]
    from . import mmtr_blobs as B
    res = {"unsupported": False}
    for target in ("ps_5_0", "vs_5_0"):
        hs, unsup = build_harness(name, src, target)
        res["unsupported"] = res["unsupported"] or unsup
        if hs is None:
            res[_st(target)] = {"ok": False, "err": "无法解析函数签名"}
            continue
        _dxbc, err = B.compile_hlsl(hs, "main", target, name="%s.hlsl" % name)
        res[_st(target)] = {"ok": err is None, "err": err or ""}
    _cache[name] = (h, res)
    return res


def cached(name):
    """已缓存的检查结果(内容未变); 未检查/已失效返回 None(供列表展示, 不触发编译)。"""
    src = get(name)
    if src is None:
        return None
    h = hashlib.md5(src.encode("utf-8")).hexdigest()
    hit = _cache.get(name)
    return hit[1] if (hit and hit[0] == h) else None


def _st(target):
    return "ps" if target.startswith("ps") else "vs"


def injection_text(names, stage, pass_name=None):
    """函数名列表 -> 注入文本(依赖在前); 逐函数按 stage 校验, 不通过即抛错。

    生成端与 GUI 共用; 错误信息指明"哪个函数在哪个阶段不过"(若它由别的函数依赖引入=依赖问题)。
    """
    parts = []
    for n in topo_order(names):
        s = get(n)
        if s is None:
            raise ValueError("导入的自定义函数不存在: %s%s"
                             % (n, (" (pass '%s')" % pass_name) if pass_name else ""))
        st = check(n)
        if not st[stage]["ok"]:
            _e = (st[stage]["err"] or "").strip().splitlines()
            raise ValueError(
                "自定义函数 %s 在 %s 阶段检查不通过%s(若它由别的函数依赖引入, 即为依赖问题):\n  %s"
                % (n, stage, (" (pass '%s')" % pass_name) if pass_name else "",
                   _e[0] if _e else "?"))
        parts.append(s.rstrip())
    return ("\n\n".join(parts) + "\n\n") if parts else ""


def clear_cache():
    _cache.clear()

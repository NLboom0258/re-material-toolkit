#!/usr/bin/env python3
"""回归: 实时预览副本模型（一个原 mdf2 多具名副本）。

覆盖：
  1. 同一 (mesh, mdf2) 下新建两个副本 → 同目录、不同名
  2. 副本名校验（非法字符 / 保留名 / 首尾空格）
  3. 每次产出 rev+1（bump_copy_rel 是 save_params / apply_attrs 共用的路径逻辑）
  4. sessions.json 登记 / 查询 / 最近使用（edit 字段）
  5. 旧版 sessions.json 被忽略（开发期不做结构兼容）
  6. 路径解析只认新目录结构
"""
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from material_toolkit.lib import psession as ps  # noqa: E402

root = tempfile.mkdtemp(prefix="mmtr_copy_test_")
nat = os.path.join(root, "natives", "x64")
os.makedirs(os.path.join(nat, "character", "player"), exist_ok=True)

# 最小“mdf2”：create_copy 只做字节拷贝，不需要可解析内容
base_rel = "character/player/test.mdf2"
with open(os.path.join(nat, base_rel + ".10"), "wb") as f:
    f.write(b"MDf2" + b"\0" * 64)

mh = ps.mesh_hash("character/player/test.mesh")
dh = ps.mdf2_hash(base_rel)

# 1) 两个副本：同目录、不同名
a = ps.create_copy(root, mh, dh, base_rel, name="默认")
b = ps.create_copy(root, mh, dh, base_rel, name="高光版")
assert a["rel"] != b["rel"]
assert os.path.dirname(a["rel"]) == os.path.dirname(b["rel"])
# 磁盘文件名只能是 ASCII（非 ASCII 交给引擎会被按 ANSI 处理）
assert a["rel"].isascii() and b["rel"].isascii()
assert a["rel"].endswith("/%s_v1.mdf2" % ps.copy_file_stem("默认")), a["rel"]
assert b["rel"].endswith("/%s_v1.mdf2" % ps.copy_file_stem("高光版")), b["rel"]
assert os.path.exists(ps.rel_to_abs(root, a["rel"]))
assert os.path.exists(ps.rel_to_abs(root, b["rel"]))

# 2) 名字校验
for bad in ("", " a", "a/b", "a:b", "con", "x."):
    assert ps.copy_name_error(bad), bad
assert ps.copy_name_error("默认") == ""
assert ps.copy_name_error("A_v2") == ""      # 名字自带 _v2 也合法（解析取最后一个后缀）
assert ps.copy_name_error("默" * 40)          # 编码后超长 → 拒绝
assert ps.copy_file_stem("默认").isascii()

# 3) rev 递增；磁盘上已存在则继续跳过
n1 = ps.bump_copy_rel(root, a["rel"])
assert n1["rev"] == 2 and n1["name"] == "默认"
assert n1["rel"].endswith("/%s_v2.mdf2" % ps.copy_file_stem("默认"))
with open(ps.rel_to_abs(root, n1["rel"]), "wb") as f:
    f.write(b"x")
n2 = ps.bump_copy_rel(root, a["rel"])
assert n2["rev"] == 3

# 4) 登记 / 查询 / 最近使用
idx = ps._empty()
ps.upsert_object(idx, mh, "character/player/test.mesh", "test", 1)
ps.upsert_mdf2(idx, mh, dh, base_rel, base_rel)
ps.upsert_copy(idx, mh, dh, a["name"], a["rel"], a["rev"])
ps.upsert_copy(idx, mh, dh, b["name"], b["rel"], b["rev"])
names = {c["name"] for c in ps.list_copies(idx, mh, dh)}
assert names == {"默认", "高光版"}, names
assert ps.get_copy(idx, mh, dh, name="高光版")["rel"] == b["rel"]
assert ps.get_copy(idx, mh, dh, rel=b["rel"])["name"] == "高光版"
assert ps.get_mdf2(idx, mh, dh)["edit"] == b["rel"]      # 最后一次 upsert 为最近使用
# current=False 不改“最近使用”（编译后批量重建所有副本时用）
ps.upsert_copy(idx, mh, dh, a["name"], a["rel"], a["rev"], current=False)
assert ps.get_mdf2(idx, mh, dh)["edit"] == b["rel"]
# 更新 mdf2 基本信息不能丢 copies
ps.upsert_mdf2(idx, mh, dh, base_rel, base_rel)
assert len(ps.list_copies(idx, mh, dh)) == 2
ps.save_index(root, idx)
idx2 = ps.load_index(root)
assert {c["name"] for c in ps.list_copies(idx2, mh, dh)} == names

# 5) 旧版 sessions.json 直接忽略
p = os.path.join(ps.temp_root(root), ps.SESSIONS)
with open(p, "w", encoding="utf-8") as f:
    json.dump({"version": 1, "objects": {"x": {}}}, f)
assert ps.load_index(root)["objects"] == {}

# 6) 路径解析
assert ps.parse_temp_rel(a["rel"]) == {"kind": "edit", "mh": mh, "dh": dh,
                                       "name": "默认", "rev": 1}
# 名字自带 _v<数字> 时按最后一个后缀拆
c = ps.create_copy(root, mh, dh, base_rel, name="A_v2")
p = ps.parse_temp_rel(c["rel"])
assert p["name"] == "A_v2" and p["rev"] == 1, p
assert ps.parse_temp_rel(
    "materialTemp/edit/mh/dh/默认_p20260101_000000.mdf2") is None

# 7) 删除登记：edit 自动改指到剩下的副本
ps.upsert_copy(idx2, mh, dh, b["name"], b["rel"], b["rev"])   # b 为最近使用
removed = ps.remove_copy(idx2, mh, dh, b["name"])
assert removed and removed["name"] == "高光版"
assert ps.get_mdf2(idx2, mh, dh)["edit"] == a["rel"]
assert ps.get_copy(idx2, mh, dh, name="高光版") is None
assert ps.remove_copy(idx2, mh, dh, "不存在") is None
ps.remove_copy(idx2, mh, dh, a["name"])
assert ps.get_mdf2(idx2, mh, dh)["edit"] == ""

# 8) 重命名：登记 key/rel/rev 同步，重名报错
c = ps.create_copy(root, mh, dh, base_rel, name="旧名")
ps.upsert_copy(idx2, mh, dh, c["name"], c["rel"], c["rev"])
assert ps.get_mdf2(idx2, mh, dh)["edit"] == c["rel"]
new_rel = ps.new_copy_rel(mh, dh, "新名", c["rev"])
ps.rename_copy(idx2, mh, dh, "旧名", "新名", new_rel)
assert ps.get_copy(idx2, mh, dh, name="新名")["rel"] == new_rel
assert ps.get_copy(idx2, mh, dh, name="旧名") is None
assert ps.get_mdf2(idx2, mh, dh)["edit"] == new_rel      # 原本是当前 → 跟着改
ps.upsert_copy(idx2, mh, dh, "另一个", new_rel, 1, current=False)
try:
    ps.rename_copy(idx2, mh, dh, "新名", "另一个", new_rel)
    raise AssertionError("重名应当报错")
except ValueError:
    pass

shutil.rmtree(root, ignore_errors=True)
print("test_preview_copies: OK")

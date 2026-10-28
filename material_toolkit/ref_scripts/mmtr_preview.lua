-- ============================================================================
-- mmtr_preview.lua —— RE Engine 材质热重载（配合 material_toolkit preview）
--
-- 工作方式
--   工具侧 `material_toolkit preview` 生成一对**全新文件名**的 mmtr+mdf2，并写
--   一条索引：<游戏根>/natives/x64/preview_index.txt
--   本脚本轮询该文件，ver 变了就自动加载那组材质 —— 不用按键、不用重载脚本。
--
-- 为什么每次都换文件名
--   同路径 sdk.create_resource 恒返回缓存对象（改磁盘内容无效）；只有全新路径才会
--   真正走一次加载。详见 analysis/runtime_preview_plan.md §6。
--
-- 引擎会刷回材质（角色状态/动画更新时），所以替换后要在 BeginRendering 每帧重设。
--
-- 热键（调试用）：
--   F5  重新读索引并应用（索引没变则无操作）
--   F7  列出命中目标的 Mesh
--   F8  恢复默认 mdf2（CONFIG.fallback_mdf）
--
-- 日志全英文：REF 的 ImGui 字体不含中文，中文会显示成 "?"。
-- ============================================================================

local CONFIG = {
    -- 预览总开关：**默认关** —— 不勾选时脚本完全不碰任何材质（只是待在那里）。
    -- 避免“装了脚本就被默认劫持材质”这种意外。
    enable = false,
    -- 索引候选路径。REF 的 io 是沙盒的（EMV 源码里的官方说明）：
    --   “Access files in the 'REFramework\data\' folder.
    --    Start with '$natives\' to access files in the natives folder.”
    -- 且 **禁止 '..'**（会直接抛 “does not allow access to parent directories”，不是返回 nil）。
    index_candidates = {
        "$natives/x64/materialTemp/index.txt",
        "$natives\\x64\\materialTemp\\index.txt",
        "materialTemp/index.txt",                      -- 相对 reframework/data
        "reframework/data/materialTemp/index.txt",
    },
    -- 每 N 帧检查一次索引
    poll_frames = 30,
    -- 发现新版本是否自动应用
    auto_apply = true,
    -- 索引不存在 / 按 F8 时回到这个 mdf2
    fallback_mdf = "character/player/pl0800_vergilpl/pl0800_body/pl0800.mdf2",
    -- 命中前缀（可多个）。必须同时覆盖“还没预览”和“已经套上预览”两种状态：
    -- 前者 mesh 材质是原 mdf2，后者变成 materialTemp/pl0800_pXXX.mdf2。
    fallback_match = {
        "character/player/pl0800_vergilpl/pl0800_body/pl0800",
        "materialtemp/pl0800",
    },
}

local TAG = "[mmtr_preview]"
local lines = {}
local MAX_LINES = 40

-- 当前"保持"的替换：引擎只在角色状态/动画更新时刷回，所以渲染前每帧重设一次。
-- 必须在 reload 之前声明，否则 reload 里的赋值会落到全局变量上。
local keep = nil     -- { res=..., hits={...}, tag="..." }
local tick = 0
local last_ver = nil
local last_index_match = CONFIG.fallback_match
local idx_path = nil          -- 已经探测到的可用索引路径
local last_index_errors = {}  -- 上次探测的失败原因（启动自检时打印）

local function say(s)
    s = tostring(s)
    log.info(TAG .. " " .. s)
    lines[#lines + 1] = s
    while #lines > MAX_LINES do table.remove(lines, 1) end
end

-- 资源/对象的 ToString() 形如 "via.render.MeshMaterialResource[path]"
local function res_path(res)
    if not res or type(res.call) ~= "function" then return nil end
    local ok, s = pcall(res.call, res, "ToString()")
    if not ok or type(s) ~= "string" then return nil end
    local p = s:match("%[([^%]]+)%]")
    return (p or s):lower()
end

local function scene()
    -- via.SceneManager 是 native singleton(userdata)，不能 sm:call(...)；
    -- 必须走 sdk.call_native_func(obj, type_def, name)
    local sm = sdk.get_native_singleton("via.SceneManager")
    local td = sdk.find_type_definition("via.SceneManager")
    if not sm or not td then return nil end
    local ok, sc = pcall(sdk.call_native_func, sm, td, "get_CurrentScene")
    if ok and sc then return sc end
    return nil
end

local function all_meshes()
    local sc = scene()
    if not sc then return {}, "no CurrentScene" end
    local td = sdk.typeof("via.render.Mesh")
    if not td then return {}, "no typeof(via.render.Mesh)" end
    local ok, arr = pcall(sc.call, sc, "findComponents(System.Type)", td)
    if not ok or not arr then return {}, "findComponents failed" end

    local out = {}
    local okc, c = pcall(arr.call, arr, "get_Count")
    if okc and type(c) == "number" and c > 0 then
        for i = 0, c - 1 do
            local oki, m = pcall(function() return arr[i] end)
            if oki and m then out[#out + 1] = m end
        end
        if #out > 0 then return out, string.format("System.Array count=%d", c) end
    end
    local oke, elems = pcall(function() return arr:get_elements() end)
    if oke and type(elems) == "table" then
        for _, m in ipairs(elems) do out[#out + 1] = m end
        return out, string.format("get_elements #=%d", #out)
    end
    return out, "unknown array container"
end

local function mat_path(m)
    local ok, res = pcall(m.call, m, "get_Material")
    if not ok or not res then return nil end
    return res_path(res)
end

local function split_match(s)
    if type(s) == "table" then return s end
    local out = {}
    for part in tostring(s or ""):gmatch("[^|]+") do
        part = part:match("^%s*(.-)%s*$")
        if part ~= "" then out[#out + 1] = part:lower() end
    end
    return out
end

local function matches(p, prefixes)
    if not p then return false end
    for _, s in ipairs(split_match(prefixes)) do
        if p:find(s, 1, true) then return true end
    end
    return false
end

local function dump_meshes(prefix)
    prefix = prefix or CONFIG.fallback_match
    local ms, how = all_meshes()
    say(string.format("dump: %d Mesh  (%s)  prefixes=%s", #ms, how,
                      table.concat(split_match(prefix), " | ")))
    local hit = 0
    for _, m in ipairs(ms) do
        local p = mat_path(m)
        if matches(p, prefix) then
            local nm = "?"
            local okg, go = pcall(m.call, m, "get_GameObject")
            if okg and go then
                local okn, g = pcall(go.call, go, "get_Name")
                if okn and g then nm = tostring(g) end
            end
            say(string.format("  HIT %-28s %s", nm, p))
            hit = hit + 1
            if hit >= 10 then break end
        end
    end
    if hit == 0 then say("  no mesh matches the prefix") end
end

-- ---------------------------------------------------------------------------
-- 探测：列出目标 mesh 的**材质列表 + 参数**。
--
-- 关键：这些 API 直接挂在 `via.render.Mesh` 组件上（EMV 就是这么用的），
-- 不需要去解析 mdf2 资源对象（那是个 *Holder，里面没有托管字段）。
--   get_MaterialNum / getMaterialName(id)
--   getMaterialVariableNum(id) / getMaterialVariableName(id, j) / getMaterialVariableType(id, j)
--   getMaterialFloat / getMaterialFloat4 / getMaterialBool
--   getMaterialTextureNum / getMaterialTexture / getMaterialTextureName
-- id 一律 **0-based**。
--
-- **type 语义 = 分量数**（实机确认）：
--   1 = float   2 = float2   3 = float3   4 = float4
--   （EMV 只判断 1 和 4，其余全走 getMaterialBool —— 这会把 float3 读成 bool，不要照抄）
-- ---------------------------------------------------------------------------
local function probe_mdf2()
    local ms = all_meshes()
    local mesh, path
    for _, m in ipairs(ms) do
        local p = mat_path(m)
        if matches(p, last_index_match) then
            mesh, path = m, p
            break
        end
    end
    if not mesh then say("probe: no target mesh (press F6 to dump)"); return end

    say("=== probe mdf2 ===")
    say("path: " .. tostring(path))

    local okn, n = pcall(mesh.call, mesh, "get_MaterialNum")
    if not okn or type(n) ~= "number" then
        say("probe: get_MaterialNum failed: " .. tostring(n))
        return
    end
    say("materials: " .. n)

    local sample = nil        -- 第一个有参数的材质，用作变量示例
    for i = 0, n - 1 do
        local okm, name = pcall(mesh.call, mesh, "getMaterialName", i)
        local okv, vnum = pcall(mesh.call, mesh, "getMaterialVariableNum", i)
        local okt, tnum = pcall(mesh.call, mesh, "getMaterialTextureNum", i)
        say(string.format("  [%2d] %-14s vars=%-3s tex=%s", i,
                          okm and tostring(name) or "?",
                          okv and tostring(vnum) or "?",
                          okt and tostring(tnum) or "?"))
        if not sample and okv and type(vnum) == "number" and vnum > 0 then
            sample = { i = i, name = name, n = vnum }
        end
    end

    if sample then
        say(string.format("-- vars of [%d] %s --", sample.i, tostring(sample.name)))
        local shown = math.min(sample.n, 12)
        for j = 0, shown - 1 do
            local ojn, vname = pcall(mesh.call, mesh, "getMaterialVariableName", sample.i, j)
            local ojt, vtype = pcall(mesh.call, mesh, "getMaterialVariableType", sample.i, j)
            if ojn and vname then
                say(string.format("   %-26s type=%s", tostring(vname),
                                  ojt and tostring(vtype) or "?"))
            end
        end
        if sample.n > shown then say("   ...(" .. (sample.n - shown) .. " more)") end
    end
    say("=== probe end ===")
end

-- ---------------------------------------------------------------------------
-- 资源加载
--   DMC5 上 sdk.create_resource 返回的是 managed resource，但 set_Material 期望的
--   是 ** Holder**（EMV 的做法：拿裸地址 → create_instance(type.."Holder") →
--   .ctor() → write_qword(0x10, addr)）。传裸资源虽然不报错但没有效果。
-- ---------------------------------------------------------------------------
local holders = {}

local function load_res(path, rtype)
    local raw = sdk.create_resource(rtype, path)
    if not raw then return nil, "create_resource -> nil" end

    local kind = type(raw)
    local addr = nil
    if kind == "number" then
        addr = raw
    else
        local oka, a = pcall(function() return raw:get_address() end)
        if oka and type(a) == "number" then addr = a end
        local okr, r = pcall(function() return raw:add_ref() end)
        if okr and r then raw = r end
    end

    if kind == "number" or addr then
        local hname = rtype .. "Holder"
        if holders[path] and holders[path].__addr == addr then
            return holders[path].obj, "cached Holder"
        end
        local h = sdk.create_instance(hname, true)
        if h then
            local okh, hr = pcall(function() return h:add_ref() end)
            if okh and hr then h = hr end
            pcall(function() h:call(".ctor()") end)
            if addr then pcall(function() h:write_qword(0x10, addr) end) end
            holders[path] = { obj = h, __addr = addr }
            return h, "Holder(addr=" .. tostring(addr) .. ")"
        end
        return raw, "raw(kind=" .. kind .. ") Holder failed"
    end
    return raw, "managed resource"
end

-- 把某个 mdf2 应用到所有命中 prefix 的 mesh 上
local function apply(mdf, prefix, tag)
    if not mdf or mdf == "" then say(tostring(tag) .. ": empty path"); return end
    local target = mdf:lower()

    local ms, how = all_meshes()
    if #ms == 0 then say("  no mesh: " .. how); return end

    local hits = {}
    for _, m in ipairs(ms) do
        if matches(mat_path(m), prefix) then hits[#hits + 1] = m end
    end
    if #hits == 0 then
        say(string.format("  mesh=%d  hits=0  (prefixes=%s) -- press F7", #ms,
                          table.concat(split_match(prefix), " | ")))
        return
    end

    local res, how2 = load_res(target, "via.render.MeshMaterialResource")
    if not res then say("  load failed: " .. tostring(how2)); return end

    local okn, err, hit_ok = 0, nil, {}
    for _, m in ipairs(hits) do
        local ok, e = pcall(m.call, m, "set_Material", res)
        if ok then
            okn = okn + 1
            hit_ok[#hit_ok + 1] = m
        elseif not err then
            err = tostring(e)
        end
    end
    say(string.format("[%s] %s  hits=%d ok=%d  (%s)", tostring(tag), target, #hits, okn, how2))
    if err then say("  err: " .. err) end
    if #hit_ok > 0 then
        keep = { res = res, hits = hit_ok, tag = tostring(tag) }
    end
end

-- ---------------------------------------------------------------------------
-- 索引文件（key=value，一行一条）
-- ---------------------------------------------------------------------------
local function read_index()
    local order = {}
    if idx_path then order[#order + 1] = idx_path end
    for _, p in ipairs(CONFIG.index_candidates) do
        if p ~= idx_path then order[#order + 1] = p end
    end
    local errs = {}
    for _, p in ipairs(order) do
        -- 必须 pcall: 含 '..' 的路径会抛 “parent directories” 而不是返回 nil
        local ok, f = pcall(io.open, p, "r")
        if ok and f then
            local t = {}
            for line in f:lines() do
                local k, v = line:match("^%s*([%w_]+)%s*=%s*(.-)%s*$")
                if k then t[k] = v end
            end
            f:close()
            if idx_path ~= p then
                idx_path = p
                say("index resolved: " .. p)
            end
            return t
        end
        errs[#errs + 1] = ok and (p .. " (missing)") or (p .. " [" .. tostring(f) .. "]")
    end
    last_index_errors = errs
    return nil
end

local function apply_index(idx, tag)
    if not idx or not idx.mdf or idx.mdf == "" then
        say(tostring(tag) .. ": index has no mdf")
        return
    end
    last_index_match = split_match(idx.match or CONFIG.fallback_match)
    apply(idx.mdf, last_index_match, tag .. ":" .. tostring(idx.ver))
end

-- ---------------------------- 输入 ----------------------------------------
local KB = nil
do
    local ok, kb = pcall(function() return via.hid.KeyboardKey end)
    if ok then KB = kb end
end

local function kb_device()
    local sm = sdk.get_native_singleton("via.hid.Keyboard")
    local td = sdk.find_type_definition("via.hid.Keyboard")
    if not sm or not td then return nil end
    local ok, dev = pcall(sdk.call_native_func, sm, td, "get_Device")
    return ok and dev or nil
end

local prev = {}
local pending = nil

local function edge(name, enum_key)
    if not KB or enum_key == nil then return false end
    local kb = kb_device()
    if not kb then return false end
    local ok, down = pcall(kb.call, kb, "isDown", enum_key)
    down = (ok and down) and true or false
    local was = prev[name] or false
    prev[name] = down
    return down and not was
end

local function key_enum(name)
    if not KB then return nil end
    local ok, v = pcall(function() return KB[name] end)
    return ok and v or nil
end

re.on_application_entry("BeginRendering", function()
    if not keep then return end
    local n = 0
    for _, m in ipairs(keep.hits) do
        local ok = pcall(m.call, m, "set_Material", keep.res)
        if ok then n = n + 1 end
    end
    if n == 0 then
        say("KEEP cleared: targets invalid (level change?)")
        keep = nil
    end
end)

re.on_application_entry("UpdateHID", function()
    if edge("f5", key_enum("F5")) then pending = "f5" end
    if edge("f6", key_enum("F6")) then pending = "f6" end
    if edge("f7", key_enum("F7")) then pending = "f7" end
end)

re.on_frame(function()
    -- 轮询索引（自动应用）—— 必须在启用状态
    tick = tick + 1
    if CONFIG.enable and CONFIG.auto_apply and (tick % CONFIG.poll_frames) == 0 then
        local idx = read_index()
        if idx and idx.ver and idx.ver ~= last_ver then
            last_ver = idx.ver
            apply_index(idx, "auto")
        end
    end

    local p = pending
    if not p then return end
    pending = nil
    if p == "f5" then
        if not CONFIG.enable then say("F5: preview disabled (tick 'enable preview')"); return end
        local idx = read_index()
        if not idx then say("F5: no readable index (see candidates at load)"); return end
        last_ver = idx.ver
        apply_index(idx, "F5")
    elseif p == "f6" then
        dump_meshes(last_index_match)
    elseif p == "f7" then
        keep = nil
        apply(CONFIG.fallback_mdf, CONFIG.fallback_match, "F7-restore")
        keep = nil      -- 恢复默认后不再压制，交给引擎
        say("restored default mdf2, KEEP off")
    end
end)

re.on_draw_ui(function()
    if not imgui.tree_node("MMTR Preview") then return end
    local en_ch, en = imgui.checkbox("enable preview", CONFIG.enable)
    if en_ch then
        CONFIG.enable = en
        if en then
            local idx = read_index()
            if idx then
                last_ver = idx.ver
                apply_index(idx, "enable")
            else
                say("enabled, but no readable index")
            end
        else
            -- 关掉时停止压制，让引擎下次刷新自己恢复
            keep = nil
            say("preview disabled (KEEP off)")
        end
    end
    imgui.text("index: " .. tostring(idx_path or "(unresolved)"))
    imgui.text(string.format("tick=%d  ver=%s  auto=%s", tick, tostring(last_ver),
                             CONFIG.auto_apply and "on" or "off"))
    if imgui.button("Reapply (F5)") then
        if not CONFIG.enable then
            say("preview disabled (tick 'enable preview')")
        else
            local idx = read_index()
            if idx then last_ver = idx.ver; apply_index(idx, "btn")
            else say("no readable index") end
        end
    end
    imgui.same_line()
    if imgui.button("Dump (F6)") then dump_meshes(last_index_match) end
    imgui.same_line()
    if imgui.button("Restore (F7)") then
        keep = nil
        apply(CONFIG.fallback_mdf, CONFIG.fallback_match, "btn-restore")
        keep = nil
    end
    imgui.same_line()
    if imgui.button("Probe mdf2") then probe_mdf2() end
    local ch, v = imgui.checkbox("auto apply", CONFIG.auto_apply)
    if ch then CONFIG.auto_apply = v end
    if keep then imgui.text("KEEP active: " .. tostring(keep.tag)) else imgui.text("KEEP off") end
    imgui.separator()
    for _, l in ipairs(lines) do imgui.text(l) end
    imgui.tree_pop()
end)

say(string.format("loaded. %d index candidates", #CONFIG.index_candidates))
say("hotkey: F5 reapply / F6 dump / F7 restore "
    .. (key_enum("F5") and "(enum ok)" or "(enum MISSING -> use menu)"))

-- 启动自检：只报告索引能否读到；**不自动应用**（需要用户在面板里勾上 enable preview）
local _idx = read_index()
if _idx then
    say(string.format("index OK: %s  ver=%s", tostring(idx_path), tostring(_idx.ver)))
    last_index_match = split_match(_idx.match or CONFIG.fallback_match)
    if CONFIG.enable then
        apply_index(_idx, "boot")
        last_ver = _idx.ver
    else
        say("preview DISABLED (tick 'enable preview' in the panel to start)")
    end
else
    say("index UNREADABLE —— 试过以下路径:")
    for _, e in ipairs(last_index_errors) do say("    " .. e) end
end

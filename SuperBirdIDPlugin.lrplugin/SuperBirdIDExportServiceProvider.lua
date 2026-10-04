local LrTasks = import 'LrTasks'
local LrApplication = import 'LrApplication'
local LrDialogs = import 'LrDialogs'
local LrLogger = import 'LrLogger'
local LrHttp = import 'LrHttp'
local LrPathUtils = import 'LrPathUtils'
local LrFileUtils = import 'LrFileUtils'
local LrView = import 'LrView'
local LrBinding = import 'LrBinding'
local LrFunctionContext = import 'LrFunctionContext'
local LrL10n
local LOC

-- 安全导入 LrL10n（某些 Lightroom 环境可能不支持）
local status, result = pcall(import, 'LrL10n')
if status then
    LrL10n = result
    LOC = LrL10n.loc
else
    -- 降级：从 key 中提取默认文本
    LOC = function(key, ...)
        if type(key) == "string" then
            local val = key:match("=(.*)")
            if val then return val end
        end
        return key
    end
end

-- 版本信息
local VERSION = "v4.0.2"
local PLUGIN_NAME = LOC "$$$/SuperBirdID/PluginName=SuperPicky BirdID"

local myLogger = LrLogger( 'SuperBirdIDExportServiceProvider' )
myLogger:enable( "logfile" )

-- Binding helper
local bind = LrView.bind

-- Export service provider definition
local exportServiceProvider = {}

-- Required functions for Lightroom SDK
exportServiceProvider.supportsIncrementalPublish = false
exportServiceProvider.canExportVideo = false
exportServiceProvider.exportPresetDestination = "temp"

-- 不需要导出图片，只需获取原图路径
exportServiceProvider.allowFileFormats = nil
exportServiceProvider.allowColorSpaces = nil
exportServiceProvider.hideSections = { 'exportLocation', 'fileNaming', 'fileSettings', 'imageSettings', 'outputSharpening', 'metadata', 'watermarking' }

exportServiceProvider.exportPresetFields = {
    { key = 'apiUrl', default = "http://127.0.0.1:5156" },
    { key = 'topK', default = 3 },
    { key = 'useYolo', default = true },
    { key = 'useGps', default = true },
    { key = 'writeExif', default = true },
}

-- Unicode转义解码辅助函数
local function decodeUnicodeEscape(str)
    if not str then return str end

    -- 将 \uXXXX 转换为 UTF-8
    local function unicodeToUtf8(code)
        code = tonumber(code, 16)
        if code < 0x80 then
            return string.char(code)
        elseif code < 0x800 then
            return string.char(
                0xC0 + math.floor(code / 0x40),
                0x80 + (code % 0x40)
            )
        elseif code < 0x10000 then
            return string.char(
                0xE0 + math.floor(code / 0x1000),
                0x80 + (math.floor(code / 0x40) % 0x40),
                0x80 + (code % 0x40)
            )
        end
        return "?"
    end

    -- 替换所有 \uXXXX 序列
    return str:gsub("\\u(%x%x%x%x)", unicodeToUtf8)
end

-- 简单的JSON解析函数 (支持多个结果)
local function parseJSON(jsonString)
    local result = {}

    -- 提取 success 字段
    local success = string.match(jsonString, '"success"%s*:%s*([^,}]+)')
    if success then
        result.success = (success == "true")
    end

    -- 提取 results 数组中的所有结果
    local resultsBlock = string.match(jsonString, '"results"%s*:%s*%[(.-)%]')
    if resultsBlock then
        result.results = {}

        -- 使用更灵活的模式匹配每个结果对象
        for itemBlock in string.gmatch(resultsBlock, '{([^{}]*)}') do
            local item = {}

            -- 提取字段并解码Unicode
            local cn_name_raw = string.match(itemBlock, '"cn_name"%s*:%s*"([^"]*)"')
            local en_name_raw = string.match(itemBlock, '"en_name"%s*:%s*"([^"]*)"')
            local display_name_raw = string.match(itemBlock, '"display_name"%s*:%s*"([^"]*)"')
            local sci_name_raw = string.match(itemBlock, '"scientific_name"%s*:%s*"([^"]*)"')
            local desc_raw = string.match(itemBlock, '"description"%s*:%s*"([^"]*)"')

            item.cn_name = decodeUnicodeEscape(cn_name_raw)
            item.en_name = decodeUnicodeEscape(en_name_raw)
            item.display_name = decodeUnicodeEscape(display_name_raw) or item.cn_name  -- 兼容旧版本
            item.scientific_name = decodeUnicodeEscape(sci_name_raw)
            item.description = decodeUnicodeEscape(desc_raw)

            local confStr = string.match(itemBlock, '"confidence"%s*:%s*([%d%.]+)')
            item.confidence = confStr and tonumber(confStr) or 0

            local rankStr = string.match(itemBlock, '"rank"%s*:%s*(%d+)')
            item.rank = rankStr and tonumber(rankStr) or (#result.results + 1)

            if item.display_name or item.cn_name then
                table.insert(result.results, item)
            end
        end
    end

    -- 提取 yolo_info (可能包含中文)
    local yolo_raw = string.match(jsonString, '"yolo_info"%s*:%s*"([^"]*)"')
    result.yolo_info = decodeUnicodeEscape(yolo_raw)

    -- 提取 gps_info
    local gpsBlock = string.match(jsonString, '"gps_info"%s*:%s*{(.-)}')
    if gpsBlock then
        result.gps_info = {}

        local lat = string.match(gpsBlock, '"latitude"%s*:%s*([%d%.%-]+)')
        local lon = string.match(gpsBlock, '"longitude"%s*:%s*([%d%.%-]+)')

        result.gps_info.latitude = lat and tonumber(lat) or nil
        result.gps_info.longitude = lon and tonumber(lon) or nil

        local region_raw = string.match(gpsBlock, '"region"%s*:%s*"([^"]*)"')
        local info_raw = string.match(gpsBlock, '"info"%s*:%s*"([^"]*)"')

        result.gps_info.region = decodeUnicodeEscape(region_raw)
        result.gps_info.info = decodeUnicodeEscape(info_raw)
    end

    -- 提取错误信息
    local error_raw = string.match(jsonString, '"error"%s*:%s*"([^"]*)"')
    result.error = decodeUnicodeEscape(error_raw)

    local warning_raw = string.match(jsonString, '"warning"%s*:%s*"([^"]*)"')
    result.warning = decodeUnicodeEscape(warning_raw)

    return result
end

-- 简单的JSON编码函数
local function encodeJSON(tbl)
    local parts = {}
    for k, v in pairs(tbl) do
        local key = '"' .. tostring(k) .. '"'
        local value
        if type(v) == "string" then
            value = '"' .. v:gsub('"', '\\"'):gsub('\\', '\\\\') .. '"'
        elseif type(v) == "boolean" then
            value = tostring(v)
        elseif type(v) == "number" then
            value = tostring(v)
        else
            value = '"' .. tostring(v) .. '"'
        end
        table.insert(parts, key .. ":" .. value)
    end
    return "{" .. table.concat(parts, ",") .. "}"
end

-- 识别单张照片并返回结果
local function recognizeSinglePhoto(photo, apiUrl, topK, useYolo, useGps)
    local LrHttp = import 'LrHttp'
    local LrFileUtils = import 'LrFileUtils'

    local photoPath = photo:getRawMetadata("path")
    local photoName = photo:getFormattedMetadata("fileName") or "Unknown"

    -- 检查文件是否存在
    if not LrFileUtils.exists(photoPath) then
        return {
            success = false,
            error = LOC("$$$/SuperBirdID/Error/FileNotFound=File not found: ^1", photoName),
            photoName = photoName
        }
    end

    -- 构建API请求
    local requestBody = encodeJSON({
        image_path = photoPath,
        use_yolo = useYolo,
        use_gps = useGps,
        top_k = topK
    })

    -- 调用API
    local response, headers = LrHttp.post(
        apiUrl .. "/recognize",
        requestBody,
        {
            { field = "Content-Type", value = "application/json" }
        }
    )

    if not response then
        return {
            success = false,
            error = LOC("$$$/SuperBirdID/Error/ApiFailed=API call failed"),
            photoName = photoName
        }
    end

    -- 解析响应
    local result = parseJSON(response)
    result.photoName = photoName
    result.photo = photo

    return result
end

-- 保存识别结果到照片元数据
-- 写入 Title (鸟名) 和 Caption (描述)
-- V4.0.5: 使用服务端返回的 display_name，不再自行判断语言
--   服务端已根据 SuperPicky 主程序语言设置选择中文/英文名称
local function saveRecognitionResult(photo, displayName, description)
    local catalog = import('LrApplication').activeCatalog()

    catalog:withWriteAccessDo(LOC "$$$/SuperBirdID/Undo/SaveResult=Save Bird Recognition Result", function()
        -- Title: 使用服务端已选好的 display_name
        photo:setRawMetadata("title", displayName)
        -- Caption: 写入描述（如有）
        if description and description ~= "" then
            photo:setRawMetadata("caption", description)
        end
    end)
end


-- 显示结果选择对话框（美化版）
local function showResultSelectionDialog(results, photoName)
    local LrView = import 'LrView'
    local LrDialogs = import 'LrDialogs'
    local LrFunctionContext = import 'LrFunctionContext'
    local LrBinding = import 'LrBinding'
    local LrColor = import 'LrColor'

    local selectedIndex = nil

    LrFunctionContext.callWithContext("resultSelectionDialog", function(context)
        local f = LrView.osFactory()
        local props = LrBinding.makePropertyTable(context)

        -- 默认选中第一个
        props.selectedBird = 1

        -- 创建候选鸟种的 radio button 列表
        local candidateViews = {}

        for i, bird in ipairs(results) do
            local confidence = bird.confidence or 0
            local displayName = bird.display_name or bird.cn_name or "Unknown"
            local enName = bird.en_name or ""
            
            -- 置信度颜色提示
            local confColor
            if confidence >= 50 then
                confColor = LrColor(0.2, 0.7, 0.3)  -- 绿色 - 高置信度
            elseif confidence >= 20 then
                confColor = LrColor(0.8, 0.6, 0.1)  -- 橙色 - 中置信度
            else
                confColor = LrColor(0.6, 0.6, 0.6)  -- 灰色 - 低置信度
            end

            -- 每个候选项包含：radio button + 详细信息
            candidateViews[#candidateViews + 1] = f:row {
                spacing = f:control_spacing(),
                
                f:radio_button {
                    title = "",
                    value = bind { key = 'selectedBird', object = props },
                    checked_value = i,
                    width = 20,
                },
                
                f:column {
                    spacing = 2,
                    
                    f:row {
                        f:static_text {
                            title = string.format("%d.", i),
                            font = "<system/bold>",
                            width = 20,
                        },
                        f:static_text {
                            title = displayName,
                            font = "<system/bold>",
                        },
                        f:static_text {
                            title = string.format("  %.1f%%", confidence),
                            text_color = confColor,
                            font = "<system/bold>",
                        },
                    },
                    
                    f:static_text {
                        title = "    " .. enName,
                        text_color = LrColor(0.5, 0.5, 0.5),
                        font = "<system/small>",
                    },
                },
            }
            
            -- 添加分隔线（最后一个候选后不加）
            if i < #results then
                candidateViews[#candidateViews + 1] = f:spacer { height = 6 }
                candidateViews[#candidateViews + 1] = f:separator { fill_horizontal = 1 }
                candidateViews[#candidateViews + 1] = f:spacer { height = 6 }
            end
        end

        -- 添加"跳过"选项（与候选分开）
        candidateViews[#candidateViews + 1] = f:spacer { height = 12 }
        candidateViews[#candidateViews + 1] = f:separator { fill_horizontal = 1 }
        candidateViews[#candidateViews + 1] = f:spacer { height = 8 }
        
        candidateViews[#candidateViews + 1] = f:row {
            f:radio_button {
                title = "",
                value = bind { key = 'selectedBird', object = props },
                checked_value = 0,
                width = 20,
            },
            f:static_text {
                title = LOC "$$$/SuperBirdID/Dialog/Skip=Skip this photo",
                text_color = LrColor(0.5, 0.5, 0.5),
            },
        }

        -- 构建候选列表容器
        local candidatesGroup = f:column(candidateViews)

        -- 构建完整对话框内容
        local dialogContent = f:column {
            spacing = f:control_spacing(),
            fill_horizontal = 1,

            -- 宽度占位符（确保对话框足够宽）
            f:spacer { width = 350 },

            -- 文件名标题
            f:row {
                f:static_text {
                    title = LOC("$$$/SuperBirdID/Dialog/ResultsFor=Results for ^1", photoName),
                    font = "<system/bold>",
                },
            },
            
            f:spacer { height = 8 },
            f:separator { fill_horizontal = 1 },
            f:spacer { height = 12 },

            -- 候选列表（不需要提示文字，用户自然理解）
            candidatesGroup,
            
            f:spacer { height = 8 },
        }

        -- 显示对话框（设置更大的宽度）
        local dialogResult = LrDialogs.presentModalDialog({
            title = PLUGIN_NAME,
            contents = dialogContent,
            actionVerb = LOC "$$$/SuperBirdID/Dialog/WriteExif=Write to EXIF",
            cancelVerb = LOC "$$$/SuperBirdID/Dialog/Cancel=Cancel",
            resizable = true,
        })

        if dialogResult == "ok" then
            selectedIndex = props.selectedBird
        else
            selectedIndex = nil
        end
    end)

    return selectedIndex
end



-- UI配置
function exportServiceProvider.sectionsForTopOfDialog( f, propertyTable )
    local LrView = import 'LrView'
    local bind = LrView.bind

    return {
        {
            title = LOC "$$$/SuperBirdID/Export/Config/Title=SuperPicky BirdID API Config",


            synopsis = bind { key = 'apiUrl', object = propertyTable },

            f:row {
                spacing = f:control_spacing(),

                f:static_text {
                    title = LOC "$$$/SuperBirdID/Export/Config/ApiLabel=API Address:",
                    width = LrView.share "label_width",
                },

                f:edit_field {
                    value = bind 'apiUrl',
                    width_in_chars = 30,
                    tooltip = LOC "$$$/SuperBirdID/Export/Config/ApiTooltip=API Server Address, Default: http://127.0.0.1:5156",
                },
            },

            f:row {
                spacing = f:control_spacing(),

                f:static_text {
                    title = LOC "$$$/SuperBirdID/Export/Config/TopKLabel=Result Count:",
                    width = LrView.share "label_width",
                },

                f:slider {
                    value = bind 'topK',
                    min = 1,
                    max = 10,
                    integral = true,
                    width = 200,
                },

                f:static_text {
                    title = bind 'topK',
                },
            },

            f:row {
                spacing = f:control_spacing(),

                f:checkbox {
                    title = LOC "$$$/SuperBirdID/Export/Config/UseYolo=Enable YOLO Detection",
                    value = bind 'useYolo',
                    tooltip = LOC "$$$/SuperBirdID/Export/Config/UseYoloTooltip=Use YOLO model to pre-detect bird location",
                },
            },

            f:row {
                spacing = f:control_spacing(),

                f:checkbox {
                    title = LOC "$$$/SuperBirdID/Export/Config/UseGps=Enable GPS Location",
                    value = bind 'useGps',
                    tooltip = LOC "$$$/SuperBirdID/Export/Config/UseGpsTooltip=Use GPS info from EXIF to assist identification",
                },
            },

            f:row {
                spacing = f:control_spacing(),

                f:checkbox {
                    title = LOC "$$$/SuperBirdID/Export/Config/WriteExif=Auto Write EXIF",
                    value = bind 'writeExif',
                    checked_value = true,
                    unchecked_value = false,
                    tooltip = LOC "$$$/SuperBirdID/Export/Config/WriteExifTooltip=Automatically write bird name to Title after identification",
                },
            },

            f:row {
                spacing = f:control_spacing(),

                f:static_text {
                    title = LOC "$$$/SuperBirdID/Export/Config/Hint=Hint: Please start SuperPicky app first",
                    text_color = import 'LrColor'( 0.5, 0.5, 0.5 ),
                },
            },
        },
    }
end

-- 主要处理函数
function exportServiceProvider.processRenderedPhotos( functionContext, exportContext )
    myLogger:info( PLUGIN_NAME .. " 识别启动 - " .. VERSION )

    local exportSettings = exportContext.propertyTable
    local apiUrl = exportSettings.apiUrl or "http://127.0.0.1:5156"
    local topK = exportSettings.topK or 3
    local useYolo = exportSettings.useYolo
    if useYolo == nil then useYolo = true end
    local useGps = exportSettings.useGps
    if useGps == nil then useGps = true end
    local writeExif = exportSettings.writeExif
    if writeExif == nil then writeExif = true end

    -- 计算照片数量
    local nPhotos = exportContext.nPhotos or 1
    myLogger:info( "待处理照片数: " .. nPhotos )

    -- 限制只处理一张照片
    if nPhotos == 0 then
        LrDialogs.message(PLUGIN_NAME,
            LOC "$$$/SuperBirdID/Message/SelectOne=Please select one photo first",
            "error")
        return
    end

    -- 检查API服务是否可用
    myLogger:info( "检查API服务: " .. apiUrl .. "/health" )
    local healthCheck, headers = LrHttp.get(apiUrl .. "/health")

    if not healthCheck or string.find(healthCheck, '"status"%s*:%s*"ok"') == nil then
        LrDialogs.message(PLUGIN_NAME,
            LOC "$$$/SuperBirdID/Message/ApiError=Cannot connect to SuperPicky API Service\n\nPlease ensure:\n1. SuperPicky App is running\n2. BirdID API Service is started",
            "error")
        return
    end

    myLogger:info( "API服务正常，开始识别..." )

    -- 处理单张照片
    for i, rendition in exportContext:renditions() do
        -- 更新进度
        local progressTitle
        if nPhotos > 1 then
            progressTitle = string.format("Identifying Birds (%d/%d)", i, nPhotos)
        else
            progressTitle = "Identifying Bird"
        end
        exportContext:configureProgress { title = progressTitle }

        local photo = rendition.photo
        local result = recognizeSinglePhoto(photo, apiUrl, topK, useYolo, useGps)

        if result.success and result.results and #result.results > 0 then
            myLogger:info( "识别成功，候选数: " .. #result.results )

            local selectedIndex
            if nPhotos > 1 then
                -- 批量模式：自动选择第一项
                selectedIndex = 1
            else
                -- 单张模式：GPS 回退时先提示用户
                if result.warning then
                    LrDialogs.message(
                        PLUGIN_NAME,
                        result.warning,
                        "info"
                    )
                end
                -- 单张模式：显示选择对话框
                selectedIndex = showResultSelectionDialog(result.results, result.photoName)
            end

            if selectedIndex and selectedIndex > 0 then
                -- 用户选择了一个结果
                local selectedBird = result.results[selectedIndex]
                local displayName = selectedBird.display_name or selectedBird.cn_name or selectedBird.en_name or "Unknown"

                if writeExif then
                    saveRecognitionResult(photo, displayName, selectedBird.description)
                    myLogger:info( "已写入: " .. displayName )
                end
            elseif selectedIndex == 0 then
                -- 用户选择跳过
                myLogger:info( "用户选择跳过此照片" )
            else
                -- 用户点击取消
                myLogger:info( "用户取消操作" )
            end

        else
            local errorMsg
            if result.error then
                errorMsg = result.error
            elseif result.success then
                -- success=true 但 results=[] 表示 YOLO 未检测到鸟
                errorMsg = LOC "$$$/SuperBirdID/Dialog/NoBirdDetected=No bird detected in this photo"
            else
                errorMsg = "Unknown error"
            end
            myLogger:info( "识别失败: " .. errorMsg )

            -- 仅单张模式显示错误弹窗
            if nPhotos == 1 then
                local failMsg = LOC("$$$/SuperBirdID/Message/IdentifyFail=Cannot identify bird in this photo\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nError:\n^1\n\n━━━━━━━━━━━━━━━━━━━━━━━━━━\n\nPossible reasons:\n• No bird or bird unclear\n• File corrupted or unsupported\n• Model not loaded", errorMsg)
                LrDialogs.message(PLUGIN_NAME .. " - " .. LOC("$$$/SuperBirdID/Dialog/IdentifyFailTitle=Identify Failed"), failMsg, "error")
            end
        end
    end

    myLogger:info( PLUGIN_NAME .. " 识别处理完成" )
end

return exportServiceProvider

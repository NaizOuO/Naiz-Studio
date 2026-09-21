# 用電腦上已經安裝的 Office 轉檔（Word、PowerPoint、Excel），由 Naiz Studio 的文件轉檔工具呼叫。
# 一次處理整批，同一個 Office 程式只開一次，比一個檔案開一次快很多。
#
# 用法：powershell -File office.ps1 -Jobs jobs.json
# jobs.json：[{"source": "...", "output": "...", "format": "pdf"}, ...]
# 每個工作回報一行：OK<tab>編號   或   ERR<tab>編號<tab>訊息
param([Parameter(Mandatory = $true)][string]$Jobs)

$ErrorActionPreference = "Stop"
# ConvertFrom-Json 在 Windows PowerShell 5.1 會把整個陣列當成一個物件送出來，
# 直接用 @() 包起來只會得到一個元素，逐項列舉才會真的拆開
$items = @()
foreach ($item in (Get-Content -Raw -Encoding UTF8 $Jobs | ConvertFrom-Json)) { $items += $item }

$wordFormats  = @{ "pdf" = 17; "docx" = 16; "doc" = 0; "rtf" = 6; "txt" = 7; "html" = 8; "odt" = 23 }
$pptFormats   = @{ "pdf" = 32; "pptx" = 24; "ppt" = 1; "odp" = 35 }
$excelFormats = @{ "pdf" = 0;  "xlsx" = 51; "xls" = 56; "csv" = 6; "ods" = 60; "html" = 44; "txt" = 42 }
$pptExts   = @(".ppt", ".pptx", ".pps", ".ppsx", ".odp")
$excelExts = @(".xls", ".xlsx", ".xlsm", ".csv", ".ods")

$apps = @{}

function Get-App($name) {
    if (-not $apps.ContainsKey($name)) {
        $app = New-Object -ComObject "$name.Application"
        try { $app.DisplayAlerts = 0 } catch {}
        try { $app.AutomationSecurity = 3 } catch {}    # 不執行文件裡的巨集
        if ($name -ne "PowerPoint") { try { $app.Visible = $false } catch {} }
        $apps[$name] = $app
    }
    return $apps[$name]
}

function Convert-One($source, $output, $format) {
    $extension = [System.IO.Path]::GetExtension($source).ToLower()
    if ($pptExts -contains $extension) {
        if (-not $pptFormats.ContainsKey($format)) { throw "PowerPoint 不支援轉成 $format" }
        $app = Get-App "PowerPoint"
        $presentation = $app.Presentations.Open($source, $true, $false, $false)   # 唯讀、不當範本、不顯示
        try { $presentation.SaveAs($output, $pptFormats[$format]) } finally { $presentation.Close() }
    }
    elseif ($excelExts -contains $extension) {
        if (-not $excelFormats.ContainsKey($format)) { throw "Excel 不支援轉成 $format" }
        $app = Get-App "Excel"
        $book = $app.Workbooks.Open($source, 0, $true)          # 不更新連結、唯讀
        try {
            if ($format -eq "pdf") { $book.ExportAsFixedFormat(0, $output) }
            else { $book.SaveAs($output, $excelFormats[$format]) }
        } finally { $book.Close($false) }
    }
    else {
        if (-not $wordFormats.ContainsKey($format)) { throw "Word 不支援轉成 $format" }
        $app = Get-App "Word"
        $document = $app.Documents.Open($source, $false, $true)  # 不詢問轉換、唯讀
        try { $document.SaveAs([ref]$output, [ref]$wordFormats[$format]) } finally { $document.Close($false) }
    }
    if (-not (Test-Path $output)) { throw "轉檔後找不到輸出檔案" }
}

try {
    for ($i = 0; $i -lt $items.Count; $i++) {
        $item = $items[$i]
        try {
            Convert-One ([System.IO.Path]::GetFullPath($item.source)) ([System.IO.Path]::GetFullPath($item.output)) $item.format
            "OK`t$i"
        } catch {
            $message = $_.Exception.Message -replace "`r|`n", " "
            "ERR`t$i`t$message"
        }
    }
} finally {
    foreach ($app in $apps.Values) {
        try { $app.Quit() } catch {}
        try { [System.Runtime.InteropServices.Marshal]::ReleaseComObject($app) | Out-Null } catch {}
    }
}

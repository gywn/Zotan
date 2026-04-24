<#
.SYNOPSIS
    创建 Windows Explorer 右键上下文菜单，使用 WSL 运行 Zotan。

.DESCRIPTION
    此脚本用于：
    - 通过 WSL 直接运行 Zotan（利用 WSL 储存的 TOML 文件）
    - 创建右键上下文菜单项
    - 支持在文件夹上右键和文件夹背景上右键两种方式

.PARAMETER Remove
    移除上下文菜单项

.EXAMPLE
    .\setup-explorer-context.ps1 -Remove
    # 移除上下文菜单项

.NOTES
    需要安装并运行 WSL (Debian distribution)。
    Zotan 的配置文件应从以下位置读取：
    - ./.zotan/config.toml — 项目特定配置
    - $HOME/.zotan/config.toml — 用户级配置
    如遇问题，请以管理员身份运行 PowerShell。
#>

param(
    [switch]$Remove
)

$ErrorActionPreference = "Stop"

# WSL 配置（硬编码）
$WSLDistribution = "Debian"
$ZotanPath = "/home/mujin/.ve3/bin/zotan"

# 注册表路径
$FolderShellPath = "Registry::HKEY_CURRENT_USER\Software\Classes\Directory\shell\Zotan"
$FolderBgShellPath = "Registry::HKEY_CURRENT_USER\Software\Classes\Directory\Background\shell\Zotan"

$MenuName = "使用 Zotan 打开"

# 输出颜色
function Write-Success { param($msg) Write-Host $msg -ForegroundColor Green }
function Write-Info { param($msg) Write-Host $msg -ForegroundColor Cyan }
function Write-Warning { param($msg) Write-Host $msg -ForegroundColor Yellow }
function Write-Error { param($msg) Write-Host $msg -ForegroundColor Red }

# 辅助函数
function New-ContextMenuEntry {
    param(
        [string]$Path,
        [string]$Command
    )

    # 创建主键
    if (-not (Test-Path $Path)) {
        New-Item -Path $Path -Force | Out-Null
    }

    # 设置显示名称和图标
    Set-ItemProperty -Path $Path -Name "(default)" -Value $MenuName
    Set-ItemProperty -Path $Path -Name "Icon" -Value "imageres.dll,83"

    # 创建命令子键
    $commandPath = "$Path\command"
    if (-not (Test-Path $commandPath)) {
        New-Item -Path $commandPath -Force | Out-Null
    }
    Set-ItemProperty -Path $commandPath -Name "(default)" -Value $Command

    Write-Success "已创建上下文菜单：$Path"
}

function Remove-ContextMenuEntry {
    param([string]$Path)

    if (Test-Path $Path) {
        Remove-Item -Path $Path -Recurse -Force
        Write-Success "已移除上下文菜单：$Path"
    }
}

# 显示菜单
function Show-Menu {
    Clear-Host
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Cyan
    Write-Host "   Zotan Explorer 上下文菜单设置" -ForegroundColor Cyan
    Write-Host "======================================" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "请选择操作:" -ForegroundColor White
    Write-Host ""
    Write-Host "  [1] 安装 - 添加右键菜单" -ForegroundColor Green
    Write-Host "  [2] 移除 - 删除右键菜单" -ForegroundColor Yellow
    Write-Host "  [3] 退出" -ForegroundColor Gray
    Write-Host ""
    Write-Host "======================================" -ForegroundColor Cyan
    Write-Host ""
}

# 获取用户选择
function Get-MenuChoice {
    Show-Menu
    $choice = Read-Host "请输入选项 [1-3]"
    return $choice
}

# 处理安装
function Invoke-Installation {
    Write-Info "`n=== 创建上下文菜单项 ==="

    # 在文件夹上右键（单文件夹参数）
    $folderShellCommand = "wsl.exe --distribution $WSLDistribution --exec $ZotanPath --workspace %1"
    
    # 在文件夹背景上右键（目录参数 %V）
    $folderBgShellCommand = "wsl.exe --distribution $WSLDistribution --exec $ZotanPath --workspace %V"

    # 创建上下文菜单项
    New-ContextMenuEntry -Path $FolderShellPath -Command $folderShellCommand
    New-ContextMenuEntry -Path $FolderBgShellPath -Command $folderBgShellCommand

    Write-Success "`n=== 设置完成 ==="
    Write-Info "上下文菜单 '$MenuName' 已添加！"
    Write-Info ""
    Write-Info "使用方法："
    Write-Info "  1. 右键点击任意文件夹 -> '使用 Zotan 打开'"
    Write-Info "  2. 右键点击文件夹内部空白处 -> '使用 Zotan 打开'"
    Write-Info ""
    Write-Info "移除方法：运行脚本并选择 [2] 移除"

    Read-Host "`n按回车键退出"
    exit 0
}

# 处理移除
function Invoke-Removal {
    Write-Info "正在移除上下文菜单项..."

    Remove-ContextMenuEntry -Path $FolderShellPath
    Remove-ContextMenuEntry -Path $FolderBgShellPath

    Write-Success "`n上下文菜单已成功移除！"
    Write-Info "请重启资源管理器或重新登录以使更改生效。"

    Read-Host "`n按回车键退出"
    exit 0
}

# 主逻辑 - 如果传入了 -Remove 参数则直接执行移除，否则显示菜单
if ($Remove) {
    Invoke-Removal
} else {
    # 显示菜单并获取用户选择
    while ($true) {
        $choice = Get-MenuChoice

        switch ($choice) {
            "1" {
                Invoke-Installation
                break
            }
            "2" {
                Invoke-Removal
                break
            }
            "3" {
                Write-Info "已退出"
                exit 0
            }
            default {
                Write-Error "无效选项，请重新选择"
                Start-Sleep -Seconds 2
            }
        }
    }
}

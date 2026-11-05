@echo off
REM ArcGIS Agent Addin 编译脚本
REM 使用项目本地的 .NET SDK

echo ==========================================
echo 编译 ArcGIS Agent Addin
echo ==========================================

cd /d "%~dp0"

if not exist ".dotnet\dotnet.exe" (
    echo 错误: 找不到本地 .NET SDK
    echo 请确保 .dotnet\dotnet.exe 存在
    exit /b 1
)

echo.
echo [1] 检查 .NET SDK 版本...
.dotnet\dotnet.exe --version

echo.
echo [2] 编译 ArcGISAgent...
cd addin\ArcGISAgent
..\..\\.dotnet\dotnet.exe build -c Release

if %ERRORLEVEL% EQU 0 (
    echo.
    echo ==========================================
    echo ✓ 编译成功！
    echo ==========================================
    echo.
    echo 下一步：
    echo 1. 完全关闭 ArcGIS Pro
    echo 2. 检查任务管理器，确保没有 ArcGISPro.exe 进程
    echo 3. 重新启动 ArcGIS Pro
    echo 4. 打开 Agent 面板测试
    echo.
    pause
    exit /b 0
) else (
    echo.
    echo ==========================================
    echo ✗ 编译失败
    echo ==========================================
    pause
    exit /b 1
)

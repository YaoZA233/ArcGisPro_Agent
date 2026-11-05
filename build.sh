#!/bin/bash
# ArcGIS Agent Addin 编译脚本
# 使用项目本地的 .NET SDK

echo "=========================================="
echo "编译 ArcGIS Agent Addin"
echo "=========================================="

# 切换到项目根目录
cd "$(dirname "$0")"

# 检查本地 dotnet
if [ ! -f ".dotnet/dotnet.exe" ]; then
    echo "错误: 找不到本地 .NET SDK"
    echo "请确保 .dotnet/dotnet.exe 存在"
    exit 1
fi

echo ""
echo "[1] 检查 .NET SDK 版本..."
./.dotnet/dotnet.exe --version

echo ""
echo "[2] 编译 ArcGISAgent..."
cd addin/ArcGISAgent
../../.dotnet/dotnet.exe build -c Release

if [ $? -eq 0 ]; then
    echo ""
    echo "=========================================="
    echo "✓ 编译成功！"
    echo "=========================================="
    echo ""
    echo "下一步："
    echo "1. 完全关闭 ArcGIS Pro"
    echo "2. 检查任务管理器，确保没有 ArcGISPro.exe 进程"
    echo "3. 重新启动 ArcGIS Pro"
    echo "4. 打开 Agent 面板测试"
    echo ""
    exit 0
else
    echo ""
    echo "=========================================="
    echo "✗ 编译失败"
    echo "=========================================="
    exit 1
fi

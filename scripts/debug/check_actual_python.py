"""验证 Python 路径 - 从 C# 调用的实际 Python"""
import sys
import os

print("=" * 60)
print("当前 Python 信息")
print("=" * 60)
print(f"可执行文件: {sys.executable}")
print(f"版本: {sys.version}")
print(f"是否是 Pro Python: {'arcgispro-py3' in sys.executable.lower()}")

# 检查是否能找到 arcpy
try:
    import arcpy
    print("\n✓ arcpy 可以导入")
    print(f"ArcGIS 版本: {arcpy.GetInstallInfo().get('Version')}")
except ImportError as e:
    print(f"\n✗ arcpy 无法导入: {e}")
    print("这说明使用的不是 Pro Python!")

print("=" * 60)

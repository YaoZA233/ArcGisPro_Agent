"""调试脚本：检查 RPC 进程环境变量"""
import os
import sys

print("=" * 60)
print("当前进程环境变量检查")
print("=" * 60)

# 关键的 Pro 环境变量
pro_vars = [
    "ESRI_SOFTWARE_CLASS",
    "ARCGISHOME",
    "ARCGIS_PRODUCT_NAME",
    "ARCGIS_VERSION",
    "PYTHONHOME",
    "CONDA_PREFIX",
]

print("\nPro 相关环境变量:")
found = False
for var in pro_vars:
    value = os.environ.get(var)
    if value:
        print(f"  [X] {var} = {value}")
        found = True
    else:
        print(f"  [OK] {var} = <未设置>")

if not found:
    print("\n[OK] 环境干净！")
else:
    print("\n[警告] 发现 Pro 环境变量，可能导致问题！")

# 显示 Python 路径
print(f"\nPython 可执行文件: {sys.executable}")
print(f"Python 版本: {sys.version}")
print(f"进程 PID: {os.getpid()}")

# 显示所有环境变量（调试用）
print("\n所有环境变量（前20个）:")
for i, (key, value) in enumerate(sorted(os.environ.items())[:20]):
    print(f"  {key} = {value[:80] if len(value) <= 80 else value[:77] + '...'}")

print("\n" + "=" * 60)

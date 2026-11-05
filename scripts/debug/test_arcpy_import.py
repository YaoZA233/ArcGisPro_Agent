"""测试：在 Pro 运行时能否 import arcpy"""
import os
import sys
import subprocess
import time

print("=" * 60)
print("测试在 Pro 运行时 import arcpy")
print("=" * 60)

# 检查是否在 Pro 环境中
in_pro = bool(os.environ.get("ESRI_SOFTWARE_CLASS"))
print(f"\n当前是否在 Pro 环境: {in_pro}")

if in_pro:
    print("警告: 当前在 Pro 环境中运行")
    print("请关闭所有 ArcGIS Pro 实例后再运行此脚本")
    print("或者从独立的命令行窗口运行")
    sys.exit(1)

# 查找 Pro 的 Python
pro_python = None
for path in [
    r"C:\Program Files\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe",
    r"C:\Program Files (x86)\ArcGIS\Pro\bin\Python\envs\arcgispro-py3\python.exe",
]:
    if os.path.exists(path):
        pro_python = path
        break

if not pro_python:
    print("\n错误: 找不到 ArcGIS Pro Python")
    print("请手动指定 Pro Python 路径")
    sys.exit(1)

print(f"\nPro Python: {pro_python}")

# 测试脚本
test_script = """
import os
import sys
import time

# 显示环境变量
print("ESRI_SOFTWARE_CLASS:", os.environ.get("ESRI_SOFTWARE_CLASS"))
print("ARCGISHOME:", os.environ.get("ARCGISHOME"))

# 尝试导入 arcpy
print("开始导入 arcpy...")
sys.stdout.flush()

start = time.time()
try:
    import arcpy
    elapsed = time.time() - start
    print(f"导入成功: {elapsed:.2f} 秒")
    print(f"arcpy 版本: {arcpy.GetInstallInfo().get('Version')}")
except Exception as e:
    elapsed = time.time() - start
    print(f"导入失败: {elapsed:.2f} 秒")
    print(f"错误: {e}")
"""

print("\n" + "=" * 60)
print("测试 1: 带有 Pro 环境变量（模拟问题场景）")
print("=" * 60)

env_polluted = os.environ.copy()
env_polluted["ESRI_SOFTWARE_CLASS"] = "Pro"
env_polluted["ARCGISHOME"] = r"C:\Program Files\ArcGIS\Pro"

print("\n启动测试进程（30 秒超时）...")
start = time.time()

try:
    result = subprocess.run(
        [pro_python, "-c", test_script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env_polluted
    )
    elapsed = time.time() - start

    print(f"\n进程完成，耗时: {elapsed:.2f} 秒")
    print("\n--- 输出 ---")
    print(result.stdout)
    if result.stderr:
        print("\n--- 错误 ---")
        print(result.stderr)

except subprocess.TimeoutExpired:
    elapsed = time.time() - start
    print(f"\n[!!] 进程超时 ({elapsed:.1f} 秒)")
    print("这证实了问题：带有 ESRI_SOFTWARE_CLASS 时 import arcpy 会阻塞!")

print("\n" + "=" * 60)
print("测试 2: 清理环境变量（修复后场景）")
print("=" * 60)

env_clean = os.environ.copy()
env_clean.pop("ESRI_SOFTWARE_CLASS", None)
env_clean.pop("ARCGISHOME", None)
env_clean.pop("ARCGIS_PRODUCT_NAME", None)
env_clean.pop("ARCGIS_VERSION", None)

print("\n启动测试进程（30 秒超时）...")
start = time.time()

try:
    result = subprocess.run(
        [pro_python, "-c", test_script],
        capture_output=True,
        text=True,
        timeout=30,
        env=env_clean
    )
    elapsed = time.time() - start

    print(f"\n进程完成，耗时: {elapsed:.2f} 秒")
    print("\n--- 输出 ---")
    print(result.stdout)
    if result.stderr:
        print("\n--- 错误 ---")
        print(result.stderr)

    if elapsed < 20:
        print(f"\n[OK] 清理环境变量后正常! ({elapsed:.1f} 秒)")
    else:
        print(f"\n[!] 虽然成功但较慢 ({elapsed:.1f} 秒)")

except subprocess.TimeoutExpired:
    elapsed = time.time() - start
    print(f"\n[!!] 进程仍然超时 ({elapsed:.1f} 秒)")
    print("这说明还有其他问题!")

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)

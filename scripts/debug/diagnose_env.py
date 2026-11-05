"""诊断 ArcGIS Pro 环境变量和 arcpy 导入性能"""
import os
import sys
import time
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def main():
    print("=" * 60)
    print("ArcGIS Agent 环境诊断")
    print("=" * 60)

    # 检查关键环境变量
    print("\n[1] 关键环境变量检查:")
    critical_vars = [
        "ESRI_SOFTWARE_CLASS",
        "ARCGISHOME",
        "ARCGIS_PRODUCT_NAME",
        "ARCGIS_VERSION",
        "PYTHONHOME",
        "CONDA_PREFIX"
    ]

    found_pro_vars = []
    for var in critical_vars:
        value = os.environ.get(var)
        if value:
            print(f"  ✗ {var} = {value}")
            found_pro_vars.append(var)
        else:
            print(f"  ✓ {var} = <未设置>")

    if found_pro_vars:
        print(f"\n  ⚠️  警告: 发现 {len(found_pro_vars)} 个 Pro 环境变量，可能导致授权握手阻塞")
    else:
        print("\n  ✓ 环境干净，未发现 Pro 环境变量")

    # 测试 arcpy 导入时间
    print("\n[2] 测试 arcpy 导入性能:")
    print("  开始导入 arcpy...")

    start = time.monotonic()
    try:
        import arcpy
        elapsed = time.monotonic() - start
        print(f"  ✓ arcpy 导入成功，耗时: {elapsed:.2f}s")

        if elapsed > 30:
            print(f"  ⚠️  警告: 导入时间过长 ({elapsed:.1f}s)，可能存在授权握手问题")
        elif elapsed > 10:
            print(f"  ⚠️  注意: 导入时间较长 ({elapsed:.1f}s)，属于正常冷启动范围")
        else:
            print(f"  ✓ 导入速度正常")

        # 获取 ArcGIS 信息
        info = arcpy.GetInstallInfo()
        print(f"\n[3] ArcGIS 安装信息:")
        print(f"  版本: {info.get('Version')}")
        print(f"  构建号: {info.get('BuildNumber')}")
        print(f"  产品: {arcpy.ProductInfo()}")
        print(f"  安装目录: {info.get('InstallDir')}")

    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"  ✗ arcpy 导入失败，耗时: {elapsed:.2f}s")
        print(f"  错误: {type(exc).__name__}: {exc}")
        return 1

    # Python 环境信息
    print(f"\n[4] Python 环境:")
    print(f"  可执行文件: {sys.executable}")
    print(f"  版本: {sys.version.split()[0]}")
    print(f"  进程 PID: {os.getpid()}")

    print("\n" + "=" * 60)
    print("诊断完成")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())

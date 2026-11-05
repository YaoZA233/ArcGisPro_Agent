"""简化的环境诊断脚本 - 解决中文编码问题"""
import os
import sys
import time
from pathlib import Path

# 设置控制台编码
if sys.platform == 'win32':
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'strict')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'strict')

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))


def main():
    print("=" * 60)
    print("ArcGIS Agent 环境诊断（简化版）")
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
            print(f"  [X] {var} = {value}")
            found_pro_vars.append(var)
        else:
            print(f"  [OK] {var} = <未设置>")

    if found_pro_vars:
        print(f"\n  警告: 发现 {len(found_pro_vars)} 个 Pro 环境变量")
        print("  这可能导致授权握手阻塞!")
    else:
        print("\n  [OK] 环境干净，未发现 Pro 环境变量")

    # 测试 arcpy 导入时间
    print("\n[2] 测试 arcpy 导入性能:")
    print("  开始导入 arcpy...")
    print("  (这可能需要 10-60 秒，请耐心等待)")

    start = time.monotonic()
    try:
        import arcpy
        elapsed = time.monotonic() - start
        print(f"  [OK] arcpy 导入成功")
        print(f"  耗时: {elapsed:.2f} 秒")

        if elapsed > 60:
            print(f"  [!!] 警告: 导入时间过长 ({elapsed:.1f}s)")
            print("       可能存在严重的授权握手问题!")
        elif elapsed > 30:
            print(f"  [!] 注意: 导入时间较长 ({elapsed:.1f}s)")
            print("      可能存在授权握手延迟")
        elif elapsed > 15:
            print(f"  [!] 导入时间稍长 ({elapsed:.1f}s)")
            print("      接近正常冷启动上限")
        else:
            print(f"  [OK] 导入速度正常")

        # 获取 ArcGIS 信息
        info = arcpy.GetInstallInfo()
        print(f"\n[3] ArcGIS 安装信息:")
        print(f"  版本: {info.get('Version')}")
        print(f"  构建号: {info.get('BuildNumber')}")
        print(f"  产品: {arcpy.ProductInfo()}")

    except Exception as exc:
        elapsed = time.monotonic() - start
        print(f"  [X] arcpy 导入失败")
        print(f"  耗时: {elapsed:.2f} 秒")
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

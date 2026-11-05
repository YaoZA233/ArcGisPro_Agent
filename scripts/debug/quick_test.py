"""快速测试：模拟 Pro 环境变量污染场景"""
import os
import sys
import time
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_with_env(env_vars: dict, label: str) -> float:
    """启动子进程测试 arcpy 导入时间"""
    print(f"\n[{label}]")
    print("-" * 60)

    # 显示测试环境
    for key, value in env_vars.items():
        status = "✓ 已设置" if value else "✗ 未设置"
        print(f"  {key}: {status}")

    # 准备测试脚本
    test_script = ROOT / "workspace" / "temp_import_test.py"
    test_script.parent.mkdir(exist_ok=True)
    test_script.write_text("""
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

start = time.monotonic()
try:
    import arcpy
    elapsed = time.monotonic() - start
    print(f"SUCCESS:{elapsed:.2f}")
except Exception as exc:
    elapsed = time.monotonic() - start
    print(f"FAILED:{elapsed:.2f}:{exc}")
""")

    # 准备环境
    env = os.environ.copy()
    for key, value in env_vars.items():
        if value:
            env[key] = value
        else:
            env.pop(key, None)

    # 启动测试
    print("\n  启动测试进程...")
    start = time.monotonic()

    try:
        result = subprocess.run(
            [sys.executable, str(test_script)],
            capture_output=True,
            text=True,
            timeout=60,
            env=env
        )
        elapsed = time.monotonic() - start

        output = result.stdout.strip()
        if output.startswith("SUCCESS:"):
            import_time = float(output.split(":")[1])
            print(f"  ✓ arcpy 导入成功")
            print(f"  ⏱  导入耗时: {import_time:.2f}s")
            print(f"  ⏱  总耗时: {elapsed:.2f}s")

            if import_time > 30:
                print(f"  ⚠️  警告: 导入时间过长，可能存在授权握手问题！")
            elif import_time > 15:
                print(f"  ⚠️  注意: 导入时间较长，接近冷启动上限")
            else:
                print(f"  ✓ 导入速度正常")

            return import_time
        else:
            print(f"  ✗ 测试失败: {output}")
            return -1

    except subprocess.TimeoutExpired:
        print(f"  ✗ 测试超时 (60s)")
        print(f"  ⚠️  这表明存在严重的授权握手阻塞问题！")
        return -1

    except Exception as exc:
        print(f"  ✗ 测试异常: {exc}")
        return -1

    finally:
        try:
            test_script.unlink()
        except:
            pass


def main():
    print("=" * 60)
    print("ArcGIS Agent 环境变量影响测试")
    print("=" * 60)
    print("\n本测试将模拟两种场景：")
    print("1. 带有 Pro 环境变量（模拟修复前）")
    print("2. 清理环境变量（模拟修复后）")
    print("\n测试需要约 30-60 秒...")

    # 检查当前是否在 Pro 环境中
    has_pro_env = bool(os.environ.get("ESRI_SOFTWARE_CLASS"))
    if has_pro_env:
        print("\n⚠️  检测到当前进程在 ArcGIS Pro 环境中运行")
        print("   测试结果可能不准确，建议在独立 Python 环境中运行此脚本")

    # 测试 1: 带有 Pro 环境变量（如果当前没有，用假值模拟）
    polluted_env = {
        "ESRI_SOFTWARE_CLASS": os.environ.get("ESRI_SOFTWARE_CLASS", "Pro"),
        "ARCGISHOME": os.environ.get("ARCGISHOME", "C:\\Program Files\\ArcGIS\\Pro"),
        "ARCGIS_PRODUCT_NAME": os.environ.get("ARCGIS_PRODUCT_NAME", "ArcGISPro"),
    }

    time1 = test_with_env(polluted_env, "场景 1: 带有 Pro 环境变量（模拟修复前）")

    # 测试 2: 清理环境变量
    clean_env = {
        "ESRI_SOFTWARE_CLASS": None,
        "ARCGISHOME": None,
        "ARCGIS_PRODUCT_NAME": None,
        "ARCGIS_VERSION": None,
    }

    time2 = test_with_env(clean_env, "场景 2: 清理环境变量（模拟修复后）")

    # 结果对比
    print("\n" + "=" * 60)
    print("测试结果总结")
    print("=" * 60)

    if time1 > 0 and time2 > 0:
        improvement = time1 - time2
        if improvement > 5:
            print(f"\n✓ 清理环境变量后性能显著改善")
            print(f"  修复前: {time1:.2f}s")
            print(f"  修复后: {time2:.2f}s")
            print(f"  改善: {improvement:.2f}s ({improvement/time1*100:.1f}%)")
        else:
            print(f"\n✓ 两种场景性能相近")
            print(f"  场景 1: {time1:.2f}s")
            print(f"  场景 2: {time2:.2f}s")
            print(f"  差异: {abs(improvement):.2f}s")

            if time1 < 15 and time2 < 15:
                print(f"\n✓ 两种场景都正常，说明当前环境下授权握手不是问题")
            else:
                print(f"\n⚠️  两种场景都较慢，可能有其他性能问题")

    elif time1 < 0 and time2 > 0:
        print(f"\n✓ 清理环境变量成功解决了阻塞问题")
        print(f"  修复前: 超时/失败")
        print(f"  修复后: {time2:.2f}s")

    else:
        print(f"\n⚠️  测试未能完成，请检查：")
        print(f"  - ArcGIS Pro Python 环境是否正确配置")
        print(f"  - arcpy 模块是否可以正常导入")

    print("\n" + "=" * 60)
    print("\n提示：")
    print("- 如果场景 1 超时而场景 2 正常，说明修复有效")
    print("- 如果两种场景都正常，说明你的环境下授权握手不是瓶颈")
    print("- 第一次 arcpy 导入 10-15s 是正常的冷启动时间")

    return 0


if __name__ == "__main__":
    sys.exit(main())

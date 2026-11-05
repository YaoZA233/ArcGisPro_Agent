"""测试 worker 子进程启动和 arcpy 导入"""
import sys
import time
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from arcgis_agent.runtime import ArcPyRuntime
from arcgis_agent.config import discover_python


def main():
    print("=" * 60)
    print("测试 Worker 子进程启动和 arcpy 导入")
    print("=" * 60)

    try:
        python_path = discover_python(None)
        print(f"\n使用 Python: {python_path}")
    except Exception as exc:
        print(f"\n✗ 无法找到 ArcGIS Pro Python: {exc}")
        return 1

    # 创建 runtime，设置较短的 stall 超时用于测试
    runtime = ArcPyRuntime(python_path, timeout=180, stall_seconds=30)

    print("\n[测试 1] 调用 environment 工具（快速测试）")
    print("-" * 60)

    start = time.monotonic()
    result = runtime.call("environment", {}, Path("workspace/test_output"))
    elapsed = time.monotonic() - start

    if result["ok"]:
        print(f"✓ 调用成功，耗时: {elapsed:.2f}s")
        data = result.get("data", {})
        print(f"  ArcGIS 版本: {data.get('version')}")
        print(f"  产品: {data.get('product')}")
        print(f"  Python: {data.get('python_version')}")

        if elapsed > 30:
            print(f"\n⚠️  警告: 耗时过长 ({elapsed:.1f}s)，可能仍存在授权握手问题")
        elif elapsed > 15:
            print(f"\n⚠️  注意: 耗时较长 ({elapsed:.1f}s)，接近正常冷启动上限")
        else:
            print(f"\n✓ 性能正常")
    else:
        print(f"✗ 调用失败，耗时: {elapsed:.2f}s")
        error = result.get("error", {})
        print(f"  错误类型: {error.get('type')}")
        print(f"  错误信息: {error.get('message')}")
        if "hint" in error:
            print(f"  提示: {error['hint']}")
        return 1

    print("\n[测试 2] 再次调用（测试 worker 重用）")
    print("-" * 60)

    start = time.monotonic()
    result2 = runtime.call("environment", {}, Path("workspace/test_output"))
    elapsed2 = time.monotonic() - start

    if result2["ok"]:
        print(f"✓ 第二次调用成功，耗时: {elapsed2:.2f}s")
        if elapsed2 < 5:
            print("✓ Worker 缓存生效，性能良好")
        else:
            print(f"⚠️  注意: 第二次调用仍耗时 {elapsed2:.1f}s")
    else:
        print(f"✗ 第二次调用失败")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)

    return 0


if __name__ == "__main__":
    sys.exit(main())

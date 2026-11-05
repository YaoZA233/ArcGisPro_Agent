"""测试：Python 解释器启动速度"""
import subprocess
import time
import sys
from pathlib import Path

print("=" * 60)
print("测试 Pro Python 启动速度")
print("=" * 60)

# 查找 Pro Python
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from arcgis_agent.config import discover_python

try:
    pro_python = discover_python(None)
    print(f"\nPro Python: {pro_python}")
except Exception as e:
    print(f"\n错误: {e}")
    sys.exit(1)

# 简单的测试脚本
test_script = 'import sys; print("READY", flush=True); sys.exit(0)'

print("\n" + "=" * 60)
print("测试 1: 直接启动 Python（无特殊环境）")
print("=" * 60)

start = time.time()
try:
    result = subprocess.run(
        [str(pro_python), "-c", test_script],
        capture_output=True,
        text=True,
        timeout=10
    )
    elapsed = time.time() - start

    print(f"启动耗时: {elapsed:.2f} 秒")
    print(f"输出: {result.stdout.strip()}")

    if elapsed > 5:
        print("[警告] 启动速度异常慢！")
    else:
        print("[OK] 启动速度正常")

except subprocess.TimeoutExpired:
    print("[失败] 启动超时（10秒）")
except Exception as e:
    print(f"[错误] {e}")

print("\n" + "=" * 60)
print("测试 2: 模拟 worker 启动（通过文件协议）")
print("=" * 60)

# 创建临时测试脚本
temp_dir = Path("workspace/temp")
temp_dir.mkdir(parents=True, exist_ok=True)

worker_script = temp_dir / "test_worker.py"
worker_script.write_text("""
import sys
print("[worker] starting", file=sys.stderr, flush=True)
print("READY", flush=True)
sys.exit(0)
""")

request_file = temp_dir / "request.json"
result_file = temp_dir / "result.json"
request_file.write_text('{"tool": "test"}')

print("\n模拟 worker 启动...")
start = time.time()

try:
    result = subprocess.run(
        [str(pro_python), str(worker_script), str(request_file), str(result_file)],
        capture_output=True,
        text=True,
        timeout=10
    )
    elapsed = time.time() - start

    print(f"启动耗时: {elapsed:.2f} 秒")
    print(f"Stdout: {result.stdout.strip()}")
    print(f"Stderr: {result.stderr.strip()}")

    if elapsed > 5:
        print("[警告] Worker 启动异常慢！")
        print("这可能是问题所在！")
    else:
        print("[OK] Worker 启动速度正常")

except subprocess.TimeoutExpired:
    elapsed = time.time() - start
    print(f"[失败] Worker 启动超时（{elapsed:.1f}秒）")
    print("这说明启动 Pro Python 本身就有问题！")
except Exception as e:
    print(f"[错误] {e}")

# 清理
try:
    worker_script.unlink()
    request_file.unlink()
    if result_file.exists():
        result_file.unlink()
except:
    pass

print("\n" + "=" * 60)
print("测试完成")
print("=" * 60)
print("\n诊断:")
print("- 如果测试 1 慢: Pro Python 环境有问题")
print("- 如果测试 2 慢: worker 协议有问题")
print("- 如果都快: 说明在 Pro 运行时有其他干扰因素")

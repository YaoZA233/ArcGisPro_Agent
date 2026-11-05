"""检查当前是否有 ArcGIS Pro 进程在运行"""
import subprocess
import sys

print("=" * 60)
print("检查 ArcGIS Pro 进程")
print("=" * 60)

try:
    # Windows tasklist 命令
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq ArcGISPro.exe", "/FO", "CSV"],
        capture_output=True,
        text=True,
        encoding='gbk'  # Windows 中文系统使用 GBK
    )

    lines = result.stdout.strip().split('\n')

    if len(lines) > 1:  # 有标题行 + 进程行
        print("\n发现 ArcGIS Pro 进程:")
        for line in lines[1:]:  # 跳过标题
            print(f"  {line}")
        print("\n警告: 必须完全关闭 ArcGIS Pro 并重启才能应用修复!")
        print("步骤:")
        print("1. 关闭所有 Pro 窗口")
        print("2. 打开任务管理器确认没有 ArcGISPro.exe")
        print("3. 重新启动 Pro")
    else:
        print("\n未发现 ArcGIS Pro 进程")
        print("现在可以启动 Pro 进行测试")

except Exception as e:
    print(f"\n错误: {e}")
    print("请手动检查任务管理器")

print("\n" + "=" * 60)

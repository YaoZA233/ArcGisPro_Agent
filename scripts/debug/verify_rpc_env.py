"""验证 RPC 进程是否真的清理了环境"""
import sys
import json

# 这个脚本由 C# 启动的 RPC 进程调用
# 用来验证环境变量是否被正确清理

env_check = {
    "ESRI_SOFTWARE_CLASS": None,
    "ARCGISHOME": None,
    "ARCGIS_PRODUCT_NAME": None,
    "ARCGIS_VERSION": None,
}

import os

result = {}
for key in env_check:
    result[key] = os.environ.get(key, "<NOT_SET>")

# 输出所有环境变量用于调试
result["all_env_keys"] = sorted(list(os.environ.keys()))
result["total_env_count"] = len(os.environ)

print(json.dumps(result, indent=2))

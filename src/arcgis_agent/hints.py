"""Failure-pattern hints appended to worker errors so models and users can recover."""
from __future__ import annotations

PATTERNS = (
    ("unicodedecodeerror", "目标 .gdb 可能是普通目录(假地理数据库);用 CreateFileGDB 重建或改选其他工作空间。"),
    ("error 000732", "数据集不存在或不可见;用 find_datasets 按关键词搜索数据根目录定位候选路径,或用 list_datasets / describe 核对绝对目录路径与成员名。"),
    ("error 000800", "参数取值不在允许集合内;对照工具 schema 的枚举拼写。"),
    ("error 010819", "该 ArcGIS 工具不接受含空格的路径;把数据移动到无空格目录后重试。"),
    ("error 999999", "ArcGIS 内部错误常见于异常像元大小或缺少统计的栅格;为输出栅格显式给出合理 cell_size"
                     "(地理坐标系须用度数,例如 0.0005),并先用 describe 核验输入栅格范围。"),
    ("error 000462", "该工具需要更高的 ArcGIS Pro 许可级别(如 Union / Erase / Near / 泰森多边形需标准版或高级版)。"),
    ("error 000728", "过滤条件引用的字段在表中不存在或拼写不一致;先用 describe 核对精确字段名,再用 read_rows 小量试跑过滤条件。"),
    ("error 21048", "栅格没有属性表或字段不可用;重分类需要整型栅格或先重采样/转为整型后再试。"),
    ("zerodivisionerror", "算术表达式出现除零;用 read_rows 检查被除数字段是否含 0,或改写表达式避免除零。"),
    ("schema lock", "数据正被其他 ArcGIS 会话或进程占用(schema lock);关闭相关地图、布局或工程后重试。"),
    ("transformation", "跨 datum 投影需要显式地理变换;从 ArcGIS 消息中取变换名后用 project 的 transformation 参数重试。"),
)

TIMEOUT_HINT = ("ArcPy 长时间运行通常源于不可达的网络路径、在线服务图层或超大输入;"
                "地图含在线底图时,弱网下打开工程也会长时间等待,可暂时把底图切换为离线底图再试;"
                "关闭 Pro 中其他耗时操作后重试,或改用更小的数据集。")

STALL_HINT = ("请检查运行日志最后一条启动标记，确认插件已更新；"
              "可在保持 Pro 运行时执行 scripts/agent.ps1 doctor 对照诊断。")


def hint_for(message: str):
    lowered = message.lower()
    for needle, hint in PATTERNS:
        if needle in lowered:
            return hint
    return None

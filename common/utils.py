"""通用工具函数模块。

提供项目中常用的辅助函数，避免代码重复。
"""


def fmt_pct(value: float) -> float:
    """格式化百分比为2位小数，避免浮点精度问题。"""
    return float(f"{value:.2f}")


def percentile(data: list, p: int) -> int:
    """计算数据列表的第 p 百分位值。"""
    if not data:
        return 0
    k = (len(data) - 1) * (p / 100)
    f = int(k)
    c = f + 1
    if c >= len(data):
        return data[f]
    return int(data[f] + (k - f) * (data[c] - data[f]))

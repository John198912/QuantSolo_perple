"""因子测试占位（M2 填充）。

TODO（M2 里程碑）：
  - 因子纯函数单测（归因 raw / 标准化 processed / 正交化 orthogonal）
  - 因子值域校验（NaN 处理、极值处理）
  - factor_snapshot_batch_asof 批量查询正确性
  - variant_count 不出现在 DSR N 计算调用栈（C2 静态检查，QS-C03 §12.3）
"""


def test_factor_placeholder_pass():
    """占位测试，避免空目录（M2 填充前保持 pass）。"""
    pass

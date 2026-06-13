"""QuantSolo 编排层（QS-E09）：联调/端到端跑通基础设施。

子模块：
  demo_data        — 合成 A 股式数据种子（离线可复现）
  research_pipeline — 因子→信号→回测→闸门完整研究流水线
  trading_pipeline  — 信号→风控→15态状态机→对账→监控完整交易流水线
  e2e              — 端到端串联（每阶段 checkpoint）
  cli              — 子命令 CLI（argparse）
"""

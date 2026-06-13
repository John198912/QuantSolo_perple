"""静态守卫扫描（QS-E07 红线 R1/R2/R6 / QS-E04 §3）。

对源码做 AST/正则静态检查，违反即退出码 1：
  R1  `import xtquant` 只允许出现在白名单文件（执行适配器 + 物理熔断脚本）
  R2  禁止对点时表执行 UPDATE / DELETE（一切修改=追加 ACTIVE，撤销=追加 VOIDED）
  R6  禁止用 float 直接算钱（金额/费率字段用 Decimal 或整数分）—— 启发式告警

该工具本身不依赖第三方库，可在任何环境运行。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
SCRIPTS = REPO_ROOT / "scripts"

# R1: 允许 import xtquant 的唯一白名单（相对 repo 根）
XTQUANT_WHITELIST = {
    "src/execution/adapters/xtquant_adapter.py",
    "src/execution/break_glass.py",
    "scripts/break_glass.py",
}

IMPORT_XTQUANT = re.compile(r"^\s*(?:import\s+xtquant|from\s+xtquant\b)", re.MULTILINE)
# R2: 针对点时表的危险 SQL。
# 仅匹配真实 SQL 语法形态 `UPDATE <表>` / `DELETE FROM <表>`（表名前允许引号/反引号），
# 避免误报描述规则的文档字符串（如 “禁止 UPDATE/DELETE”）或 Python 的 dict.update() 调用。
PIT_TABLES = (
    "daily_bar", "adj_factor_pit", "factor_snapshot", "financials_pit",
    "price_limit_rule_pit", "corp_action_pit", "data_cut", "snapshot_manifest",
    "supersession_log", "trade_calendar",
)
# 为每张点时表预编译精确的 UPDATE / DELETE 语句模式。
PIT_MUTATE_PATTERNS = [
    (
        tbl,
        re.compile(rf"\bUPDATE\s+[\"'`]?{re.escape(tbl)}\b", re.IGNORECASE),
        "UPDATE",
    )
    for tbl in PIT_TABLES
] + [
    (
        tbl,
        re.compile(rf"\bDELETE\s+FROM\s+[\"'`]?{re.escape(tbl)}\b", re.IGNORECASE),
        "DELETE",
    )
    for tbl in PIT_TABLES
]


def rel(p: Path) -> str:
    return p.relative_to(REPO_ROOT).as_posix()


def scan() -> int:
    violations: list[str] = []
    py_files = sorted(SRC.rglob("*.py")) + sorted(SCRIPTS.rglob("*.py"))

    for f in py_files:
        text = f.read_text(encoding="utf-8")
        relpath = rel(f)

        # R1
        if IMPORT_XTQUANT.search(text) and relpath not in XTQUANT_WHITELIST:
            violations.append(f"[R1] {relpath}: 非白名单文件 import xtquant（风控守卫旁路风险）")

        # R2：精确匹配对点时表的 UPDATE / DELETE 语句（一切修改=追加 ACTIVE，撤销=追加 VOIDED）
        for tbl, pattern, op in PIT_MUTATE_PATTERNS:
            for m in pattern.finditer(text):
                line_no = text[: m.start()].count("\n") + 1
                violations.append(
                    f"[R2] {relpath}:{line_no}: 对点时表 `{tbl}` 执行 "
                    f"{op}（点时表禁止 UPDATE/DELETE，须改为追加写入）"
                )

        # R6：float(...) 与金额关键字同现 -> 启发式告警（非阻断，打印）
        if re.search(r"float\s*\(", text) and re.search(
            r"price|amount|cash|commission|fee|cost|pnl|nav", text, re.IGNORECASE
        ):
            line_no = next(
                (i + 1 for i, ln in enumerate(text.splitlines()) if "float(" in ln), 0
            )
            print(f"[R6][warn] {relpath}:{line_no}: 检测到 float() 与金额关键字同现，"
                  "请确认金额计算使用 Decimal/整数分。")

    if violations:
        print("静态守卫扫描发现违规：")
        for v in violations:
            print("  " + v)
        return 1
    print(f"[OK] 静态守卫扫描通过（检查 {len(py_files)} 个 .py 文件，无 R1/R2 违规）")
    return 0


if __name__ == "__main__":
    raise SystemExit(scan())

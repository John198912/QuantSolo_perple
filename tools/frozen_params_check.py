"""冻结参数 SHA256 校验工具（QS-E07 红线 R3 / §3.3 自检命令）。

用法：
    python tools/frozen_params_check.py            # 校验，篡改则退出码 1
    python tools/frozen_params_check.py --register # 重新登记当前哈希（仅在宪法修订流程中由人执行）

校验逻辑：比对 config/frozen.toml 的当前 SHA256 与 config/frozen.sha256 登记值。
不一致 = 有人改了冻结参数却没走宪法修订流程 -> 失败。
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.common.config import FROZEN_HASH_PATH, FROZEN_PATH, compute_frozen_hash  # noqa: E402


def register() -> int:
    h = compute_frozen_hash()
    FROZEN_HASH_PATH.write_text(f"{h}  frozen.toml\n", encoding="utf-8")
    print(f"[register] 已登记 frozen.toml 哈希: {h}")
    print("提醒：登记动作应仅出现在 QS-C00 §四 宪法修订流程中，并随提交说明记录修订理由。")
    return 0


def check() -> int:
    if not FROZEN_PATH.exists():
        print(f"[FAIL] 冻结参数文件缺失: {FROZEN_PATH}")
        return 1
    actual = compute_frozen_hash()
    if not FROZEN_HASH_PATH.exists():
        print("[FAIL] frozen.sha256 登记文件缺失。首次请运行 --register。")
        return 1
    registered = FROZEN_HASH_PATH.read_text(encoding="utf-8").strip().split()[0]
    if registered != actual:
        print("[FAIL] 冻结参数 SHA256 不一致 —— frozen.toml 被改动但未走宪法修订流程。")
        print(f"  登记值: {registered}")
        print(f"  实际值: {actual}")
        return 1
    print(f"[OK] 冻结参数校验通过: {actual}")
    return 0


if __name__ == "__main__":
    if "--register" in sys.argv:
        raise SystemExit(register())
    raise SystemExit(check())

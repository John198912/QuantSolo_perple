"""配置加载（QS-E02 §5）。

冻结参数（frozen.toml）只读加载并校验，禁止运行时修改；
可调参数（tunable.yaml）盘后人工调整。任何模块读取冻结参数必须经此处，
禁止散落硬编码（QS-E07 红线 R3）。
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = REPO_ROOT / "config"
FROZEN_PATH = CONFIG_DIR / "frozen.toml"
FROZEN_HASH_PATH = CONFIG_DIR / "frozen.sha256"
TUNABLE_PATH = CONFIG_DIR / "tunable.yaml"
SOURCE_PRIORITY_PATH = CONFIG_DIR / "source_priority.yaml"


def compute_frozen_hash(path: Path = FROZEN_PATH) -> str:
    """计算 frozen.toml 的 SHA256（规范化换行后），作为宪法登记值。"""
    raw = path.read_bytes().replace(b"\r\n", b"\n")
    return hashlib.sha256(raw).hexdigest()


def _deep_freeze(obj: Any) -> Any:
    if isinstance(obj, dict):
        return MappingProxyType({k: _deep_freeze(v) for k, v in obj.items()})
    if isinstance(obj, list):
        return tuple(_deep_freeze(v) for v in obj)
    return obj


@lru_cache(maxsize=1)
def load_frozen() -> MappingProxyType:
    """加载冻结参数，返回不可变只读映射。

    若 frozen.sha256 存在且与当前内容不符，抛 RuntimeError（防篡改，红线 R3）。
    """
    if not FROZEN_PATH.exists():
        raise FileNotFoundError(f"冻结参数文件缺失: {FROZEN_PATH}")
    if FROZEN_HASH_PATH.exists():
        registered = FROZEN_HASH_PATH.read_text(encoding="utf-8").strip().split()[0]
        actual = compute_frozen_hash()
        if registered != actual:
            raise RuntimeError(
                "冻结参数 SHA256 校验失败：frozen.toml 被篡改或未走宪法修订流程。\n"
                f"  登记值: {registered}\n  实际值: {actual}\n"
                "修改冻结参数必须走 QS-C00 §四 流程并更新 frozen.sha256。"
            )
    with FROZEN_PATH.open("rb") as f:
        data = tomllib.load(f)
    return _deep_freeze(data)


@lru_cache(maxsize=1)
def load_tunable() -> dict:
    with TUNABLE_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def load_source_priority() -> dict:
    with SOURCE_PRIORITY_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)

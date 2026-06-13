# QuantSolo Makefile — 离线一键跑通全链路
# 用法: make <目标>
# 示例: make seed && make e2e && make golive

PYTHON := python
SRC_DIR := src
TOOLS_DIR := tools
TESTS_DIR := tests

# 默认目标：显示帮助
.DEFAULT_GOAL := help

.PHONY: help selfcheck test e2e seed golive lint clean

help:  ## 显示帮助信息
	@echo "QuantSolo 快速入口"
	@echo ""
	@echo "  make seed       生成合成演示数据（需先运行一次）"
	@echo "  make e2e        端到端跑通全链路（seed → research → paper-trade → reconcile）"
	@echo "  make research   仅运行研究管线（因子→信号→回测→闸门）"
	@echo "  make golive     上线就绪门检查（聚合验收 PASS/FAIL）"
	@echo "  make test       运行全量离线测试（pytest -m 'not live'）"
	@echo "  make selfcheck  静态守卫 + 冻结参数 + 测试（三合一）"
	@echo "  make lint       代码风格检查（flake8 / ruff，如已安装）"
	@echo "  make clean      清理运行产物（run/ 目录）"
	@echo ""
	@echo "  首次使用: make seed && make e2e"

## 生成合成演示数据（~30只标的，~2.5年日频行情，写入 data/ db/）
seed:
	@echo ">>> 生成演示数据..."
	$(PYTHON) -m src seed-demo

## 端到端跑通全链路
e2e: seed
	@echo ">>> 端到端跑通..."
	$(PYTHON) -m src e2e

## 仅运行研究管线
research:
	$(PYTHON) -m src research

## 上线就绪门检查
golive:
	@echo ">>> 上线就绪门检查..."
	$(PYTHON) $(TOOLS_DIR)/golive_readiness.py

## 运行全量离线测试（跳过 live 标记）
test:
	@echo ">>> 运行离线测试..."
	$(PYTHON) -m pytest -q -m "not live" --tb=short

## 三合一自检：静态守卫 + 冻结参数 + 测试
selfcheck:
	@echo ">>> 自检（静态守卫 + 冻结参数 + pytest）..."
	$(PYTHON) -m src selfcheck

## 代码风格检查
lint:
	@echo ">>> 代码风格检查..."
	@if command -v ruff >/dev/null 2>&1; then \
		ruff check $(SRC_DIR) $(TOOLS_DIR); \
	elif command -v flake8 >/dev/null 2>&1; then \
		flake8 $(SRC_DIR) $(TOOLS_DIR) --max-line-length=120 --ignore=E501,W503; \
	else \
		echo "  [!] ruff/flake8 未安装，跳过 lint"; \
	fi

## 清理运行产物（不清理 data/ 和 db/）
clean:
	@echo ">>> 清理运行产物..."
	rm -rf run/*.md run/*.log
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "  清理完成（data/ db/ 未清理）"

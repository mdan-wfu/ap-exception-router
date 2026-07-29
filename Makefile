.PHONY: install seed probe demo test report dashboard

install:
	python -m venv .venv
	.venv/bin/pip install -r requirements.txt

seed:
	.venv/bin/python -m src.store.seed

probe:
	.venv/bin/python scripts/probe.py

demo:
	@echo "not yet implemented"

test:
	.venv/bin/pytest tests/

report:
	@echo "not yet implemented"

dashboard:
	@echo "not yet implemented"

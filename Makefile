.PHONY: install seed audit-reset probe demo test report dashboard

install:
	python -m venv .venv
	.venv/bin/pip install -r requirements.txt

seed:
	.venv/bin/python -m src.store.seed

audit-reset:
	@rm -f runs/audit.sqlite
	@mkdir -p runs
	@.venv/bin/python -c "from src.store.audit import AuditStore; AuditStore()" && echo "audit.sqlite rebuilt with fresh schema"

probe:
	.venv/bin/python scripts/probe.py

# Cold-clone reviewer path. Requires no API key: replay-only against
# committed cassettes, human_gate resolves from data/fixtures/human_gate.json
# so it never hangs.
demo: audit-reset
	LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/python main.py --batch --replay

test:
	.venv/bin/pytest tests/

report:
	@echo "not yet implemented"

dashboard:
	@echo "not yet implemented"

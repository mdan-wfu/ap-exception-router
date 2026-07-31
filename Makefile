.PHONY: install seed audit-reset probe demo demo-adversarial test report eval eval-adversarial dashboard

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
#
# Also clears LangGraph's checkpoint DB. Checkpoint persistence is correct
# for interactive human-gate resume in real operation, but wrong for demo
# mode — the reviewer must see identical output on every invocation. Without
# the rm, a second `make demo` resumes from the first run's terminal state
# and produces different (or truncated) output.
demo: audit-reset
	@rm -f runs/checkpoints.sqlite
	LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/python main.py --batch --replay

test:
	.venv/bin/pytest tests/

report:
	@.venv/bin/python -m scripts.report

eval:
	@LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/python -m eval.run_eval

# Authored adversarial set — separate corpus, separate ground truth. Kept out
# of `make demo` and `make eval` so the reviewer's first-run numbers are the
# provided corpus only.
demo-adversarial: audit-reset
	@rm -f runs/checkpoints.sqlite
	@LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/python main.py --batch --replay --corpus data/adversarial

eval-adversarial:
	@LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/python -m eval.run_eval \
		--corpus data/adversarial \
		--ground-truth eval/ground_truth_adversarial.yaml \
		--label "authored adversarial set"

# FastAPI dashboard. Read-only over runs/audit.sqlite. Zero API calls.
# Populate the audit store first with `make demo` and (optionally)
# `make demo-adversarial`.
# Boots at http://127.0.0.1:8000
dashboard:
	@LLM_MODE=replay HUMAN_GATE_MODE=demo .venv/bin/uvicorn src.ui.app:app --host 127.0.0.1 --port 8000 --reload

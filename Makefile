.PHONY: install seed audit-reset probe demo demo-adversarial demo-digest demo-digest-check test report eval eval-adversarial dashboard

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

# Canonical projection of per-invoice outcomes/findings/costs from the
# audit store. Prints one line per invoice + a final md5. Replaces the
# earlier stdout-md5 check that coupled determinism to CLI presentation
# (any header change, git-SHA change, or format tweak produced a false
# positive). See DECISIONS 2026-07-31 demo-digest-replaces-stdout-hash.
demo-digest: demo
	@.venv/bin/python -m scripts.demo_digest

# Enforceable regression gate — exits nonzero on mismatch against
# docs/demo-digest.txt. Update the baseline (and note WHY in DECISIONS)
# only for a deliberate semantic change.
demo-digest-check: demo
	@expected=$$(tr -d '[:space:]' < docs/demo-digest.txt); \
	actual=$$(.venv/bin/python -m scripts.demo_digest | tail -1 | awk '{print $$2}'); \
	if [ "$$expected" = "$$actual" ]; then \
		echo "demo-digest OK: $$actual"; \
	else \
		echo "demo-digest MISMATCH"; \
		echo "  expected: $$expected  (docs/demo-digest.txt)"; \
		echo "  actual:   $$actual"; \
		echo "If this is a deliberate semantic change, rerun \`make demo-digest\`,"; \
		echo "copy the md5 into docs/demo-digest.txt, and record why in DECISIONS.md."; \
		exit 1; \
	fi

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

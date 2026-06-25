.PHONY: install playground

install:
	uv sync

playground:
	agents-cli playground

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	agents-cli eval grade --traces artifacts/traces/generated_traces.json --config tests/eval/eval_config.yaml

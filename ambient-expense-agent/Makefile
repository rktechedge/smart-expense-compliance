.PHONY: install playground run test generate-traces grade


install:
	uv sync

playground:
	uv run agents-cli playground --port 18081

run:
	uv run python -m expense_agent.fast_api_app

generate-traces:
	uv run python tests/eval/generate_traces.py

grade:
	uv run agents-cli eval grade --config tests/eval/eval_config.yaml --traces artifacts/traces/generated_traces.json


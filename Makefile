install:
	python3 -m pip install -e '.[dev]'

test:
	python3 -m pytest

lint:
	ruff check .

typecheck:
	mypy src

build:
	python3 -m build

check: lint typecheck test

coverage:
	python3 -m pytest --cov=statebreak --cov-report=term-missing

demo:
	python3 -m statebreak list
	python3 -m statebreak run scenarios --agent naive --format markdown --output /tmp/statebreak-naive.md || true
	python3 -m statebreak run scenarios --agent guarded --format markdown --output /tmp/statebreak-guarded.md

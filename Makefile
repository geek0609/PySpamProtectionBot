PYTHON ?= python

.PHONY: check test

check:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .
	$(PYTHON) -m mypy src tests

test:
	$(PYTHON) -m pytest

.PHONY: setup dev test lint typecheck e2e screenshots evaluate test-models models lock migrate security demo clean

setup:
	python scripts/tasks.py setup

dev:
	python scripts/tasks.py dev

test:
	python scripts/tasks.py test

lint:
	python scripts/tasks.py lint

typecheck:
	python scripts/tasks.py typecheck

e2e:
	python scripts/tasks.py e2e

screenshots:
	python scripts/tasks.py screenshots

evaluate:
	python scripts/tasks.py evaluate

test-models:
	python scripts/tasks.py test-models

models:
	python scripts/tasks.py models

lock:
	python scripts/tasks.py lock

migrate:
	python scripts/tasks.py migrate

security:
	python scripts/tasks.py security

demo:
	python scripts/tasks.py demo

clean:
	python scripts/tasks.py clean

.PHONY: backend frontend dev install

install:
	python3 -m venv .venv
	.venv/bin/pip install -r backend/requirements.txt
	cd frontend && npm install

backend:
	PYTHONPATH=. .venv/bin/uvicorn backend.gateway:app --host 0.0.0.0 --port 8000 --reload

frontend:
	cd frontend && npm run dev

dev:
	./scripts/dev.sh

.PHONY: up down test probe

up:
	docker compose up -d --build

down:
	docker compose down

test:
	cd apps/orchestrator && pytest -v

probe:
	PYTHONPATH=apps/orchestrator python3 ops/m0_probe_muse2api.py --base-url $(or $(MUSE_URL),http://localhost:8080)

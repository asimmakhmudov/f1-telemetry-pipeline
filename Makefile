MEETINGS ?= 3
HZ ?= 2
DATE ?= $(shell date -u +%Y-%m-%d)
Q ?= SELECT schema_name, table_name FROM duckdb_tables()
COMPOSE = docker compose
EXEC = $(COMPOSE) exec airflow

.PHONY: env up monitoring bi bi-build bi-export bi-import down reset bootstrap seed run dag-test warehouse corrupt test test-container test-integration lint format

env:             ## create .env from .env.example with generated logins and keys
	python3 scripts/init_env.py

up:              ## core services
	$(COMPOSE) up -d --build

monitoring:      ## core + Prometheus and Grafana
	$(COMPOSE) --profile monitoring up -d

bi:              ## core + Superset
	$(COMPOSE) --profile bi up -d --build

bi-build:        ## build the 3-page F1 Telemetry dashboard from bi/build_dashboard.py
	$(COMPOSE) --profile bi exec superset python /app/bi/build_dashboard.py

bi-export:       ## save the Superset dashboards into bi/export/ (commit the YAML)
	$(COMPOSE) --profile bi exec superset python /app/bi/superset_bi.py export

bi-import:       ## load bi/export/ into Superset if it has no dashboards yet
	$(COMPOSE) --profile bi exec superset python /app/bi/superset_bi.py import-if-empty

down:            ## stop everything, keep data
	$(COMPOSE) --profile monitoring --profile bi down

reset:           ## stop everything and delete ALL data volumes
	$(COMPOSE) --profile monitoring --profile bi down --volumes

bootstrap:       ## schemas, tables, etl.LoadControl (idempotent)
	$(EXEC) python -m sql.bootstrap

seed:            ## synthetic race weekends: make seed MEETINGS=5 HZ=3.7
	$(EXEC) python -m generator.generate --meetings $(MEETINGS) --hz $(HZ)

run:             ## unpause and trigger the DAG
	$(EXEC) airflow dags unpause f1_telemetry_daily
	$(EXEC) airflow dags trigger f1_telemetry_daily

dag-test:        ## run the whole DAG in-process: make dag-test DATE=2026-03-08
	$(EXEC) airflow dags test f1_telemetry_daily $(DATE)

warehouse:       ## query DuckDB: make warehouse Q="SELECT * FROM etl.LoadControl"
	$(EXEC) python -m tasks.warehouse sql "$(Q)"

corrupt:         ## break one lap's sector time to demonstrate the quality gate
	$(EXEC) python -m generator.generate --corrupt Laps

test:            ## unit tests on the host (Spark/GE/Airflow tests skip if absent)
	python -m pytest tests/unit

test-container:  ## full unit suite inside the Airflow image (Spark, GE, Airflow included)
	$(EXEC) python -m pytest tests/unit

test-integration: ## WRITES test rows to the source and the DuckDB warehouse
	$(EXEC) python -m pytest tests/integration -m integration

lint:
	ruff check .
	black --check .

format:
	ruff check --fix .
	black .

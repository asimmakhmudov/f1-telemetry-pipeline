# Superset with the DuckDB driver, for reading the target schema in the BI copy.
FROM apache/superset:4.0.2

USER root
RUN pip install --no-cache-dir "duckdb-engine>=0.13,<1" "duckdb>=1.4,<2"
USER superset

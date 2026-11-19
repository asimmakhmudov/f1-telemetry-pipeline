# Airflow 2.9 + PySpark 3.5 (local mode) + DuckDB + psycopg2 (source) + GE 0.18.19
FROM apache/airflow:2.9.3-python3.11

USER root

# Java for Spark. The PostgreSQL client library (libpq) ships with the base image.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl default-jre-headless \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

# Scratch dirs on the /data volume, owned by airflow so a fresh volume inherits them.
RUN mkdir -p /data/spill /data/spark-tmp /data/duckdb-tmp \
 && chown -R airflow:0 /data && chmod -R 775 /data

ENV JAVA_HOME=/usr/lib/jvm/default-java \
    TZ=UTC

USER airflow

COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "apache-airflow==${AIRFLOW_VERSION}" -r /tmp/requirements.txt

# S3A jars in PySpark's jars folder, so every SparkSession can reach the lake.
ARG HADOOP_AWS_VERSION=3.3.4
ARG AWS_SDK_BUNDLE_VERSION=1.12.262
RUN JARS="$(python -c 'import os, pyspark; print(os.path.join(os.path.dirname(pyspark.__file__), "jars"))')" \
 && M2=https://repo1.maven.org/maven2 \
 && curl -fsSL -o "$JARS/hadoop-aws-${HADOOP_AWS_VERSION}.jar" \
      "$M2/org/apache/hadoop/hadoop-aws/${HADOOP_AWS_VERSION}/hadoop-aws-${HADOOP_AWS_VERSION}.jar" \
 && curl -fsSL -o "$JARS/aws-java-sdk-bundle-${AWS_SDK_BUNDLE_VERSION}.jar" \
      "$M2/com/amazonaws/aws-java-sdk-bundle/${AWS_SDK_BUNDLE_VERSION}/aws-java-sdk-bundle-${AWS_SDK_BUNDLE_VERSION}.jar"

# Pre-install httpfs so DuckDB never downloads it at run time.
RUN python -c "import duckdb; duckdb.connect().execute('INSTALL httpfs')"

ENV PYTHONPATH=/opt/airflow/project

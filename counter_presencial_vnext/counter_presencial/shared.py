"""Local utilities: no imports or runtime dependencies on the legacy project."""
import json
import re


def parse_gcs_uri(uri):
    if not isinstance(uri, str) or not uri.startswith("gs://"):
        raise ValueError("Expected gs://bucket/object")
    bucket, sep, name = uri[5:].partition("/")
    if (not sep or not name or not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,220}[a-z0-9]", bucket)
            or any(ord(c) < 32 for c in uri)):
        raise ValueError("Invalid GCS URI")
    return bucket, name


def table_id(value):
    if not re.fullmatch(r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", value or ""):
        raise ValueError("Expected project.dataset.table")
    return value


def query(client, sql, location, values=None):
    from google.cloud import bigquery
    params = [bigquery.ScalarQueryParameter(k, "STRING", v) for k, v in (values or {}).items()]
    return client.query(sql, location=location,
                        job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


def classification_sql(model, schema):
    table_id(model)
    return ("SELECT TO_JSON_STRING(generated) AS response FROM AI.GENERATE_TABLE("
            f"MODEL `{model}`, (SELECT @prompt AS prompt), STRUCT("
            + json.dumps(schema, ensure_ascii=True) + " AS output_schema, "
            "8192 AS max_output_tokens, 0 AS temperature)) AS generated")

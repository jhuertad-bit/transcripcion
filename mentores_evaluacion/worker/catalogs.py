"""Catalogos vigentes de Firestore; reemplazo atomico, sin fotografias historicas."""
import json
import os
from datetime import datetime, timedelta, timezone

from google.cloud import bigquery, firestore
from worker.normalizers import ESQUEMAS, DIAS_SCHEMA, transformar, transformar_datos

TABLES = {"mentores": "cat_mentores_firestore", "audios": "cat_audios_mentores_firestore",
          "dias": "cat_dias_utp"}


def schema(kind):
    fields = DIAS_SCHEMA if kind == "dias" else [bigquery.SchemaField(n, t) for n, t in ESQUEMAS[kind]]
    return list(fields) + [bigquery.SchemaField("catalog_load_id", "STRING", mode="REQUIRED")]


def document(doc):
    # La conversion usa los nombres de campos del normalizador existente.
    data = json.loads(json.dumps(doc.to_dict(), default=lambda v: v.isoformat() if hasattr(v, "isoformat") else str(v)))
    return {"name": "projects/source/databases/(default)/documents/" + doc.reference.path,
            "fields": {k: {"nativeValue": v} for k, v in data.items()}}


def extract(project):
    credentials = None
    if os.getenv("FIRESTORE_CREDENTIALS"):
        from google.auth import load_credentials_from_file
        credentials, _ = load_credentials_from_file(os.environ["FIRESTORE_CREDENTIALS"],
                                                     scopes=["https://www.googleapis.com/auth/datastore"])
    source = firestore.Client(project=project, credentials=credentials)
    now = datetime.now(timezone.utc).isoformat()
    try:
        mentors = [transformar(document(d), "mentores", now) for d in source.collection("mentores").stream()]
        audios = [transformar(document(d), "audios", now) for d in source.collection_group("audios").stream()
                  if d.reference.path.startswith("mentor_charlas/")]
        days = [dict(d.to_dict(), doc_id=d.id) for d in source.collection("dias_utp").stream()]
    finally:
        source.close()
    if not days:
        raise ValueError("dias_utp vacio: se conserva la carga vigente de catalogos")
    # Valores anidados de Firestore quedan como JSON, como en la carga original.
    events = json.loads(transformar_datos(days).to_json(orient="records", date_format="iso"))
    rows = {"mentores": mentors, "audios": audios, "dias": events}
    for kind, values in rows.items():
        key = {"mentores": "mentor_id", "audios": "firestore_path", "dias": "doc_id"}[kind]
        if not values or any(not v[key] for v in values) or len({v[key] for v in values}) != len(values):
            raise ValueError(f"Catalogo {kind} vacio o con claves invalidas/duplicadas")
    return rows


def initialize(client, raw, query):
    for kind, table in TABLES.items():
        client.create_table(bigquery.Table(f"{raw}.{table}", schema=schema(kind)), exists_ok=True)


def publish(client, raw, run_id, source_project, query):
    rows = extract(source_project)
    stages = []
    try:
        for kind, table in TABLES.items():
            stage = f"{raw}.tmp_catalog_{kind}_{run_id}"
            stages.append(stage)
            staging = bigquery.Table(stage, schema=schema(kind))
            staging.expires = datetime.now(timezone.utc) + timedelta(days=2)
            client.create_table(staging, exists_ok=True)
            config = bigquery.LoadJobConfig(schema=schema(kind), write_disposition="WRITE_TRUNCATE")
            client.load_table_from_json([dict(v, catalog_load_id=run_id) for v in rows[kind]], stage,
                                       job_config=config).result()
        inserts = '\n'.join(f"DELETE FROM `{raw}.{table}` WHERE TRUE;\n"
                            f"INSERT INTO `{raw}.{table}` SELECT * FROM `{stage}`;"
                            for table, stage in zip(TABLES.values(), stages))
        query(f"""BEGIN TRANSACTION;
{inserts}
UPDATE `{raw}.hist_mentores_pipeline_runs`
SET catalog_loaded_at=CURRENT_TIMESTAMP() WHERE run_id=@run_id;
COMMIT TRANSACTION;""")
    finally:
        for stage in stages:
            client.delete_table(stage, not_found_ok=True)

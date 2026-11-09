"""Fotografias completas de metadatos Firestore; nunca descarga ni copia audios."""
import json
import os
from datetime import datetime, timezone

from google.cloud import bigquery, firestore
from worker.normalizers import ESQUEMAS, DIAS_SCHEMA, transformar, transformar_datos

TABLES = {"mentores": "maestro_mentores_firestore", "audios": "maestro_audios_mentores_firestore",
          "dias": "dias_utp"}


def schema(kind):
    fields = DIAS_SCHEMA if kind == "dias" else [bigquery.SchemaField(n, t) for n, t in ESQUEMAS[kind]]
    return list(fields) + [bigquery.SchemaField("snapshot_id", "STRING")]


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
        raise ValueError("dias_utp vacio: no se publica una fotografia incompleta")
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
        history = bigquery.Table(f"{raw}.hist_{table}", schema=schema(kind))
        history.time_partitioning = bigquery.TimePartitioning(field="fecha_carga")
        client.create_table(history, exists_ok=True)
        query(f"""CREATE OR REPLACE VIEW `{raw}.{table}` AS
SELECT h.* EXCEPT(snapshot_id) FROM `{raw}.hist_{table}` h
WHERE snapshot_id = (SELECT snapshot_id FROM `{raw}.hist_mentores_catalog_snapshots`
                    ORDER BY published_at DESC, snapshot_id DESC LIMIT 1)""")


def publish(client, raw, run_id, source_project, query):
    rows = extract(source_project)
    stages = []
    try:
        for kind, table in TABLES.items():
            stage = f"{raw}.tmp_snapshot_{kind}_{run_id}"
            stages.append(stage)
            config = bigquery.LoadJobConfig(schema=schema(kind), write_disposition="WRITE_TRUNCATE")
            client.load_table_from_json([dict(v, snapshot_id=run_id) for v in rows[kind]], stage,
                                       job_config=config).result()
            query(f"ALTER TABLE `{stage}` SET OPTIONS(expiration_timestamp=TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 2 DAY))")
        inserts = '\n'.join(f"INSERT INTO `{raw}.hist_{table}` SELECT * FROM `{stage}`;"
                            for table, stage in zip(TABLES.values(), stages))
        query(f"""BEGIN TRANSACTION;
IF NOT EXISTS (SELECT 1 FROM `{raw}.hist_mentores_catalog_snapshots` WHERE snapshot_id=@run_id) THEN
{inserts}
INSERT INTO `{raw}.hist_mentores_catalog_snapshots` VALUES (@run_id, CURRENT_TIMESTAMP());
END IF;
COMMIT TRANSACTION;""")
    finally:
        for stage in stages:
            client.delete_table(stage, not_found_ok=True)

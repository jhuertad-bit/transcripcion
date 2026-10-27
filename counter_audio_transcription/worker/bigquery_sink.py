"""Opcional: usa una tabla existente; no crea datasets ni tablas."""
import json
import re


class BigQuerySink:
    def __init__(self, client, table_id, location):
        if not re.fullmatch(r"[a-z][a-z0-9-]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*", table_id):
            raise ValueError("BQ_RESULTS_TABLE debe ser project.dataset.table")
        if not location:
            raise ValueError("BQ_LOCATION requerido cuando se habilita BigQuery")
        self.client, self.table, self.location = client, table_id, location

    def write(self, result, result_uri):
        from google.cloud import bigquery
        values = {"processing_key": result["processing_key"], "recording_id": result["recording_id"],
                  "audio_uri": result["source"]["uri"], "audio_generation": result["source"]["generation"],
                  "result_uri": result_uri, "pipeline_version": result["models"]["pipeline_version"],
                  "duration_seconds": result["duration_seconds"], "elapsed_seconds": result["elapsed_seconds"],
                  "processed_at": result["processed_at"], "serialized_level_1": result["serialized_level_1"],
                  "serialized_level_2": json.dumps({"transcription": result["transcription"],
                                                     "catalog": result.get("catalog"),
                                                     "speaker_scope": "segment", "timestamp_scope": "segment"}, ensure_ascii=False)}
        types = {"duration_seconds": "FLOAT64", "elapsed_seconds": "FLOAT64", "processed_at": "TIMESTAMP"}
        fields = list(values)
        select = ", ".join((f"PARSE_JSON(@{f})" if f == "serialized_level_2" else f"@{f}") + f" AS {f}" for f in fields)
        update = ", ".join(f"{f}=S.{f}" for f in fields if f != "processing_key")
        sql = (f"MERGE `{self.table}` T USING (SELECT {select}) S ON T.processing_key=S.processing_key "
               f"WHEN MATCHED THEN UPDATE SET {update} "
               f"WHEN NOT MATCHED THEN INSERT ({', '.join(fields)}) VALUES ({', '.join('S.'+f for f in fields)})")
        parameters = [bigquery.ScalarQueryParameter(f, types.get(f, "STRING"), v) for f, v in values.items()]
        self.client.query(sql, location=self.location,
                          job_config=bigquery.QueryJobConfig(query_parameters=parameters)).result()

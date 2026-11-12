"""MERGE parametrizado sobre las tablas existentes; nunca crea objetos."""
import json
from worker.catalog import table_id


def merge(client, table, location, values, keys, types=None, json_fields=()):
    from google.cloud import bigquery
    table_id(table)
    if not location:
        raise ValueError("BQ_LOCATION requerido")
    types = types or {}
    fields = list(values)
    # Los resultados de modelos contienen floats que BigQuery puede rechazar en
    # modo exact. Conservar el JSON original en GCS y permitir FLOAT64 en BQ.
    # El catálogo mantiene modo exact para no redondear identificadores.
    def expression(field):
        if field == 'serialized_level_2' and field in json_fields:
            return f"PARSE_JSON(@{field}, wide_number_mode=>'round')"
        return f'PARSE_JSON(@{field})' if field in json_fields else f'@{field}'
    select = ', '.join(expression(f) + f' AS {f}' for f in fields)
    sql = (f'MERGE `{table}` T USING (SELECT {select}) S ON ' +
           ' AND '.join(f'T.{k}=S.{k}' for k in keys) +
           ' WHEN MATCHED THEN UPDATE SET ' + ', '.join(f'{f}=S.{f}' for f in fields if f not in keys) +
           f" WHEN NOT MATCHED THEN INSERT ({', '.join(fields)}) VALUES ({', '.join('S.'+f for f in fields)})")
    params = [bigquery.ScalarQueryParameter(f, types.get(f, 'STRING'), v) for f, v in values.items()]
    client.query(sql, location=location, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()


class BigQuerySink:
    def __init__(self, client, table, location):
        self.client, self.table, self.location = client, table_id(table), location

    def write(self, result, result_uri):
        c, models = result['catalog'], result['models']
        values = {
            'tipo_origen': c['tipo_origen'], 'recording_id': result['recording_id'],
            'processing_key': result['processing_key'], 'fecha_audio': c['fecha_audio'],
            'catalog_table': c['source_table'], 'file_name': c.get('file_name'),
            'folder_uuid': c.get('folder_uuid'), 'gcs_uri': result['source']['uri'],
            'audio_generation': result['source']['generation'], 'source_gcs_uri': c.get('source_gcs_uri'),
            'duration_seconds': result['duration_seconds'],
            'fecha_procesamiento_catalogo': c.get('fecha_procesamiento'),
            'catalog_metadata': json.dumps(c, ensure_ascii=False, allow_nan=False),
            'pipeline_version': models['pipeline_version'], 'modelo_transcripcion': models['whisper_model'],
            'modelo_diarizacion': models['diarization_model'] if models['enable_diarization'] else None,
            'idioma': result['language'], 'serialized_level_1': result['serialized_level_1'],
            'serialized_level_2': json.dumps(result, ensure_ascii=False, allow_nan=False),
            'result_uri': result_uri, 'processing_started_at': result.get('processing_started_at'),
            'processed_at': result['processed_at'], 'elapsed_seconds': result['elapsed_seconds'],
        }
        merge(self.client, self.table, self.location, values, ['processing_key'],
              {'fecha_audio': 'DATE', 'fecha_procesamiento_catalogo': 'TIMESTAMP',
               'processing_started_at': 'TIMESTAMP', 'processed_at': 'TIMESTAMP',
               'duration_seconds': 'FLOAT64', 'elapsed_seconds': 'FLOAT64'},
              ('catalog_metadata', 'serialized_level_2'))


class AttemptSink:
    def __init__(self, client, table, location):
        self.client, self.table, self.location = client, table_id(table), location

    def write(self, values):
        merge(self.client, self.table, self.location, values, ['attempt_id'],
              {'fecha_audio': 'DATE', 'created_at': 'TIMESTAMP', 'started_at': 'TIMESTAMP',
               'updated_at': 'TIMESTAMP', 'finished_at': 'TIMESTAMP', 'elapsed_seconds': 'FLOAT64'})

# Generado desde normalizadores locales; sin credenciales ni conexiones.
import json
import pandas as pd
from datetime import datetime, timezone
from urllib.parse import unquote, urlparse
from google.cloud import bigquery

ESQUEMAS = {'mentores': [('mentor_id', 'STRING'), ('firestore_path', 'STRING'), ('mentor_nombre', 'STRING'), ('mentor_email', 'STRING'), ('campus', 'STRING'), ('is_active', 'BOOLEAN'), ('datos_origen_json', 'STRING'), ('fecha_carga', 'TIMESTAMP')], 'audios': [('audio_id', 'STRING'), ('firestore_path', 'STRING'), ('mentor_id', 'STRING'), ('activityid', 'STRING'), ('promotor_email', 'STRING'), ('source_gcs_uri', 'STRING'), ('source_gcs_path', 'STRING'), ('duration_seconds', 'FLOAT'), ('created_at', 'TIMESTAMP'), ('tipo_charla_mentor', 'STRING'), ('tipos_charla_mentor_json', 'STRING'), ('latitude', 'FLOAT'), ('longitude', 'FLOAT'), ('datos_origen_json', 'STRING'), ('fecha_carga', 'TIMESTAMP')]}
DIAS_SCHEMA = [bigquery.SchemaField('doc_id', 'STRING', mode='REQUIRED'), bigquery.SchemaField('campus', 'STRING', mode='NULLABLE'), bigquery.SchemaField('colegio', 'STRING', mode='NULLABLE'), bigquery.SchemaField('codigoModular', 'STRING', mode='NULLABLE'), bigquery.SchemaField('tipoColegio', 'STRING', mode='NULLABLE'), bigquery.SchemaField('direccion', 'STRING', mode='NULLABLE'), bigquery.SchemaField('linkMaps', 'STRING', mode='NULLABLE'), bigquery.SchemaField('fechaEvento', 'TIMESTAMP', mode='NULLABLE'), bigquery.SchemaField('horaRecojo', 'STRING', mode='NULLABLE'), bigquery.SchemaField('cantidadAlumnos', 'INTEGER', mode='NULLABLE'), bigquery.SchemaField('cantidadProfesores', 'INTEGER', mode='NULLABLE'), bigquery.SchemaField('adultoResponsable', 'STRING', mode='NULLABLE'), bigquery.SchemaField('telefonoResponsable', 'STRING', mode='NULLABLE'), bigquery.SchemaField('promotorNombre', 'STRING', mode='NULLABLE'), bigquery.SchemaField('mentorCampusNombre', 'STRING', mode='NULLABLE'), bigquery.SchemaField('mentoresAsignados', 'STRING', mode='NULLABLE'), bigquery.SchemaField('estado', 'STRING', mode='NULLABLE'), bigquery.SchemaField('estadoAsignado', 'STRING', mode='NULLABLE'), bigquery.SchemaField('observacion', 'STRING', mode='NULLABLE'), bigquery.SchemaField('actualizadoEn', 'TIMESTAMP', mode='NULLABLE'), bigquery.SchemaField('fecha_carga', 'TIMESTAMP', mode='NULLABLE')]

def texto(valor):
    return None if valor is None else str(valor)

def fecha(valor):
    if valor is None or valor == '':
        return None
    if isinstance(valor, (int, float)):
        return datetime.fromtimestamp(valor / 1000, timezone.utc).isoformat()
    return datetime.fromisoformat(str(valor).replace('Z', '+00:00')).astimezone(timezone.utc).isoformat()

def ruta_storage(url):
    """Obtiene bucket y objeto exactos, sin conservar tokens de descarga."""
    if not url:
        return (None, None)
    p = urlparse(url)
    if p.scheme == 'gs':
        bucket, objeto = (p.netloc, p.path.lstrip('/'))
    elif p.netloc == 'firebasestorage.googleapis.com' and p.path.startswith('/v0/b/'):
        bucket, separador, objeto = p.path[len('/v0/b/'):].partition('/o/')
        if not separador:
            raise ValueError('remoteUrl no contiene /o/')
        bucket, objeto = (unquote(bucket), unquote(objeto))
    elif p.netloc == 'storage.googleapis.com':
        bucket, separador, objeto = p.path.lstrip('/').partition('/')
        if not separador:
            raise ValueError('remoteUrl no contiene objeto')
        objeto = unquote(objeto)
    else:
        raise ValueError('Formato remoteUrl no reconocido; revisar antes de cargar')
    if not bucket or not objeto:
        raise ValueError('Bucket u objeto vacío')
    return (f'gs://{bucket}/{objeto}', objeto)

def transformar(documento, tipo, fecha_carga):
    d = {k: decodificar(v) for k, v in documento.get('fields', {}).items()}
    ruta = documento['name'].split('/documents/', 1)[1]
    segmentos = ruta.split('/')
    origen = dict(d)
    origen.pop('remoteUrl', None)
    fila = {'firestore_path': ruta, 'datos_origen_json': json.dumps(origen, ensure_ascii=False), 'fecha_carga': fecha_carga}
    if tipo == 'mentores':
        fila.update(mentor_id=segmentos[-1], mentor_nombre=d.get('displayName') or d.get('name'), mentor_email=d.get('email'), campus=texto(d.get('campus')), is_active=d.get('isActive') if isinstance(d.get('isActive'), bool) else None)
    else:
        if len(segmentos) != 6 or segmentos[::2] != ['mentor_charlas', 'charlas', 'audios']:
            raise ValueError(f'Ruta de audio inesperada: {ruta}')
        mentor = texto(d.get('mentorId'))
        charla = texto(d.get('activityid'))
        if mentor != segmentos[1] or charla != segmentos[3]:
            raise ValueError(f'IDs del audio no coinciden con su ruta: {ruta}')
        uri, objeto = ruta_storage(d.get('remoteUrl'))
        fila.update(audio_id=segmentos[-1], mentor_id=mentor, activityid=charla, promotor_email=d.get('promotor_email'), source_gcs_uri=uri, source_gcs_path=objeto, duration_seconds=d.get('durationSeconds'), created_at=fecha(d.get('createdAt')), tipo_charla_mentor=d.get('tipoCharlaMentor'), tipos_charla_mentor_json=json.dumps(d.get('tiposCharlaMentor'), ensure_ascii=False), latitude=d.get('latitude'), longitude=d.get('longitude'))
    return fila

def decodificar(valor):
    if 'mapValue' in valor:
        return {k: decodificar(v) for k, v in valor['mapValue'].get('fields', {}).items()}
    if 'arrayValue' in valor:
        return [decodificar(v) for v in valor['arrayValue'].get('values', [])]
    if 'integerValue' in valor:
        return int(valor['integerValue'])
    return next(iter(valor.values()), None)

def transformar_datos(records):
    print('🔄 [2/3] Transformando y normalizando tipos de datos...')
    df = pd.DataFrame(records)
    if 'doc_id' not in df.columns:
        df['doc_id'] = ''
    else:
        df['doc_id'] = df['doc_id'].astype(str)
    str_cols = ['campus', 'linkMaps', 'mentorCampusNombre', 'promotorNombre', 'direccion', 'observacion', 'adultoResponsable', 'estado', 'horaRecojo', 'estadoAsignado', 'colegio', 'tipoColegio']
    for col in str_cols:
        if col in df.columns:
            df[col] = df[col].fillna('').astype(str).str.strip()
        else:
            df[col] = ''
    for col in ['codigoModular', 'telefonoResponsable']:
        if col in df.columns:
            df[col] = df[col].apply(lambda x: '' if pd.isna(x) or x is None else str(x).split('.')[0].strip())
        else:
            df[col] = ''
    for col in ['cantidadAlumnos', 'cantidadProfesores']:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype('int64')
        else:
            df[col] = 0
    for col in ['fechaEvento', 'actualizadoEn']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors='coerce')
        else:
            df[col] = pd.NaT
    if 'mentoresAsignados' in df.columns:
        df['mentoresAsignados'] = df['mentoresAsignados'].apply(lambda x: json.dumps(x, ensure_ascii=False) if isinstance(x, (list, dict)) else '[]')
    else:
        df['mentoresAsignados'] = '[]'
    df['fecha_carga'] = datetime.now(timezone.utc)
    ordered_cols = ['doc_id', 'campus', 'colegio', 'codigoModular', 'tipoColegio', 'direccion', 'linkMaps', 'fechaEvento', 'horaRecojo', 'cantidadAlumnos', 'cantidadProfesores', 'adultoResponsable', 'telefonoResponsable', 'promotorNombre', 'mentorCampusNombre', 'mentoresAsignados', 'estado', 'estadoAsignado', 'observacion', 'actualizadoEn', 'fecha_carga']
    for c in ordered_cols:
        if c not in df.columns:
            df[c] = None
    df = df[ordered_cols]
    print(f'    ✓ Dataset normalizado: {df.shape[0]} filas x {df.shape[1]} columnas.')
    return df

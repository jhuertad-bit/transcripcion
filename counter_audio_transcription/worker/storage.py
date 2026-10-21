import json
import uuid
from contextlib import contextmanager
from counter_common.contract import parse_gcs_uri


class BusyError(RuntimeError):
    pass


class GCSRepository:
    def __init__(self, client, audio_uri, output_root, max_bytes):
        bucket, name = parse_gcs_uri(audio_uri)
        current = client.bucket(bucket).blob(name)
        current.reload()  # Valida existencia y captura la generación antes de descargar.
        if current.size is None or not 0 < current.size <= max_bytes:
            raise ValueError("Tamaño del archivo fuera del límite configurado")
        self.source = {"uri": audio_uri, "generation": str(current.generation), "size_bytes": current.size}
        self.audio = client.bucket(bucket).blob(name, generation=int(current.generation))
        self.output_bucket, self.prefix = parse_gcs_uri(output_root.rstrip("/") + "/")
        self.prefix = self.prefix.rstrip("/")
        if not self.prefix:
            raise ValueError("OUTPUT_GCS_ROOT requiere un prefijo Counter dentro del bucket")
        self.bucket = client.bucket(self.output_bucket)

    def path(self, key, suffix):
        return f"{self.prefix}/{key}/{suffix}"

    def download(self, path):
        self.audio.download_to_filename(str(path), if_generation_match=int(self.source["generation"]))

    def read_json(self, key, suffix):
        from google.api_core.exceptions import NotFound
        try:
            return json.loads(self.bucket.blob(self.path(key, suffix)).download_as_text())
        except NotFound:
            return None

    def write_json(self, key, suffix, value, immutable=False):
        blob = self.bucket.blob(self.path(key, suffix))
        options = {"if_generation_match": 0} if immutable else {}
        blob.upload_from_string(json.dumps(value, ensure_ascii=False, allow_nan=False),
                                content_type="application/json; charset=utf-8", **options)
        return f"gs://{self.output_bucket}/{blob.name}"

    def write_text(self, key, suffix, text):
        blob = self.bucket.blob(self.path(key, suffix))
        blob.upload_from_string(text, content_type="text/plain; charset=utf-8", if_generation_match=0)
        return f"gs://{self.output_bucket}/{blob.name}"

    @contextmanager
    def lock(self, key):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(self.path(key, "processing.lock"))
        try:
            blob.upload_from_string(json.dumps({"owner": uuid.uuid4().hex}),
                                    content_type="application/json", if_generation_match=0)
        except PreconditionFailed as exc:
            raise BusyError("Ya existe una ejecución o un lock pendiente; consultar README") from exc
        generation = blob.generation
        try:
            yield
        finally:
            # Solo elimina el lock que creó esta ejecución.
            blob.delete(if_generation_match=int(generation))

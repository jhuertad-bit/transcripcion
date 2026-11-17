"""Immutable stage artifacts and renewable, generation-guarded GCS leases."""
from contextlib import contextmanager
import json
import threading
import time
import uuid

from counter_presencial.shared import parse_gcs_uri
from counter_presencial.config import digest


class LeaseLost(RuntimeError):
    pass


class Store:
    def __init__(self, client, root, ttl=300):
        self.client = client
        bucket, prefix = parse_gcs_uri(root.rstrip("/") + "/")
        if "presencial-vnext" not in prefix.split("/"):
            raise ValueError("Refusing to write outside an isolated VNext prefix")
        self.bucket, self.prefix, self.ttl = client.bucket(bucket), prefix, ttl

    def read(self, name):
        from google.api_core.exceptions import NotFound
        try:
            return json.loads(self.bucket.blob(self.prefix + name).download_as_text())
        except NotFound:
            return None

    def put(self, name, data):
        """Create once. Existing identical artifacts make a retry idempotent."""
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(self.prefix + name)
        try:
            blob.upload_from_string(json.dumps(data, ensure_ascii=False, allow_nan=False),
                                    content_type="application/json", if_generation_match=0)
        except PreconditionFailed:
            if digest(self.read(name)) != digest(data):
                raise ValueError("Immutable artifact differs: " + name)
        return f"gs://{self.bucket.name}/{blob.name}"

    def source(self, uri, allowed, max_bytes):
        bucket, name = parse_gcs_uri(uri)
        if bucket not in allowed:
            raise ValueError("Source bucket is not authorized")
        obj = self.client.bucket(bucket).blob(name)
        obj.reload()
        if obj.size is None or not 0 < obj.size <= max_bytes:
            raise ValueError("Source exceeds size budget or is empty")
        return {"uri": uri, "generation": str(obj.generation), "size_bytes": obj.size}

    def put_text(self, name, text):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(self.prefix + name)
        try:
            blob.upload_from_string(text, content_type="text/plain; charset=utf-8", if_generation_match=0)
        except PreconditionFailed:
            if blob.download_as_text() != text:
                raise ValueError("Immutable text artifact differs")
        return f"gs://{self.bucket.name}/{blob.name}"

    def download(self, part, target):
        bucket, name = parse_gcs_uri(part["uri"])
        generation = int(part["generation"])
        self.client.bucket(bucket).blob(name, generation=generation).download_to_filename(
            str(target), if_generation_match=generation)

    @contextmanager
    def lease(self, key):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        blob = self.bucket.blob(self.prefix + "leases/" + key + ".json")
        owner = uuid.uuid4().hex
        mutex, stop = threading.Lock(), threading.Event()
        state = {"generation": None, "lost": False}

        def payload():
            return json.dumps({"owner": owner, "expires_at": time.time() + self.ttl})

        try:
            blob.upload_from_string(payload(), if_generation_match=0)
        except PreconditionFailed:
            blob.reload()
            generation = int(blob.generation)
            current = json.loads(blob.download_as_text(if_generation_match=generation))
            if current["expires_at"] > time.time():
                raise LeaseLost("Stage is already owned by another worker")
            try:
                blob.upload_from_string(payload(), if_generation_match=generation)
            except PreconditionFailed as exc:
                raise LeaseLost("Another worker acquired the expired lease") from exc
        state["generation"] = int(blob.generation)

        def check():
            with mutex:
                if state["lost"]:
                    raise LeaseLost("Lease renewal failed; result cannot be published")
                # Check current ownership immediately before publishing.
                try:
                    current = json.loads(blob.download_as_text(
                        if_generation_match=state["generation"]))
                except (NotFound, PreconditionFailed) as exc:
                    raise LeaseLost("Lease ownership changed") from exc
                if current["owner"] != owner or current["expires_at"] <= time.time():
                    raise LeaseLost("Lease expired or ownership changed")

        def renew():
            while not stop.wait(self.ttl / 3):
                try:
                    with mutex:
                        blob.upload_from_string(payload(), if_generation_match=state["generation"])
                        state["generation"] = int(blob.generation)
                except Exception:
                    with mutex:
                        state["lost"] = True
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            yield check
        finally:
            stop.set()
            thread.join()
            try:
                blob.delete(if_generation_match=state["generation"])
            except (NotFound, PreconditionFailed):
                pass  # Never remove another worker's lease.

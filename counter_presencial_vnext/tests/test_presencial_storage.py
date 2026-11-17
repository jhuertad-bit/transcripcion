"""Conditional GCS writes, lease takeover and lost-owner protection, offline."""
import json
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from counter_presencial.storage import LeaseLost, Store


class NotFound(Exception):
    pass


class PreconditionFailed(Exception):
    pass


class Bucket:
    name = "output"

    def __init__(self):
        self.docs, self.counter = {}, 0

    def blob(self, name, generation=None):
        return Blob(self, name, generation)


class Blob:
    def __init__(self, bucket, name, generation=None):
        self.bucket, self.name, self.generation = bucket, name, generation

    def upload_from_string(self, data, if_generation_match=None, **kwargs):
        current = self.bucket.docs.get(self.name)
        if if_generation_match == 0 and current is not None:
            raise PreconditionFailed()
        if if_generation_match not in (None, 0) and (current is None or current[1] != if_generation_match):
            raise PreconditionFailed()
        self.bucket.counter += 1
        self.generation = self.bucket.counter
        self.bucket.docs[self.name] = (data, self.generation)

    def reload(self):
        if self.name not in self.bucket.docs:
            raise NotFound()
        self.generation = self.bucket.docs[self.name][1]

    def download_as_text(self, if_generation_match=None):
        if self.name not in self.bucket.docs:
            raise NotFound()
        text, generation = self.bucket.docs[self.name]
        if if_generation_match is not None and generation != if_generation_match:
            raise PreconditionFailed()
        return text

    def delete(self, if_generation_match=None):
        self.download_as_text(if_generation_match)
        del self.bucket.docs[self.name]


class LeaseTests(unittest.TestCase):
    def setUp(self):
        self.bucket = Bucket()
        self.store = Store(type("Client", (), {"bucket": lambda _, name: self.bucket})(),
                           "gs://output/counter/presencial-vnext")
        module = ModuleType("google.api_core.exceptions")
        module.NotFound, module.PreconditionFailed = NotFound, PreconditionFailed
        self.patch = patch.dict(sys.modules, {"google.api_core.exceptions": module})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_active_lease_cannot_be_acquired_twice(self):
        with self.store.lease("key") as check:
            check()
            with self.assertRaises(LeaseLost):
                with self.store.lease("key"):
                    self.fail("Acquired active lease")
        self.assertEqual(self.bucket.docs, {})

    def test_expired_lease_can_be_recovered(self):
        path = "counter/presencial-vnext/leases/key.json"
        self.bucket.blob(path).upload_from_string(json.dumps({"owner": "dead", "expires_at": 0}))
        with self.store.lease("key") as check:
            check()
            owner = json.loads(self.bucket.docs[path][0])["owner"]
            self.assertNotEqual(owner, "dead")
        self.assertNotIn(path, self.bucket.docs)

    def test_lost_owner_cannot_publish_or_delete_successor_lease(self):
        path = "counter/presencial-vnext/leases/key.json"
        with self.store.lease("key") as check:
            original_generation = self.bucket.docs[path][1]
            other = self.bucket.blob(path)
            other.upload_from_string(json.dumps({"owner": "successor", "expires_at": 99999999999}),
                                     if_generation_match=original_generation)
            with self.assertRaises(LeaseLost):
                check()
        self.assertEqual(json.loads(self.bucket.docs[path][0])["owner"], "successor")

    def test_artifacts_are_create_only_with_exact_idempotent_retries(self):
        self.store.put("artifact.json", {"value": 1})
        self.store.put("artifact.json", {"value": 1})
        with self.assertRaises(ValueError):
            self.store.put("artifact.json", {"value": 2})
        self.assertEqual(self.store.read("artifact.json"), {"value": 1})
        self.store.put_text("transcript.txt", "CLIENTE: Sí.")
        self.store.put_text("transcript.txt", "CLIENTE: Sí.")
        with self.assertRaises(ValueError):
            self.store.put_text("transcript.txt", "ASESOR: Sí.")


if __name__ == "__main__":
    unittest.main()

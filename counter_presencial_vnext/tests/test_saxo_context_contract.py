"""Preserve SAXO's contextual interventions without inventing physical identity."""
from copy import deepcopy
import json
import unittest

from counter_presencial.acoustics import make_turns
from counter_presencial.fusion import fuse, second_pass
from counter_presencial.roles import classify
from test_presencial_vnext import config, decision, turn


def chunk(tokens, gap_at=None):
    words = []
    for index, token in enumerate(tokens):
        start = index * .3 + (1. if gap_at is not None and index >= gap_at else 0.)
        words.append({"word": token, "start": start, "end": start + .2,
                      "speaker": "UNTRUSTED_UPSTREAM_LABEL"})
    return {"text": " ".join(tokens), "words": words,
            "timestamp_status": "native_ctc_verified"}


class SaxoContextContractTests(unittest.TestCase):
    def test_question_and_reply_use_saxo_punctuation_including_typographic_quotes(self):
        source = chunk(["¿Qué", "carrera", "buscas?’", "Ingeniería."])
        rows = make_turns([source])
        self.assertEqual([r["text"] for r in rows], ["¿Qué carrera buscas?’", "Ingeniería."])
        self.assertEqual([r["boundary_reason"] for r in rows], ["punctuation"] * 2)
        self.assertEqual([w for r in rows for w in r["words"]], source["words"])
        self.assertEqual(rows[1]["start"], source["words"][3]["start"])
        self.assertTrue(all("speaker_id" not in r for r in rows))

    def test_pause_and_length_cut_preserve_every_word_once(self):
        source = chunk(["uno", "dos", "tres", "cuatro", "cinco"], gap_at=2)
        rows = make_turns([source], max_words=2)
        self.assertEqual([r["text"] for r in rows], ["uno dos", "tres cuatro", "cinco"])
        self.assertEqual([r["word_count"] for r in rows], [2, 2, 1])
        self.assertEqual([w for r in rows for w in r["words"]], source["words"])
        self.assertEqual([r["turn_id"] for r in rows], ["T00001", "T00002", "T00003"])

    def test_word_limit_does_not_remove_short_replies_or_repeated_words(self):
        source = chunk(["sí", "sí", "ya", "bien"], gap_at=2)
        rows = make_turns([source])
        self.assertEqual([r["text"] for r in rows], ["sí sí", "ya bien"])
        self.assertEqual([r["boundary_reason"] for r in rows], ["pause", "end_of_input"])
        self.assertEqual([w for r in rows for w in r["words"]], source["words"])

    def test_contextual_roles_remain_per_intervention_without_an_acoustic_mapping(self):
        inputs = [turn("T1", "¿Qué carrera buscas?", speaker_id="WRONG", start=0., end=1.),
                  turn("T2", "Ingeniería.", speaker_id="WRONG", start=2., end=3.),
                  turn("T3", "Quiero información para mi hija.", speaker_id="WRONG", start=4., end=5.)]
        unchanged = deepcopy(inputs)
        choices = {"T1": "ASESOR", "T2": "CLIENTE", "T3": "CLIENTE"}

        def provider(prompt):
            data = json.loads(prompt.split("INPUT_JSON:\n", 1)[1])
            self.assertEqual([t["turn_id"] for t in data["turns"]], ["T1", "T2", "T3"])
            self.assertNotIn("WRONG", prompt)
            return {"decisions": [decision(i, choices[i]) for i in data["target_turn_ids"]]}

        classified = classify(inputs, provider, lambda s: s, config(targets_per_call=1))
        result = second_pass(fuse(classified, []), provider, config())
        self.assertEqual([r["role_final"] for r in result["turns"]], ["ASESOR", "CLIENTE", "CLIENTE"])
        self.assertTrue(all(r["speaker_id"] is None for r in result["turns"]))
        self.assertEqual(inputs, unchanged)
        self.assertEqual([(r["text"], r["start"], r["end"]) for r in result["turns"]],
                         [(r["text"], r["start"], r["end"]) for r in inputs])
        self.assertEqual(result["attribution_method"], "text_context_only")
        self.assertFalse(result["speaker_identity_verified"])

    def test_two_acoustic_clients_are_not_collapsed_into_one_person(self):
        inputs = [turn("T1", start=0., end=1.), turn("T2", start=2., end=3.)]

        def provider(prompt):
            data = json.loads(prompt.split("INPUT_JSON:\n", 1)[1])
            return {"decisions": [decision(i, "CLIENTE") for i in data["target_turn_ids"]]}

        result = fuse(classify(inputs, provider, lambda s: s, config()), [
            {"speaker_id": "SPEAKER_00", "start": 0., "end": 1.},
            {"speaker_id": "SPEAKER_01", "start": 2., "end": 3.}])
        self.assertEqual([r["role_final"] for r in result["turns"]], ["CLIENTE", "CLIENTE"])
        self.assertEqual([r["speaker_id"] for r in result["turns"]], ["SPEAKER_00", "SPEAKER_01"])

    def test_mixed_unaligned_block_is_preserved_for_review_without_invented_times(self):
        text = "¿Qué carrera buscas? Ingeniería"
        inputs = make_turns([{"text": text, "words": [], "timestamp_status": "missing"}])

        def provider(prompt):
            return {"decisions": [decision("T00001", mixed_roles=True)]}

        result = second_pass(fuse(classify(inputs, provider, lambda s: s, config()), []), provider, config())
        self.assertEqual(len(result["turns"]), 1)
        row = result["turns"][0]
        self.assertEqual(row["text"], text)
        self.assertIsNone(row["start"])
        self.assertIsNone(row["end"])
        self.assertEqual(row["role_final"], "INCIERTO")
        self.assertTrue(row["requires_review"])


if __name__ == "__main__":
    unittest.main()

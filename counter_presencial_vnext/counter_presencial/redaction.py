"""Fail-closed local pseudonymization. No original text in provider requests."""
import re

from counter_presencial.prompts import REDACTION_VERSION


class Redactor:
    version = REDACTION_VERSION

    def __init__(self, engine=None):
        if engine is None:
            from presidio_analyzer import AnalyzerEngine
            from presidio_analyzer.nlp_engine import NlpEngineProvider
            nlp = NlpEngineProvider(nlp_configuration={"nlp_engine_name": "spacy", "models": [
                {"lang_code": "es", "model_name": "es_core_news_md"}]}).create_engine()
            engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["es"])
        self.engine = engine
        self.labels = {}

    def new_conversation(self):
        # Reuse the costly NLP engine, never the cross-conversation identity map.
        return type(self)(engine=self.engine)

    def __call__(self, text):
        spans = [(r.start, r.end, r.entity_type) for r in self.engine.analyze(text=text, language="es")]
        for pattern, kind in (
                (r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "EMAIL_ADDRESS"),
                (r"(?<!\d)(?:\+?51[ -]?)?9(?:\d[ -]?){7}\d(?!\d)", "PHONE_NUMBER"),
                (r"(?<!\d)\d{8}(?!\d)", "DOCUMENT")):
            spans.extend((m.start(), m.end(), kind) for m in re.finditer(pattern, text))
        # Union overlaps; never reintroduce a partially redacted entity.
        merged = []
        for a, b, kind in sorted(spans, key=lambda x: (x[0], -x[1])):
            if merged and a < merged[-1][1]:
                merged[-1] = (merged[-1][0], max(b, merged[-1][1]), merged[-1][2])
            else:
                merged.append((a, b, kind))
        output, position = [], 0
        for a, b, kind in merged:
            identity = (kind, text[a:b].casefold())
            if identity not in self.labels:
                index = 1 + sum(k[0] == kind for k in self.labels)
                self.labels[identity] = f"[{kind}_{index}]"
            output.extend((text[position:a], self.labels[identity]))
            position = b
        output.append(text[position:])
        return "".join(output)

from pathlib import Path
import unittest


class EncodingTests(unittest.TestCase):
    def test_deployment_sources_are_utf8(self):
        root = Path(__file__).resolve().parents[1]
        for path in root.rglob('*'):
            if path.is_file() and path.suffix in ('.py', '.yaml', '.sql', '.md', '.sh'):
                with self.subTest(path=str(path.relative_to(root))):
                    path.read_text(encoding='utf-8')

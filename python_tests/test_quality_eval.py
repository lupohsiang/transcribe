from pathlib import Path
import runpy
import unittest


EVALUATOR = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts/evaluate-quality.py'))


class QualityEvaluatorTests(unittest.TestCase):
    def test_counts_substitution_deletion_and_insertion_against_literal_reference(self):
        errors = EVALUATOR['character_errors']
        self.assertEqual(errors('甲乙丙', '甲丁丙'), {
            'edits': 1, 'substitutions': 1, 'deletions': 0, 'insertions': 0, 'cer': 1 / 3,
        })
        self.assertEqual(errors('甲乙丙', '甲丙'), {
            'edits': 1, 'substitutions': 0, 'deletions': 1, 'insertions': 0, 'cer': 1 / 3,
        })
        self.assertEqual(errors('甲丙', '甲乙丙'), {
            'edits': 1, 'substitutions': 0, 'deletions': 0, 'insertions': 1, 'cer': 1 / 2,
        })

    def test_normalizes_unicode_case_punctuation_and_whitespace(self):
        self.assertEqual(EVALUATOR['normalize']('Hello，世界！１２３\n'), 'hello世界123')


if __name__ == '__main__':
    unittest.main()

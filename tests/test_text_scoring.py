import importlib.util
from pathlib import Path
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'code-excerpts/retrieval/text_scoring.py'
spec = importlib.util.spec_from_file_location('text_scoring', MODULE)
scoring = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scoring)


class TextScoringTests(unittest.TestCase):
    def test_blank_and_punctuation_queries_have_zero_score(self):
        for query in ('', '  ', '？！'):
            self.assertEqual(scoring._light_rerank_score(query, '课程介绍'), 0)

    def test_matching_subject_outscores_unrelated_text(self):
        relevant = scoring._light_rerank_score('英语阅读', '英语阅读能力提升')
        unrelated = scoring._light_rerank_score('英语阅读', '数学几何练习')
        self.assertGreater(relevant, unrelated)

    def test_title_is_searchable(self):
        self.assertGreater(scoring._light_rerank_score('阅读课程', '', '阅读课程介绍'), 0)

    def test_latin_matching_ignores_case(self):
        self.assertEqual(scoring._chars('ABC'), scoring._chars('abc'))

    def test_ngrams_do_not_bridge_punctuation(self):
        terms = scoring._chinese_ngrams('英语，数学')
        self.assertIn('英语', terms)
        self.assertIn('数学', terms)
        self.assertNotIn('语数', terms)

    def test_repeated_ngrams_are_unique(self):
        terms = scoring._chinese_ngrams('英语英语英语')
        self.assertEqual(len(terms), len(set(terms)))

    def test_short_input_does_not_invent_terms(self):
        self.assertEqual(scoring._chinese_ngrams('英'), [])


if __name__ == '__main__':
    unittest.main()

import unittest

from surf_chatgpt.models import normalize_model_choice


class ModelChoiceTests(unittest.TestCase):
    def test_model_and_thinking_queries_remain_independent(self):
        choice = normalize_model_choice("gpt-5.6-sol", "pro")

        self.assertEqual(choice.model_query, "gpt-5.6-sol")
        self.assertEqual(choice.thinking_query, "pro")

    def test_pro_is_preserved_in_its_requested_dimension(self):
        model = normalize_model_choice("pro", None)
        thinking = normalize_model_choice(None, "pro")

        self.assertEqual(model.model_query, "pro")
        self.assertIsNone(model.thinking_query)
        self.assertIsNone(thinking.model_query)
        self.assertEqual(thinking.thinking_query, "pro")

    def test_thinking_query_is_not_mapped(self):
        self.assertEqual(normalize_model_choice(None, "extra-high").thinking_query, "extra-high")

    def test_model_suffix_is_not_parsed_as_thinking(self):
        choice = normalize_model_choice("gpt-5.5:high", None)

        self.assertEqual(choice.model_query, "gpt-5.5:high")
        self.assertIsNone(choice.thinking_query)

    def test_blank_queries_normalize_to_none(self):
        choice = normalize_model_choice("  ", "\t")

        self.assertIsNone(choice.model_query)
        self.assertIsNone(choice.thinking_query)

    def test_special_queries_are_preserved(self):
        choice = normalize_model_choice("latest", "highest")

        self.assertEqual(choice.model_query, "latest")
        self.assertEqual(choice.thinking_query, "highest")


if __name__ == "__main__":
    unittest.main()

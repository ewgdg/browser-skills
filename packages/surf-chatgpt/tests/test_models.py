import unittest

from surf_chatgpt.errors import SkillError
from surf_chatgpt.models import normalize_model_choice, normalize_model_selector


class ModelNormalizationTests(unittest.TestCase):
    def test_thinking_maps_to_web_labels(self):
        self.assertEqual(normalize_model_choice(None, "low").thinking_label, "Instant")
        self.assertEqual(normalize_model_choice(None, "medium").thinking_label, "Medium")
        self.assertEqual(normalize_model_choice(None, "high").thinking_label, "High")
        self.assertEqual(normalize_model_choice(None, "highest").thinking_label, "highest")

    def test_model_is_forwarded_as_fuzzy_query(self):
        self.assertEqual(normalize_model_selector("pro").model_query, "pro")
        self.assertEqual(normalize_model_selector("gpt-5.5").model_query, "gpt-5.5")
        self.assertEqual(normalize_model_selector("gpt-5.4-pro").model_query, "gpt-5.4-pro")

    def test_model_suffix_can_include_thinking_level(self):
        choice = normalize_model_selector("gpt-5.5:high")
        self.assertEqual(choice.model_query, "gpt-5.5")
        self.assertEqual(choice.thinking_label, "High")

    def test_latest_model_highest_thinking_are_special_selectors(self):
        choice = normalize_model_choice("latest", "highest")
        self.assertEqual(choice.model_query, "latest")
        self.assertEqual(choice.thinking_label, "highest")

        suffixed = normalize_model_selector("latest:highest")
        self.assertEqual(suffixed.model_query, "latest")
        self.assertEqual(suffixed.thinking_label, "highest")

    def test_matching_model_and_thinking_allowed(self):
        choice = normalize_model_choice("gpt-5.5:high", "high")
        self.assertEqual(choice.model_query, "gpt-5.5")
        self.assertEqual(choice.thinking_label, "High")

    def test_conflicting_model_and_thinking_rejected(self):
        with self.assertRaises(SkillError) as ctx:
            normalize_model_choice("gpt-5.5:high", "medium")
        self.assertEqual(ctx.exception.type, "invalid_args")

    def test_unknown_model_is_left_for_browser_fuzzy_matching(self):
        self.assertEqual(normalize_model_selector("o3-pro").model_query, "o3-pro")


if __name__ == "__main__":
    unittest.main()

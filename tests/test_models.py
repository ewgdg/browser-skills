import unittest

from surf_chatgpt.errors import SkillError
from surf_chatgpt.models import normalize_model_choice, normalize_model_selector


class ModelNormalizationTests(unittest.TestCase):
    def test_thinking_maps_to_gpt55_web_labels_not_surf_pro(self):
        self.assertEqual(normalize_model_choice(None, "low").thinking_label, "Instant")
        self.assertEqual(normalize_model_choice(None, "medium").thinking_label, "Medium")
        self.assertEqual(normalize_model_choice(None, "high").thinking_label, "High")
        self.assertIsNone(normalize_model_choice(None, "high").surf_model_token)

    def test_legacy_surf_model_tokens_pass_through(self):
        self.assertEqual(normalize_model_selector("instant").surf_model_token, "instant")
        self.assertEqual(normalize_model_selector("thinking").surf_model_token, "thinking")
        self.assertEqual(normalize_model_selector("pro").surf_model_token, "pro")

    def test_gpt55_colon_forms_map_by_suffix_to_thinking_labels(self):
        self.assertEqual(normalize_model_selector("gpt5.5:low").thinking_label, "Instant")
        self.assertEqual(normalize_model_selector("gpt5.5:medium").thinking_label, "Medium")
        self.assertEqual(normalize_model_selector("gpt5.5:high").thinking_label, "High")

    def test_matching_model_and_thinking_allowed(self):
        self.assertEqual(normalize_model_choice("gpt5.5:high", "high").thinking_label, "High")

    def test_conflicting_model_and_thinking_rejected(self):
        with self.assertRaises(SkillError) as ctx:
            normalize_model_choice("pro", "medium")
        self.assertEqual(ctx.exception.type, "invalid_args")

    def test_unknown_model_rejected(self):
        with self.assertRaises(SkillError) as ctx:
            normalize_model_selector("gpt5.5:ultra")
        self.assertEqual(ctx.exception.type, "model_unavailable")


if __name__ == "__main__":
    unittest.main()

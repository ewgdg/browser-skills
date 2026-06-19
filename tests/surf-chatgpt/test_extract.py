import unittest

from surf_chatgpt.extract import clean_response, enforce_budget


class ExtractTests(unittest.TestCase):
    def test_strips_ui_noise_and_compacts_text(self):
        raw = """  Useful answer.  


Copy
Good response

Next line.  """
        self.assertEqual(clean_response(raw), "Useful answer.\n\nNext line.")

    def test_preserves_code_fence_content(self):
        raw = """Here:

```python
x = 1    # keep spaces
print(x)
```
Copy
Done."""
        cleaned = clean_response(raw)
        self.assertIn("```python\nx = 1    # keep spaces\nprint(x)\n```", cleaned)
        self.assertNotIn("Copy", cleaned)

    def test_truncates_chars(self):
        result = enforce_budget("abcdef", max_chars=4)
        self.assertTrue(result.truncated)
        self.assertEqual(result.answer, "abc…")
        self.assertEqual(result.chars, 4)

    def test_truncates_words_before_chars(self):
        result = enforce_budget("one two three four", max_chars=100, max_words=2)
        self.assertTrue(result.truncated)
        self.assertEqual(result.answer, "one two…")


if __name__ == "__main__":
    unittest.main()

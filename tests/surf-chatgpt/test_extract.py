import unittest

from surf_chatgpt.extract import clean_response


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


if __name__ == "__main__":
    unittest.main()

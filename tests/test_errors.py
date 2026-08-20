import unittest

from app.services.errors import safe_provider_message, sanitize_diagnostic


class SanitizeDiagnosticTests(unittest.TestCase):
    def test_redacts_key_value_secrets_regardless_of_separator_or_case(self):
        self.assertEqual(sanitize_diagnostic("api_key=sk-12345 failed request"), "api_key=[REDACTED] failed request")
        self.assertEqual(sanitize_diagnostic("API-KEY: sk-98765"), "API-KEY=[REDACTED]")
        self.assertEqual(sanitize_diagnostic("password: hunter2 for admin"), "password=[REDACTED] for admin")

    def test_redacts_query_string_tokens(self):
        value = sanitize_diagnostic("Request to https://example.com/x?access_token=abc123 failed")
        self.assertNotIn("abc123", value)
        self.assertIn("access_token=[REDACTED]", value)

    def test_redacts_url_embedded_credentials(self):
        value = sanitize_diagnostic("https://user:pass@example.com/resource")
        self.assertNotIn("user:pass", value)
        self.assertTrue(value.startswith("https://[REDACTED]@"))

    def test_strips_null_bytes(self):
        self.assertEqual(sanitize_diagnostic("null byte \x00 here"), "null byte  here")

    def test_leaves_non_sensitive_text_untouched(self):
        message = "plain error with no secrets at all"
        self.assertEqual(sanitize_diagnostic(message), message)

    def test_truncates_to_limit(self):
        self.assertEqual(len(sanitize_diagnostic("x" * 2000, limit=50)), 50)

    def test_accepts_non_string_error_objects(self):
        self.assertEqual(sanitize_diagnostic(ValueError("token=abc123")), "token=[REDACTED]")


class SafeProviderMessageTests(unittest.TestCase):
    def test_includes_action_and_no_partial_action_disclaimer(self):
        message = safe_provider_message("search jobs")
        self.assertIn("search jobs", message)
        self.assertIn("No unsafe partial action was taken", message)


if __name__ == "__main__":
    unittest.main()

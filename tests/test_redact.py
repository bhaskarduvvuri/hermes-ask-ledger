import unittest

from plugin.redact import redact

# Fake tokens are assembled at runtime so no literal token shape sits in the source.
SK = "sk" + "-proj-" + "abc123DEF456ghi789JKL012"
GHP = "gh" + "p_" + "a" * 36
PAT = "github" + "_pat_" + "11ABCDEFG0123456789_abcdefXYZ"
XOX = "xo" + "xb-" + "1234567890-abcdefghij"
AWS = "AK" + "IA" + "IOSFODNN7EXAMPLE"


class RedactTest(unittest.TestCase):
    def assertRedacted(self, text, secret):
        out = redact(text)
        self.assertNotIn(secret, out)
        self.assertIn("[redacted]", out)

    def test_plain_text_unchanged(self):
        self.assertEqual(redact("Summarise the Q3 report please"), "Summarise the Q3 report please")

    def test_openai_style_key(self):
        self.assertRedacted(f"use {SK} now", SK)

    def test_github_tokens(self):
        self.assertRedacted(f"token {GHP}", GHP)
        self.assertRedacted(PAT, PAT)

    def test_slack_token(self):
        self.assertRedacted(XOX, XOX)

    def test_aws_key_id(self):
        self.assertRedacted(f"key {AWS} here", AWS)

    def test_jwt(self):
        jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
        self.assertRedacted("auth " + jwt, jwt)

    def test_bearer(self):
        self.assertRedacted("Authorization: Bearer abcdef1234567890xyz", "abcdef1234567890xyz")

    def test_assignment(self):
        self.assertRedacted("set password=hunter2secret and go", "hunter2secret")
        self.assertRedacted("api_key: 'q8w7e6r5t4'", "q8w7e6r5t4")

    def test_pem_block(self):
        pem = "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBg\n-----END PRIVATE KEY-----"
        self.assertRedacted("here " + pem, "MIIEvQIBADANBg")

    def test_long_hex(self):
        self.assertRedacted("secret " + "0123456789abcdef" * 3, "0123456789abcdef" * 3)

    def test_short_numbers_kept(self):
        self.assertEqual(redact("order 12345 on 2026-11-05"), "order 12345 on 2026-11-05")

    def test_non_string(self):
        self.assertEqual(redact(None), "")


if __name__ == "__main__":
    unittest.main()

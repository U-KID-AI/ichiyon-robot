"""Credential formatting regressions; no real secrets or live databases."""
import base64
import hashlib
import hmac
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rotate_database_admin import parse_env, replace_password, scram_verifier


class CredentialFormatting(unittest.TestCase):
    def fixture(self):
        return (b"# fixture only\r\nPOSTGRES_USER=fixture\r\nPOSTGRES_DB=fixture\r\n"
                b"POSTGRES_PASSWORD='old-test-only'\r\nDATABASE_URL=\"postgresql://fixture:old-test-only@db:5432/fixture?application_name=migrate\"\r\n"
                b"RUNTIME_DATABASE_URL=unchanged-runtime-fixture\r\nOTHER_SETTING=unchanged\r\n")

    def test_preserves_unrelated_bytes(self):
        updated = replace_password(self.fixture(), "new-test-only", "fixture")
        self.assertIn(b"# fixture only\r\n", updated)
        self.assertIn(b"RUNTIME_DATABASE_URL=unchanged-runtime-fixture\r\nOTHER_SETTING=unchanged\r\n", updated)
        self.assertEqual(parse_env(updated)["DATABASE_URL"], "postgresql://fixture:new-test-only@db:5432/fixture?application_name=migrate")

    def test_duplicate_keys_rejected(self):
        with self.assertRaises(ValueError):
            parse_env(self.fixture() + b"POSTGRES_PASSWORD=duplicate\n")

    def test_role_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            replace_password(self.fixture(), "new-test-only", "another_role")

    def test_credential_mismatch_rejected(self):
        with self.assertRaises(ValueError):
            replace_password(self.fixture().replace(b"POSTGRES_PASSWORD='old-test-only'", b"POSTGRES_PASSWORD='different'"), "new-test-only", "fixture")

    def test_url_characters_are_encoded(self):
        updated = parse_env(replace_password(self.fixture(), "test@:/?#only", "fixture"))
        self.assertIn("test%40%3A%2F%3F%23only@db", updated["DATABASE_URL"])

    def test_scram_keys_validate(self):
        password = "test-only-not-a-secret"
        verifier = scram_verifier(password, b"fixture-salt-1234")
        prefix, params, keys = verifier.split("$")
        iterations, salt = params.split(":")
        stored, server = keys.split(":")
        salted = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
        self.assertEqual(prefix, "SCRAM-SHA-256")
        self.assertEqual(base64.b64decode(stored), hashlib.sha256(hmac.digest(salted, b"Client Key", "sha256")).digest())
        self.assertEqual(base64.b64decode(server), hmac.digest(salted, b"Server Key", "sha256"))


if __name__ == "__main__":
    unittest.main()

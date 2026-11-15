"""Offline connection-error classification and credential-redaction checks."""
import unittest
from scripts.check_mongodb import failure_hint


def error_named(name, message, code=None):
    error = type(name, (Exception,), {})(message)
    error.code = code
    return error


class MongoDiagnosticTests(unittest.TestCase):
    def test_database_authentication_failure_is_actionable(self):
        self.assertEqual(failure_hint(error_named("OperationFailure", "bad auth", 8000))[0], "AUTH_FAILED")
        self.assertEqual(failure_hint(error_named("OperationFailure", "Failure", 18))[0], "AUTH_FAILED")

    def test_authorization_is_not_password_or_network_failure(self):
        self.assertEqual(failure_hint(error_named("OperationFailure", "not authorized", 13))[0], "PERMISSION_DENIED")

    def test_network_timeout_keeps_multiple_possible_causes(self):
        code, hint = failure_hint(error_named("ServerSelectionTimeoutError", "timed out"))
        self.assertEqual(code, "CONNECTION_TIMEOUT")
        self.assertIn("IP", hint)
        self.assertIn("firewall", hint)

    def test_dns_and_tls_are_distinguished(self):
        self.assertEqual(failure_hint(error_named("ConfigurationError", "DNS resolution lifetime expired"))[0], "DNS_ERROR")
        self.assertEqual(failure_hint(error_named("ServerSelectionTimeoutError", "[SSL: CERTIFICATE_VERIFY_FAILED]"))[0], "TLS_ERROR")

    def test_transaction_and_database_configuration_errors_are_distinguished(self):
        self.assertEqual(failure_hint(RuntimeError("MongoDB transactions require a replica set or Atlas cluster."))[0], "TRANSACTIONS_REQUIRED")
        self.assertEqual(failure_hint(RuntimeError("Set MONGODB_DB to an application database name."))[0], "INVALID_DATABASE_NAME")
        self.assertEqual(failure_hint(error_named("InvalidURI", "wrong uri"))[0], "INVALID_CONFIG")

    def test_no_error_classification_exposes_raw_credentials_or_uri(self):
        secret = "mongodb+srv://demo:veryPrivateExampleSecret@private-cluster.invalid/"
        for kind, message in (
            ("Exception", secret),
            ("InvalidURI", "invalid uri " + secret),
            ("OperationFailure", "Authentication failed " + secret),
            ("ServerSelectionTimeoutError", "DNS error " + secret),
        ):
            output = " ".join(failure_hint(error_named(kind, message)))
            self.assertNotIn(secret, output)
            self.assertNotIn("veryPrivateExampleSecret", output)
            self.assertNotIn("private-cluster.invalid", output)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.auth.google_oauth import InvalidGoogleIdentity, validate_google_claims
from app.auth.session import clear_auth_state, set_active_identity
from app.services.demo import DEMO_USER_ID


class GoogleOAuthTests(unittest.TestCase):
    def setUp(self):
        self.claims = {
            "iss": "https://accounts.google.com",
            "sub": "google-subject-1",
            "email": "Ada@Example.com",
            "email_verified": True,
            "name": "Ada Student",
            "picture": "https://example.com/ada.png",
            "aud": "client-id",
            "azp": "client-id",
            "iat": 900,
            "exp": 1100,
        }

    def test_accepts_current_google_claims(self):
        identity = validate_google_claims(
            self.claims, "client-id", now=1000
        )
        self.assertEqual(identity["google_id"], "google-subject-1")
        self.assertEqual(identity["email"], "ada@example.com")

    def test_rejects_expired_or_wrong_audience_claims(self):
        with self.assertRaisesRegex(InvalidGoogleIdentity, "expired"):
            validate_google_claims(self.claims, "client-id", now=1200)

        wrong_audience = {**self.claims, "aud": "another-client"}
        with self.assertRaisesRegex(InvalidGoogleIdentity, "another application"):
            validate_google_claims(wrong_audience, "client-id", now=1000)

    def test_rejects_unverified_email(self):
        unverified = {**self.claims, "email_verified": False}
        with self.assertRaisesRegex(InvalidGoogleIdentity, "not verified"):
            validate_google_claims(unverified, "client-id", now=1000)


class AuthenticationSessionTests(unittest.TestCase):
    def test_google_user_keeps_only_its_database_identity(self):
        state = {"current_user_id": "stale-user", "workflow_result": {}}
        user = {
            "user_id": "real-user-id",
            "name": "Ada Student",
            "email": "ada@example.com",
            "is_demo": False,
        }

        clear_auth_state(state)
        set_active_identity(state, user, "google")

        self.assertEqual(state["current_user_id"], "real-user-id")
        self.assertEqual(state["auth_mode"], "google")
        self.assertNotIn("workflow_result", state)

    def test_judge_mode_accepts_only_the_fixed_demo_identity(self):
        state = {}
        demo_user = {
            "user_id": DEMO_USER_ID,
            "name": "CareerTrace Demo Student",
            "email": None,
            "is_demo": True,
        }

        set_active_identity(state, demo_user, "judge")

        self.assertEqual(state["current_user_id"], DEMO_USER_ID)
        self.assertTrue(state["is_demo"])
        with self.assertRaisesRegex(ValueError, "fixed synthetic demo"):
            set_active_identity(
                {},
                {**demo_user, "user_id": "another-user-id"},
                "judge",
            )

    def test_google_mode_cannot_assume_demo_identity(self):
        with self.assertRaisesRegex(ValueError, "cannot assume"):
            set_active_identity(
                {},
                {
                    "user_id": DEMO_USER_ID,
                    "name": "CareerTrace Demo Student",
                    "email": None,
                    "is_demo": True,
                },
                "google",
            )

    def test_logout_clears_identity_and_user_workspace(self):
        state = {
            "authenticated": True,
            "auth_mode": "judge",
            "current_user_id": DEMO_USER_ID,
            "user_name": "CareerTrace Demo Student",
            "workflow_result": {"profile": {}},
            "edit_major": "Computer Science",
            "unrelated_preference": "kept",
        }

        clear_auth_state(state)

        self.assertNotIn("authenticated", state)
        self.assertNotIn("current_user_id", state)
        self.assertNotIn("workflow_result", state)
        self.assertNotIn("edit_major", state)
        self.assertEqual(state["unrelated_preference"], "kept")


if __name__ == "__main__":
    unittest.main()

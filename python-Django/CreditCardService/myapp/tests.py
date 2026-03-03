from django.test import TestCase, Client

class BasicAPITests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_associate_and_retrieve(self):
        r = self.client.post(
            "/associate_card",
            data='{"credit_card":"4111111111111111","phone":"+123"}',
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 201)

        r = self.client.post(
            "/retrieve_cards",
            data='{"phone_numbers":["+123"]}',
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 200)
        self.assertIn("card_numbers", r.json())
        self.assertIn("4111111111111111", r.json()["card_numbers"])
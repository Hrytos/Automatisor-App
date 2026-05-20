import unittest

from backend.address_validator import (
    ValidationInput,
    address_similarity,
    comparable_domain,
    domains_match,
    name_similarity,
    score_place,
    status_from_best,
    validate_company_site,
)
from backend.main import compact_validation_evidence


class AddressValidatorTests(unittest.TestCase):
    def test_comparable_domain_normalizes_urls_and_subdomains(self):
        self.assertEqual(comparable_domain("https://www.example.com/path"), "example.com")
        self.assertEqual(comparable_domain("app.example.com"), "example.com")

    def test_domains_match_uses_root_domain(self):
        self.assertTrue(domains_match("company.com", "https://www.company.com/about"))
        self.assertFalse(domains_match("company.com", "https://other.com"))

    def test_name_similarity_matches_spaced_and_compact_names(self):
        self.assertEqual(name_similarity("Cook Unity", "CookUnity"), 1.0)
        self.assertGreaterEqual(name_similarity("BR Williams LLC", "B R Williams Trucking Inc"), 0.5)

    def test_address_similarity_rewards_matching_zip(self):
        score = address_similarity(
            "1535 Hillyer Robinson Pkwy, Anniston, AL 36207",
            "1535 Hillyer Robinson Parkway, Anniston, AL 36207, USA",
        )
        self.assertGreaterEqual(score, 0.75)

    def test_address_similarity_penalizes_different_street_number(self):
        score = address_similarity(
            "443 Remington Blvd, Bolingbrook, IL 60440, USA",
            "401 Remington Blvd, Bolingbrook, IL 60440, USA",
        )
        self.assertLess(score, 0.45)

    def test_validated_status_for_strong_match(self):
        site = ValidationInput(
            company_name="Acme Logistics",
            address="100 Main Street, Chicago, IL 60601",
            domain="acme.com",
        )
        scored = score_place(
            site,
            {
                "displayName": {"text": "Acme Logistics"},
                "formattedAddress": "100 Main St, Chicago, IL 60601, USA",
                "websiteUri": "https://www.acme.com",
                "businessStatus": "OPERATIONAL",
            },
        )
        status, _, can_request = status_from_best(scored)
        self.assertEqual(status, "validated")
        self.assertTrue(can_request)

    def test_matching_company_wrong_address_does_not_validate(self):
        site = ValidationInput(
            company_name="Cook Unity",
            address="8730 Bollman Pl, Savage, MD 20763",
            domain="cookunity.com",
        )
        scored = score_place(
            site,
            {
                "displayName": {"text": "CookUnity"},
                "formattedAddress": "630 Flushing Ave, Brooklyn, NY 11206, USA",
                "websiteUri": "http://www.cookunity.com/",
                "businessStatus": "OPERATIONAL",
            },
        )
        status, _, can_request = status_from_best(scored)
        self.assertEqual(status, "needs_correction")
        self.assertFalse(can_request)

    def test_missing_key_returns_unavailable_but_allows_request(self):
        result = validate_company_site("Acme", "100 Main St", "acme.com", api_key="")
        self.assertEqual(result["status"], "unavailable")
        self.assertTrue(result["can_request_assessment"])

    def test_address_only_candidates_are_hidden(self):
        import backend.address_validator as validator

        def fake_search(site, api_key):
            return [
                {
                    "displayName": {"text": "401 Remington Blvd"},
                    "formattedAddress": "401 Remington Blvd, Bolingbrook, IL 60440, USA",
                    "businessStatus": "OPERATIONAL",
                }
            ]

        original = validator.search_google_places
        validator.search_google_places = fake_search
        try:
            result = validate_company_site(
                "443 Remington Blvd",
                "443 Remington Blvd, Bolingbrook, IL 60440, USA",
                "example.com",
                api_key="fake-key",
            )
        finally:
            validator.search_google_places = original

        self.assertEqual(result["status"], "needs_correction")
        self.assertEqual(result["candidates"], [])

    def test_customer_site_validation_evidence_includes_justification(self):
        body = {
            "request_basis": "manual_justification",
            "justification": "This company operates from a partner site.",
            "address_validation": {
                "status": "needs_correction",
                "justification": "Do not store this on account_sites.",
            },
        }
        customer_site_evidence = compact_validation_evidence(body, include_justification=True)
        self.assertEqual(
            customer_site_evidence["justification"],
            "This company operates from a partner site.",
        )


if __name__ == "__main__":
    unittest.main()

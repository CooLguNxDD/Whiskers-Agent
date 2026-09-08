"""Claim/decline matrix for portfolio specialist domain registration.

Load-bearing rule: discover/scoped_ask need an explicit portfolio signal;
bake/redesign are portfolio-owned verbs; pure generic goals decline.
"""

from __future__ import annotations

import pytest

from plugins.portfolio_plugin.plugin_config import portfolio_domain_claims


# (goal, expected_claim)
_CLAIM_MATRIX = [
    # bake / redesign — portfolio-owned classifiers
    ("bake a portfolio page for this SWE role at Acme", True),
    ("redesign my home page with a darker theme", True),
    ("Bake resume site for job application", True),
    # discover / scoped_ask with portfolio signal
    ("discover portfolio context from my GitHub", True),
    ("reindex portfolio projects", True),
    ("compose a scoped_ask layout for my portfolio", True),
    ("show me a GenUI portfolio layout for backend", True),
    ("catportfolio redesign for ML engineer", True),
    # discover / scoped_ask WITHOUT portfolio signal → decline
    ("discover tools for contact search", False),
    ("discover recent commits", False),
    ("layout a dashboard for billing analytics", False),
    ("search for repositories about go", False),
    # empty / unrelated
    ("", False),
    ("   ", False),
    ("hello how are you", False),
    ("list my jules sessions", False),
]


@pytest.mark.parametrize("goal,expected", _CLAIM_MATRIX)
def test_portfolio_domain_claims_matrix(goal: str, expected: bool):
    assert portfolio_domain_claims(goal) is expected

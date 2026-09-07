"""The review gate as a person reaches it.

Tested through the HTTP surface because that is where the guarantees have to
hold: the token, the attribution, the amendment, and — the ones most likely to
be got wrong — what a reviewer is told when the queue is unreachable, when
someone else took the candidate first, and when the write fails after they
clicked approve.
"""

import re

import pytest
from fastapi.testclient import TestClient

from firm_memory import FirmMemory, MemoryScope, MemoryType, Settings
from firm_memory.errors import CandidateStoreError, ProviderError
from firm_memory.providers.inmemory import InMemoryProvider
from firm_memory.review.app import build_app
from firm_memory.review.settings import ReviewSettings

TOKEN = "t" * 32
REVIEWER = "ashish"
RULE = "Cash strategies stop sending at 15:20 because the exchange rejects after that."
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def api():
    memory = FirmMemory(
        InMemoryProvider(),
        settings=Settings(min_score=0.0),
        scope=MemoryScope.for_query("oms", domains=("execution",)),
    )
    yield memory
    memory.close()


@pytest.fixture
def client(api):
    return TestClient(build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN)))


@pytest.fixture
def queued(api):
    """One candidate awaiting review."""
    return api.propose(RULE, type=MemoryType.BUSINESS_RULE, reference="mr-4821").candidate_id


# --- access ------------------------------------------------------------------


def test_the_queue_is_not_readable_without_a_token(client):
    assert client.get("/api/queue").status_code == 401


def test_a_wrong_token_is_refused(client):
    assert client.get("/api/queue", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_a_non_bearer_scheme_is_refused(client):
    assert client.get("/api/queue", headers={"Authorization": f"Basic {TOKEN}"}).status_code == 401


def test_approving_without_a_token_is_refused(client, queued):
    response = client.post(f"/api/candidates/{queued}/approve", json={})
    assert response.status_code == 401


def test_the_page_itself_needs_no_token_because_it_carries_no_data(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Firm Memory review" in response.text


#: The SVG namespace is an identifier, not an address — browsers never fetch it.
SVG_NAMESPACE = "http://www.w3.org/2000/svg"

#: Attributes and constructs that actually cause a browser to fetch something.
FETCHING = (
    re.compile(r"""\bsrc\s*=\s*['"]?\s*(?:https?:)?//""", re.I),
    re.compile(r"""\bhref\s*=\s*['"]?\s*(?:https?:)?//""", re.I),
    re.compile(r"""@import\s+(?:url\()?['"]?\s*(?:https?:)?//""", re.I),
    re.compile(r"""url\(\s*['"]?\s*(?:https?:)?//""", re.I),
    re.compile(r"""\b(?:fetch|importScripts|XMLHttpRequest)\s*\(\s*['"](?:https?:)?//""", re.I),
)


def test_the_page_fetches_nothing_from_the_internet(client):
    """A no-egress deployment must not be undone by the tool that reviews it.

    Checked against the constructs that actually load a resource rather than
    against the string "http", because an XML namespace is a name that happens
    to look like a URL and a blunt check on it would have to be relaxed — which
    is how a guard like this stops guarding anything.
    """
    page = client.get("/").text.replace(SVG_NAMESPACE, "")

    offenders = [pattern.pattern for pattern in FETCHING if pattern.search(page)]
    assert offenders == [], f"the review page would fetch something remote: {offenders}"


def test_the_page_names_no_third_party_host(client):
    """The other half: a host that is present at all is one that could be used."""
    page = client.get("/").text.replace(SVG_NAMESPACE, "")
    for host in ("cdn.", "unpkg", "jsdelivr", "cdnjs", "fonts.googleapis", "fonts.gstatic", "posthog"):
        assert host not in page


def test_health_is_open_and_reveals_nothing(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


# --- reading the queue -------------------------------------------------------


def test_the_queue_shows_the_candidate_and_why_it_is_waiting(client, queued):
    body = client.get("/api/queue", headers=AUTH).json()

    assert body["count"] == 1
    [candidate] = body["candidates"]
    assert candidate["candidate_id"] == queued
    assert candidate["memory"]["content"] == RULE
    assert candidate["reason"]


def test_provenance_is_shown_so_a_reviewer_can_check_the_source(client, queued):
    [candidate] = client.get("/api/queue", headers=AUTH).json()["candidates"]
    assert candidate["memory"]["provenance"]["reference"] == "mr-4821"


def test_the_taxonomy_is_served_so_the_dropdown_cannot_drift_from_it(client):
    types = client.get("/api/taxonomy", headers=AUTH).json()["types"]
    assert {entry["type"] for entry in types} == {member.value for member in MemoryType}


def test_an_unreachable_queue_says_so_rather_than_looking_empty(api, queued):
    """Showing "nothing to review" for a full queue is the worst failure here."""

    def unreachable():
        raise CandidateStoreError("The candidate queue in Postgres is unreachable")

    api.approvals._store.items = unreachable
    app = build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN))
    client = TestClient(app, raise_server_exceptions=False)

    response = client.get("/api/queue", headers=AUTH)
    assert response.status_code == 503
    assert "unreachable" in response.json()["detail"]


# --- approving ---------------------------------------------------------------


def test_approving_makes_the_candidate_firm_knowledge(client, api, queued):
    response = client.post(f"/api/candidates/{queued}/approve", headers=AUTH, json={})

    assert response.status_code == 200
    assert response.json()["status"] == "active"
    assert [hit.content for hit in api.search("when do cash strategies stop sending")] == [RULE]


def test_approval_is_attributed_to_the_person_who_clicked(client, api, queued):
    client.post(f"/api/candidates/{queued}/approve", headers=AUTH, json={})
    [stored] = list(api._provider.export_all())
    assert stored.provenance.author == "ashish"


def test_the_body_cannot_claim_to_be_someone_else(client, queued):
    """Identity comes from the token. A name in the body is refused outright."""
    response = client.post(
        f"/api/candidates/{queued}/approve", headers=AUTH, json={"approver": "someone-else"}
    )
    assert response.status_code == 422


def test_an_approval_needs_no_name_because_the_token_is_the_name(client, queued):
    assert client.post(f"/api/candidates/{queued}/approve", headers=AUTH, json={}).status_code == 200


def test_a_second_reviewers_token_attributes_to_that_second_reviewer(api, queued):
    """One token each, so an approval names the person who actually made it."""
    settings = ReviewSettings(reviewers={TOKEN: REVIEWER, "p" * 32: "priya"})
    client = TestClient(build_app(api, settings=settings))

    client.post(
        f"/api/candidates/{queued}/approve",
        headers={"Authorization": "Bearer " + "p" * 32},
        json={},
    )

    [stored] = list(api._provider.export_all())
    assert stored.provenance.author == "priya"


def test_whoami_tells_the_page_which_name_its_decisions_will_carry(client):
    assert client.get("/api/whoami", headers=AUTH).json() == {"reviewer": REVIEWER}


def test_whoami_needs_a_token_like_everything_else(client):
    assert client.get("/api/whoami").status_code == 401


def test_the_second_reviewer_to_act_is_told_to_reload_not_shown_a_crash(client, queued):
    """Approval is a race exactly one reviewer wins."""
    client.post(f"/api/candidates/{queued}/approve", headers=AUTH, json={})

    second = client.post(f"/api/candidates/{queued}/approve", headers=AUTH, json={})
    assert second.status_code == 409


def test_a_failed_write_reports_a_retry_and_puts_the_candidate_back(api):
    """The candidate must not vanish from both the queue and the pool."""

    class FailingProvider(InMemoryProvider):
        def insert(self, memory):
            raise ProviderError("pgvector is down")

    failing = FirmMemory(FailingProvider(), settings=Settings(min_score=0.0), scope=MemoryScope.for_repo("oms"))
    candidate_id = failing.propose(RULE, type=MemoryType.BUSINESS_RULE).candidate_id
    client = TestClient(
        build_app(failing, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN)), raise_server_exceptions=False
    )

    response = client.post(f"/api/candidates/{candidate_id}/approve", headers=AUTH, json={})

    assert response.status_code == 502
    assert client.get("/api/queue", headers=AUTH).json()["count"] == 1
    failing.close()


# --- amending ----------------------------------------------------------------


def test_a_reviewer_can_fix_the_wording_before_approving(client, api, queued):
    response = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"content": "Cash strategies stop sending at 15:20."}},
    )

    assert response.json()["amended"] is True
    [stored] = list(api._provider.export_all())
    assert stored.content == "Cash strategies stop sending at 15:20."


def test_a_reviewer_can_correct_the_type(client, api, queued):
    client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"type": "architecture_decisions"}},
    )
    [stored] = list(api._provider.export_all())
    assert stored.type is MemoryType.ARCHITECTURE_DECISION


def test_a_reviewer_can_widen_the_scope_across_repos(client, api, queued):
    client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"scope": {"firm": False, "domains": ["execution"], "repos": ["oms", "gateway"]}}},
    )
    [stored] = list(api._provider.export_all())
    assert stored.scope.repos == ("oms", "gateway")


def test_an_amendment_records_who_made_it(client, api, queued):
    client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"content": "A clearer statement of the rule."}},
    )
    [stored] = list(api._provider.export_all())
    assert stored.metadata["amended_by"] == "ashish"


def test_an_amendment_to_a_type_outside_the_taxonomy_is_refused(client, queued):
    response = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"type": "user_preferences"}},
    )
    assert response.status_code == 400
    assert "Unknown memory type" in response.json()["detail"]


def test_a_refused_amendment_leaves_the_candidate_in_the_queue(client, queued):
    client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"type": "user_preferences"}},
    )
    assert client.get("/api/queue", headers=AUTH).json()["count"] == 1


def test_an_empty_amendment_is_not_reported_as_an_edit(client, queued):
    response = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"content": "   "}},
    )
    assert response.json()["amended"] is False


def test_the_write_path_axis_cannot_be_changed_from_the_form(client, queued):
    """tier and task are not review opinions; the schema refuses them outright."""
    response = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"tier": "episodic", "task": "mr-1"}},
    )
    assert response.status_code == 422


def test_content_long_enough_to_be_a_diff_is_refused(client, queued):
    """The box must not become a way to paste source into the pool."""
    response = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"content": "x" * 5000}},
    )
    assert response.status_code == 422


# --- rejecting ---------------------------------------------------------------


def test_rejecting_clears_the_candidate_without_storing_anything(client, api, queued):
    response = client.post(
        f"/api/candidates/{queued}/reject",
        headers=AUTH,
        json={"reason": "already in the runbook"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert client.get("/api/queue", headers=AUTH).json()["count"] == 0
    assert list(api._provider.export_all()) == []


def test_a_rejection_reason_is_recorded(client, queued):
    body = client.post(
        f"/api/candidates/{queued}/reject",
        headers=AUTH,
        json={"reason": "already in the runbook"},
    ).json()
    assert body["memory"]["metadata"]["rejection_reason"] == "already in the runbook"


def test_a_rejection_is_attributed_to_the_token_that_made_it(client, queued):
    response = client.post(
        f"/api/candidates/{queued}/reject", headers=AUTH, json={"reason": "CodeGraph answers this"}
    )
    assert response.json()["memory"]["metadata"]["rejected_by"] == REVIEWER


def test_a_rejection_body_cannot_claim_to_be_someone_else(client, queued):
    response = client.post(
        f"/api/candidates/{queued}/reject", headers=AUTH, json={"reviewer": "someone-else"}
    )
    assert response.status_code == 422


def test_rejecting_a_candidate_someone_else_already_took_says_reload(client, queued):
    client.post(f"/api/candidates/{queued}/reject", headers=AUTH, json={})
    second = client.post(f"/api/candidates/{queued}/reject", headers=AUTH, json={})
    assert second.status_code == 409


# --- throttling --------------------------------------------------------------


def test_a_client_hammering_the_api_is_slowed_down(api):
    client = TestClient(build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN, rate_limit_per_minute=3)))

    codes = [client.get("/api/queue", headers=AUTH).status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert codes[-1] == 429


def test_throttling_does_not_apply_to_the_page_itself(api):
    client = TestClient(build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN, rate_limit_per_minute=1)))
    client.get("/api/queue", headers=AUTH)

    assert client.get("/").status_code == 200


# --- what a failure is allowed to say ----------------------------------------


def test_a_server_side_failure_does_not_echo_the_driver_message(api, queued):
    """Driver output names hosts, ports and accounts. It belongs in the log."""

    def unreachable():
        raise CandidateStoreError(
            "The candidate queue in Postgres is unreachable: connection to server at "
            '"db.internal.example" (10.0.4.12), port 5432 failed: password authentication '
            'failed for user "mem0"'
        )

    api.approvals._store.items = unreachable
    app = build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN))
    client = TestClient(app, raise_server_exceptions=False)

    detail = client.get("/api/queue", headers=AUTH).json()["detail"]

    assert "db.internal.example" not in detail
    assert "10.0.4.12" not in detail
    assert "password" not in detail


def test_a_server_side_failure_still_says_what_to_do_about_it(api, queued):
    def unreachable():
        raise CandidateStoreError("connection to server at \"db.internal\" failed")

    api.approvals._store.items = unreachable
    app = build_app(api, settings=ReviewSettings.for_reviewer(REVIEWER, TOKEN))
    client = TestClient(app, raise_server_exceptions=False)

    body = client.get("/api/queue", headers=AUTH).json()
    assert "nothing has been lost" in body["detail"]
    assert body["error_type"] == "CandidateStoreError"


def test_a_failure_about_the_reviewers_own_input_is_still_spelled_out(client, queued):
    """A 400 exists to be read; hiding it would leave the reviewer guessing."""
    detail = client.post(
        f"/api/candidates/{queued}/approve",
        headers=AUTH,
        json={"amendment": {"type": "user_preferences"}},
    ).json()["detail"]

    assert "user_preferences" in detail
    assert "business_rules" in detail, "the allowed types are the useful half of the message"

"""Anthropic credentials that mint a GitHub OIDC assertion per token exchange.

CI has no API key. Workload Identity Federation exchanges a GitHub Actions OIDC
JWT for a short-lived Anthropic access token, and two properties of that
assertion decide how it has to be supplied:

* It is SINGLE USE. Anthropic burns the JWT's `jti` on the first exchange and
  rejects a replay with `jti_reused` — visible in Console > Workload identity >
  Authentication events, but surfaced to the SDK only as a bare 401 whose
  message blames the federation rule.
* The minted access token lives at most twice the *remaining* life of the JWT,
  and a GitHub JWT expires ~5 minutes after issuance, so the ceiling is ~10
  minutes however `token_lifetime_seconds` is set on the rule. A generator run
  takes up to 25 minutes, so the SDK re-exchanges mid-run — every run.

The workflow used to write one JWT to `$ANTHROPIC_IDENTITY_TOKEN_FILE` and keep
it warm from a detached `while true; do sleep 120; fetch; done` loop. The SDK
re-reads that file on every exchange (the mechanism exists for Kubernetes
projected tokens, which the kubelet rotates in place), so the shape looks right
— but the file is the *only* thing a mid-run exchange can see, and whether it
holds a fresh, unburned JWT at that instant depends on a background process
nothing observes. Two exchanges inside one 120-second window replay a `jti`;
a fetch that quietly stops leaves behind a JWT that expires five minutes later
and a 401 that names the wrong cause. The loop narrows the window, it does not
close it — and a loop detached from an early step keeps re-using the
`ACTIONS_ID_TOKEN_REQUEST_TOKEN` that step handed it, which is the runner's to
rotate, so its own credential can go stale hours before the run needs a JWT.

Minting inside the exchange closes it. The SDK accepts any callable as its
identity-token provider and invokes it once per exchange, so the assertion is
minted at the only moment that matters: seconds old, never exchanged, however
often and whenever the SDK asks. No file, no refresher, no window.

Run `python -m src.anthropic_credentials` to mint one token and print its
non-secret claims — a few-second preflight for the credentials a 25-minute run
depends on.
"""

import os
import time
from typing import Optional

import requests

# The federation rule matches assertions on this audience; a JWT minted for
# any other audience is rejected at exchange time.
_AUDIENCE = "https://api.anthropic.com"

# GitHub injects this pair into every step of a job that grants
# `id-token: write`. A step-level `env:` map adds to them rather than replacing
# them, so the generator process inherits both and can mint for itself.
_REQUEST_URL_ENV = "ACTIONS_ID_TOKEN_REQUEST_URL"
_REQUEST_TOKEN_ENV = "ACTIONS_ID_TOKEN_REQUEST_TOKEN"

_MINT_ATTEMPTS = 3
_MINT_BASE_DELAY = 1  # seconds, doubled per attempt
_MINT_TIMEOUT = 10  # seconds


def actions_oidc_available() -> bool:
    """Whether this process can mint a GitHub OIDC token at all."""
    return bool(os.getenv(_REQUEST_URL_ENV) and os.getenv(_REQUEST_TOKEN_ENV))


def _looks_like_jwt(value: str) -> bool:
    """Three non-empty dot-separated segments."""
    parts = value.split(".")
    return len(parts) == 3 and all(parts)


def github_actions_identity_token() -> str:
    """Mint a fresh GitHub OIDC JWT for the Anthropic token exchange.

    The SDK calls this once per exchange, so a raise here surfaces as the
    failure of the API call that needed a token — which is the right blast
    radius: the run's own retry policy sees it.

    Validating the response is load-bearing. The shell version this replaces
    piped curl into `jq -r .value` without `--fail`/`pipefail`, so an HTTP
    error wrote the literal string "null" and the run died minutes later,
    inside the SDK, on a 401 that blamed the federation rule. The SDK checks
    nothing beyond a size cap; whatever this returns is what gets exchanged.
    """
    url = os.getenv(_REQUEST_URL_ENV)
    request_token = os.getenv(_REQUEST_TOKEN_ENV)
    if not url or not request_token:
        raise RuntimeError(
            f"No GitHub OIDC endpoint in the environment ({_REQUEST_URL_ENV} / "
            f"{_REQUEST_TOKEN_ENV}). Does the job grant `id-token: write`?"
        )

    last_error: Optional[Exception] = None
    for attempt in range(_MINT_ATTEMPTS):
        try:
            response = requests.get(
                url,
                params={"audience": _AUDIENCE},
                headers={"Authorization": f"Bearer {request_token}"},
                timeout=_MINT_TIMEOUT,
            )
            response.raise_for_status()
            value = (response.json().get("value") or "").strip()
        except (requests.RequestException, ValueError) as e:
            last_error = e
        else:
            if _looks_like_jwt(value):
                return value
            last_error = RuntimeError(
                f"OIDC endpoint returned no usable JWT (value={value[:24]!r}...)"
            )
        if attempt < _MINT_ATTEMPTS - 1:
            time.sleep(_MINT_BASE_DELAY * (2 ** attempt))

    raise RuntimeError(
        f"Could not mint a GitHub OIDC token after {_MINT_ATTEMPTS} attempts: {last_error}"
    )


def federated_credentials():
    """Credentials that re-mint per exchange, or None when this is not CI.

    Returning None leaves the SDK's own credential chain untouched, which is
    what a local run wants: it resolves ANTHROPIC_API_KEY or an `ant auth
    login` profile exactly as before.
    """
    rule_id = os.getenv("ANTHROPIC_FEDERATION_RULE_ID")
    organization_id = os.getenv("ANTHROPIC_ORGANIZATION_ID")
    if not (rule_id and organization_id and actions_oidc_available()):
        return None

    import anthropic  # local: keeps `config` importable without the SDK loaded

    return anthropic.WorkloadIdentityCredentials(
        identity_token_provider=github_actions_identity_token,
        federation_rule_id=rule_id,
        organization_id=organization_id,
        service_account_id=os.getenv("ANTHROPIC_SERVICE_ACCOUNT_ID") or None,
        # Required when the rule is scoped to more than one workspace: without
        # it the exchange 401s. Harmless when the rule resolves to a single
        # workspace, and None when the var is unset.
        workspace_id=os.getenv("ANTHROPIC_WORKSPACE_ID") or None,
    )


def _print_claims(token: str) -> None:
    """Echo the assertion's non-secret claims — never the token itself.

    These are the values the federation rule is matched against, so a real
    mismatch is diagnosable from the run log.
    """
    import base64
    import json

    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)  # base64url from a JWT carries no padding
    claims = json.loads(base64.urlsafe_b64decode(payload))
    print(json.dumps({k: claims.get(k) for k in
                      ("sub", "aud", "repository", "event_name", "ref", "exp")}))


if __name__ == "__main__":
    minted = github_actions_identity_token()
    print(f"Minted a GitHub OIDC JWT ({len(minted)} chars) for {_AUDIENCE}")
    try:
        _print_claims(minted)
    except Exception as exc:  # diagnostics only — a decode quirk must not fail the run
        print(f"(could not decode claims: {exc})")

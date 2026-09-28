"""Minimal reproduction: `prompt_summary` never reaches the L2 credential.

The spec lists `prompt_summary` as a selectively disclosable L2 claim in
Autonomous mode (spec/README.md, "Layer 2" claim table) and names it among
what to disclose to a merchant (spec/README.md, "Merchant presentation").
The reference SDK accepts it on `UserMandate` and then drops it: it appears
neither in the L2 payload nor among the disclosures.

Nothing from this project is used here — only the reference SDK and its own
example helpers — so the result is about the SDK, not about Concord.

    VI_SDK_PATH=/path/to/verifiable-intent python3 repro_prompt_summary_dropped.py

Exits 0 if the field survives, 1 if it is dropped.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

VI = Path(os.environ.get("VI_SDK_PATH", Path.home() / "Documents/verifiable-intent"))
if not (VI / "src" / "verifiable_intent").is_dir():
    raise SystemExit(
        f"Reference SDK not found at {VI}.\n"
        "  git clone https://github.com/agent-intent/verifiable-intent.git\n"
        "then set VI_SDK_PATH to the checkout."
    )
for p in (VI / "src", VI / "examples"):
    sys.path.insert(0, str(p))

from helpers import get_agent_keys, get_issuer_keys, get_user_keys  # noqa: E402
from verifiable_intent.crypto.disclosure import hash_bytes  # noqa: E402
from verifiable_intent.crypto.sd_jwt import decode_sd_jwt, resolve_disclosures  # noqa: E402
from verifiable_intent.issuance.issuer import create_layer1  # noqa: E402
from verifiable_intent.issuance.user import create_layer2_autonomous  # noqa: E402
from verifiable_intent.models.issuer_credential import IssuerCredential  # noqa: E402
from verifiable_intent.models.user_mandate import (  # noqa: E402
    CheckoutMandate, MandateMode, PaymentMandate, UserMandate,
)

PROMPT = "Buy a cheap beginner racket, Babolat, returnable"

now = int(time.time())
issuer, user, agent = get_issuer_keys(), get_user_keys(), get_agent_keys()

l1 = create_layer1(IssuerCredential(
    iss="https://www.mastercard.com", sub="user-001", iat=now, exp=now + 86400,
    aud="https://wallet.example.com", cnf_jwk=user.public_jwk,
    email="user@example.com", pan_last_four="1234", scheme="Mastercard",
), issuer.private_key)

mandate = UserMandate(
    nonce=str(uuid.uuid4()), aud="https://agent.example", iat=now,
    iss="https://wallet.example.com", exp=now + 86400,
    mode=MandateMode.AUTONOMOUS,
    sd_hash=hash_bytes(l1.serialize().encode("ascii")),
    prompt_summary=PROMPT,
    checkout_mandate=CheckoutMandate(
        cnf_jwk=agent.public_jwk, cnf_kid="agent-key-1", constraints=[],
    ),
    payment_mandate=PaymentMandate(
        cnf_jwk=agent.public_jwk, cnf_kid="agent-key-1",
        payment_instrument={"type": "demo", "id": "demo", "description": "demo"},
        risk_data={}, constraints=[],
    ),
)

print(f"in:  UserMandate.prompt_summary = {mandate.prompt_summary!r}")

l2 = create_layer2_autonomous(mandate, user.private_key)
serialized = l2.serialize()
parsed = decode_sd_jwt(serialized)
claims = resolve_disclosures(parsed)

print(f"out: L2 payload claims      = {sorted(parsed.payload)}")
print(f"out: after all disclosures  = {sorted(claims)}")
print(f"out: prompt text present in serialized L2 = "
      f"{PROMPT in serialized or PROMPT in json.dumps(claims, ensure_ascii=False)}")

survived = "prompt_summary" in parsed.payload or "prompt_summary" in claims
print()
print("RESULT:", "field survived" if survived
      else "field DROPPED — absent from the L2 payload and from every disclosure")
raise SystemExit(0 if survived else 1)

"""Строит настоящую цепочку Verifiable Intent для сценария и проверяет её
подлинной библиотекой из репозитория Mastercard.

Здесь ничего не имитируется: подписи, выборочное раскрытие, привязка слоёв
и проверка ограничений — всё вызывается из SDK как есть.
"""

from __future__ import annotations

import hashlib
import sys
import time
import uuid
from pathlib import Path

VI = Path("/home/claude/vi")
for p in (str(VI / "src"), str(VI / "examples")):
    if p not in sys.path:
        sys.path.insert(0, p)

from helpers import get_agent_keys, get_issuer_keys, get_merchant_keys, get_user_keys  # noqa: E402
from verifiable_intent.crypto.disclosure import (  # noqa: E402
    _b64url_encode, build_selective_presentation, hash_bytes, hash_disclosure,
)
from verifiable_intent.crypto.sd_jwt import decode_sd_jwt, resolve_disclosures  # noqa: E402
from verifiable_intent.crypto.signing import _jwt_encode  # noqa: E402
from verifiable_intent.issuance.agent import create_layer3_checkout, create_layer3_payment  # noqa: E402
from verifiable_intent.issuance.issuer import create_layer1  # noqa: E402
from verifiable_intent.issuance.user import create_layer2_autonomous  # noqa: E402
from verifiable_intent.models.agent_mandate import (  # noqa: E402
    CheckoutL3Mandate, FinalCheckoutMandate, FinalPaymentMandate, PaymentL3Mandate,
)
from verifiable_intent.models.constraints import (  # noqa: E402
    AllowedMerchantConstraint, AllowedPayeeConstraint,
    CheckoutLineItemsConstraint, PaymentAmountConstraint,
)
from verifiable_intent.models.issuer_credential import IssuerCredential  # noqa: E402
from verifiable_intent.models.user_mandate import (  # noqa: E402
    CheckoutMandate, MandateMode, PaymentMandate, UserMandate,
)
from verifiable_intent.verification.chain import verify_chain  # noqa: E402
from verifiable_intent.verification.constraint_checker import check_constraints  # noqa: E402

from catalog import MERCHANTS, find  # noqa: E402

PAYMENT_INSTRUMENT = {
    "type": "mastercard.srcDigitalCard",
    "id": "f199c3dd-7106-478b-9b5f-7af9ca725170",
    "description": "Mastercard **** 1234",
}


def _checkout_jwt(purchase, merchant_keys):
    now = int(time.time())
    items, total = [], 0
    for sku, qty in purchase:
        p = find(sku)
        total += p["price_cents"] * qty
        items.append({"sku": p["sku"], "name": p["name"], "quantity": qty,
                      "unitPrice": p["price_cents"] / 100})
    payload = {
        "iss": MERCHANTS[0]["website"], "sub": "cart_checkout",
        "iat": now, "exp": now + 3600,
        "cart": {"items": items, "subTotal": {"amount": total / 100, "currencyCode": "USD"}},
    }
    jwt = _jwt_encode({"alg": "ES256", "typ": "JWT", "kid": merchant_keys.kid},
                      payload, merchant_keys.private_key)
    return jwt, total


def _find_disc(sd_jwt, predicate):
    for s, v in zip(sd_jwt.disclosures, sd_jwt.disclosure_values):
        value = v[-1] if v else None
        if predicate(value):
            return s
    return None


def run_vi(scn) -> dict:
    """Возвращает: подлинность цепочки, соблюдение ограничений, нарушения."""
    now = int(time.time())
    issuer, user, agent, merchant = (
        get_issuer_keys(), get_user_keys(), get_agent_keys(), get_merchant_keys()
    )

    # --- L1 -----------------------------------------------------------
    l1 = create_layer1(IssuerCredential(
        iss="https://www.mastercard.com", sub="user-001", iat=now, exp=now + 86400,
        aud="https://wallet.example.com", cnf_jwk=user.public_jwk,
        email="user@example.com", pan_last_four="1234", scheme="Mastercard",
    ), issuer.private_key)

    acceptable = [{"id": s, "title": find(s)["name"]} for s in scn.allowlist]

    # --- L2 -----------------------------------------------------------
    mandate = UserMandate(
        nonce=str(uuid.uuid4()), aud="https://agent.example", iat=now,
        iss="https://wallet.example.com", exp=now + 86400,
        mode=MandateMode.AUTONOMOUS,
        sd_hash=hash_bytes(l1.serialize().encode("ascii")),
        prompt_summary=scn.prompt_summary,
        checkout_mandate=CheckoutMandate(
            cnf_jwk=agent.public_jwk, cnf_kid="agent-key-1",
            constraints=[
                AllowedMerchantConstraint(allowed=MERCHANTS),
                CheckoutLineItemsConstraint(items=[{
                    "id": "line-1", "acceptable_items": acceptable,
                    "quantity": scn.quantity_cap,
                }]),
            ],
        ),
        payment_mandate=PaymentMandate(
            cnf_jwk=agent.public_jwk, cnf_kid="agent-key-1",
            payment_instrument=PAYMENT_INSTRUMENT,
            risk_data={"device_id": "dev-1", "ip_address": "10.0.0.1"},
            constraints=[
                PaymentAmountConstraint(currency="USD", min=scn.amount_min_cents,
                                        max=scn.amount_max_cents),
                AllowedPayeeConstraint(allowed=MERCHANTS),
            ],
        ),
        merchants=MERCHANTS, acceptable_items=acceptable,
    )
    l2 = create_layer2_autonomous(mandate, user.private_key)
    l2_ser = l2.serialize()
    l2_base = l2_ser.split("~")[0]

    # --- агент покупает ------------------------------------------------
    jwt, total = _checkout_jwt(scn.purchase, merchant)
    c_hash = _b64url_encode(hashlib.sha256(jwt.encode("utf-8")).digest())
    nonce = str(uuid.uuid4())

    pay_disc = _find_disc(l2, lambda v: isinstance(v, dict) and v.get("vct") == "mandate.payment.open.1")
    chk_disc = _find_disc(l2, lambda v: isinstance(v, dict) and v.get("vct") == "mandate.checkout.open.1")
    mer_disc = _find_disc(l2, lambda v: isinstance(v, dict) and v.get("name") == MERCHANTS[0]["name"])
    itm_disc = _find_disc(l2, lambda v: isinstance(v, dict) and v.get("id") == scn.allowlist[0])

    l3a = create_layer3_payment(PaymentL3Mandate(
        nonce=nonce, aud="https://www.mastercard.com", iat=now,
        iss="https://agent.example", exp=now + 300,
        final_payment=FinalPaymentMandate(
            transaction_id=c_hash, payee=MERCHANTS[0],
            payment_amount={"currency": "USD", "amount": total},
            payment_instrument=PAYMENT_INSTRUMENT,
        ),
        final_merchant=MERCHANTS[0],
    ), agent.private_key, l2_base, pay_disc, mer_disc)

    create_layer3_checkout(CheckoutL3Mandate(
        nonce=nonce, aud=MERCHANTS[0]["website"], iat=now,
        iss="https://agent.example", exp=now + 300,
        final_checkout=FinalCheckoutMandate(checkout_jwt=jwt, checkout_hash=c_hash),
    ), agent.private_key, l2_base, chk_disc, itm_disc)

    # --- проверка цепочки ---------------------------------------------
    l1_parsed, l2_parsed = decode_sd_jwt(l1.serialize()), decode_sd_jwt(l2_ser)
    l2_payment_ser = build_selective_presentation(l2_base, [pay_disc, mer_disc])
    chain = verify_chain(l1_parsed, l2_parsed, l3_payment=l3a,
                         issuer_public_key=issuer.public_key,
                         l1_serialized=l1.serialize(), l2_serialized=l2_ser,
                         l2_payment_serialized=l2_payment_ser)

    violations: list[str] = []
    satisfied = False
    if chain.valid:
        l2_claims = resolve_disclosures(l2_parsed)
        disc_by_hash = {hash_disclosure(s): v for s, v in
                        zip(l2_parsed.disclosures, l2_parsed.disclosure_values)}

        pay_constraints, chk_constraints = [], []
        for d in l2_claims.get("delegate_payload", []):
            if not isinstance(d, dict):
                continue
            if d.get("vct") == "mandate.payment.open.1":
                pay_constraints = d.get("constraints", [])
            elif d.get("vct") == "mandate.checkout.open.1":
                chk_constraints = d.get("constraints", [])

        fulfillment = {}
        for d in chain.l3_payment_claims.get("delegate_payload", []):
            if isinstance(d, dict) and d.get("vct") == "mandate.payment.1":
                fulfillment = dict(d)
        for c in pay_constraints:
            if c.get("type") == "mandate.payment.allowed_payees":
                fulfillment["allowed_merchants"] = [
                    disc_by_hash[r["..."]][-1] for r in c.get("allowed", [])
                    if isinstance(r, dict) and r.get("...") in disc_by_hash
                ]

        pay_res = check_constraints(pay_constraints, fulfillment)

        # сторона магазина: те же ограничения, корзина как line_items
        chk_inline = []
        for c in chk_constraints:
            c = dict(c)
            if c.get("type") == "mandate.checkout.line_items":
                c["items"] = [{"id": "line-1", "acceptable_items": acceptable,
                               "quantity": scn.quantity_cap}]
            chk_inline.append(c)
        chk_res = check_constraints(chk_inline, {
            "line_items": [{"id": sku, "quantity": q} for sku, q in scn.purchase],
            "merchant": MERCHANTS[0],
            "allowed_merchants": MERCHANTS,
        })

        satisfied = pay_res.satisfied and chk_res.satisfied
        violations = list(pay_res.violations) + list(chk_res.violations)

    return {
        "chain_valid": chain.valid,
        "chain_errors": list(chain.errors),
        "constraints_ok": satisfied,
        "violations": violations,
        "total_cents": total,
    }

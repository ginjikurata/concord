# intent-gap

Verifiable Intent proves an agent stayed inside its formal limits. It does not
check whether the agent bought what the person actually asked for.

This repository demonstrates that gap with working code, and prototypes a check
that closes it.

---

## The gap

[Verifiable Intent](https://github.com/agent-intent/verifiable-intent) (draft
v0.1) cryptographically binds a purchase to a delegation chain: the item is on
an approved list, the amount is inside a band, the merchant is allowed. All of
that is real and it verifies.

What the person actually said is carried in a single field, `prompt_summary`.
In the spec it is:

- **OPTIONAL** (`spec/credential-format.md`)
- annotated *"for audit trail"* (`spec/README.md`)
- **never read by any verification code** — `grep prompt_summary` across
  `src/verifiable_intent/verification/` returns nothing

So the person's own words are present in the mandate, and nothing compares them
to the constraints or to the final cart.

This is not only a `prompt_summary` problem. The constraint types the standard
defines are:

| Constraint | Expresses |
|---|---|
| `AllowedMerchantConstraint` / `AllowedPayeeConstraint` | who may be paid |
| `CheckoutLineItemsConstraint` | which SKUs, how many |
| `PaymentAmountConstraint` / `PaymentBudgetConstraint` | how much |
| `PaymentRecurrenceConstraint` / `AgentRecurrenceConstraint` | how often |
| `ReferenceConstraint` | linkage |

There is no way to express *"in black"*, *"size 42"*, *"returnable if it
doesn't fit"*, or *"here by Saturday"* — so a purchase can satisfy every
constraint in the standard and still be the wrong purchase.

### Why this matters for liability

Issue [#15](https://github.com/agent-intent/verifiable-intent/issues/15) asks
who absorbs the loss when an agent buys the wrong thing. Answering it, a
participant in the discussion wrote:

> The merchant is liable as they have verified L3 cart_jwt against the L2's
> user intent

— [@fahads9](https://github.com/fahads9), issue #15

That commenter is a discussion participant, not a project maintainer, and the
position is not an official one. It is worth taking seriously anyway, because
it is the intuitive reading: someone must reconcile the cart against intent,
and the merchant is the obvious candidate. The problem is that the standard
gives merchants no mechanism to do it. The duty is assigned; the instrument
does not exist.

---

## Reproduce it

```bash
git clone https://github.com/agent-intent/verifiable-intent.git   # next to this repo
pip3 install --user cryptography
python3 run.py
```

`run.py` builds a genuine VI chain for each scenario — real ES256 signatures,
real selective disclosure, verified by the Mastercard SDK, nothing mocked — and
then runs the intent check against the same purchase.

The VI SDK is located via `VI_SDK_PATH`, then `../verifiable-intent`, then
`./verifiable-intent`.

`run.py` also needs a Gemini API key in `.env` (see `.env.example`) because
intent extraction calls a model. `build_conformance_vectors.py` does **not** —
it exercises only the cryptographic path.

---

## What the scenarios show

Eight purchases. In each one the agent stays strictly inside its mandate.

| Scenario | Person asked for | Agent bought | VI | Intent |
|---|---|---|---|---|
| `ok-baseline` | cheap beginner Babolat | Babolat Drive Junior | passes | matches |
| `price-top` | *"cheap, for a beginner"* | top-of-band pro racket | passes | **diverges** |
| `brand-swap` | *"Babolat, I'm used to them"* | HEAD | passes | **diverges** |
| `variant` | black ASICS, size 42 | white ASICS, size 44 | passes | **diverges** |
| `quantity` | *"a couple of cans"* | six | passes | **diverges** |
| `refund` | *"so I can return it"* | non-returnable | passes | **diverges** |
| `deadline` | *"by Saturday, tournament"* | ships in 21 days | passes | **diverges** |
| `vi-catches` | beginner racket under 15 000 | out-of-allowlist pro racket | **rejects** | diverges |

**Six of eight purchases satisfy Verifiable Intent completely while
contradicting what the person asked for.** The last row is the control: given a
real violation, the standard catches it on its own and this project adds
nothing.

In `variant` the price matches to the cent. In `refund` and `deadline` the
person's condition cannot be written as a constraint at all.

---

## How the check is built

```
catalog.py                    products with attributes VI constraints cannot express
scenarios.py                  8 scenarios: request -> constraints -> purchase
vi_runner.py                  builds a real VI chain, verifies it with the Mastercard SDK
coherence.py                  extraction + deterministic checking + pre-purchase decision
llm_extract.py                the language model call — the only AI in the project
stress_phrases.py             37 real-speech phrases for testing extraction
run_stress_test.py            runs them, auto-checking where an answer is unambiguous
run.py                        8 scenarios, after the fact
run_prepurchase.py            same scenarios, decided before checkout
build_conformance_vectors.py  generates conformance_vectors.json
```

### Extraction and checking are deliberately separate

1. **Extraction** turns a person's words into facts. This needs a language
   model, and it is confined to `llm_extract.py`.
2. **Checking** compares those facts to the cart. This is ordinary
   deterministic code with no model in it.

The split is the point, not decoration. *"The AI decided so"* is worth nothing
in a dispute. *"The person asked to be able to return it; the item is
non-returnable"* is worth something, because a human can verify it by hand. The
same inputs always produce the same findings — 20 identical calls give one
identical result.

### Soft preferences

Stress-testing exposed a second gap, this one in our own schema rather than the
standard's. People hedge:

> черные кроссовки, а вообще если только белые есть — ну ладно, разница не
> принципиальная
> *(black shoes, though if only white are available — fine, not a big deal)*

A schema of hard facts records `color=black` and the checker blocks a perfectly
acceptable purchase. So the model additionally returns `soft_fields`: which
extracted facts the person themselves marked as non-binding. A mismatch on a
soft field produces a warning instead of a block, and does not stop the agent.
Softness on one field does not weaken the others.

### Before the purchase, not after

`decide_before_purchase()` runs the same check while the agent is still
deciding. Blocking findings stop it and produce a question for the person;
warnings do not. This is the earlier and more useful entry point — the buyer
owns the agent, and being asked beforehand beats discovering the problem in a
chargeback.

---

## Conformance vectors

The standard ships no cross-implementation test vectors, so a second
implementation has no way to show it agrees with the reference one.

`build_conformance_vectors.py` generates them: all 8 scenarios, each with a
genuinely signed chain (L1, L2, the L2 payment presentation, L3a, L3b), the
public JWKs, and the expected verdict — chain validity, constraint
satisfaction, and the exact violations.

The output is **not** committed. L3 mandates expire five minutes after
issuance, so a frozen fixture would test "can you reject something stale"
within minutes of being written. Run the generator and validate the result
immediately.

The signatures are real: they verify with a plain ECDSA implementation and no
Mastercard SDK involved, which is exactly what a second implementation would
do.

---

## Limitations

Read the numbers with these in mind:

- **The eight scenarios were written to expose this gap.** "Six of eight" is
  not a measured failure rate in the wild; it shows the failure mode exists and
  is easy to construct, not how often it occurs.
- **The catalog is a toy** — 7 products, 2 merchants.
- **One model, one language.** Extraction was tested with Gemini on Russian
  phrasing: 37 conversational phrases, 0 extraction failures, all checkable
  fields matching. Other models and languages are untested.
- **Extraction is not bit-reproducible.** Even at `temperature=0` the model
  occasionally differs on borderline phrasing. The deterministic half is
  unaffected, and it is the half that belongs in a dispute.
- **Soft-field detection depends on the model** noticing that a person hedged.
- **This is a prototype**, not a library — no packaging, no API, no tests
  beyond the runnable demonstrations above.

---

## Relationship to the standard

This repository works *on top of* Verifiable Intent and does not modify it. The
spec is Apache 2.0 and is expected as a sibling checkout, not vendored here.

Nothing here has been raised with the standard's authors yet. Of the open
discussions in that repository, none currently addresses the mismatch between
stated intent and encoded constraints.

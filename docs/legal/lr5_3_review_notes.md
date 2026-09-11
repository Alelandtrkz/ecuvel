# LR5.3 Review Notes

## 1. Baseline and scope

- Baseline audit: `master` at `18ab5c1c6c354e65597c0ea2ef3b0992e0da26a9`, equal to `origin/master`, with subject `docs(legal): draft seller policies` and a clean worktree before LR5.3.
- LR5.3 adds schema and services for immutable published-version evidence and per-user evidence. It does not publish a document, seed a legal version, backfill a user, or integrate Register, Auth, Checkout, Profile, payments, payouts, or Seller flows.
- LR5.2A-D legal prose remains closed and unchanged.

## 2. Existing architecture audited

- Docs registry: `backend/app/services/legal_documents.py` remains the source for logical identity, navigation, template location, and DRAFT/public presentation metadata. No equivalent persistent legal-version model existed.
- User: `backend/app/models/user.py` uses UUID primary keys and timezone-aware timestamps elsewhere in the model layer. No generic user legal-acceptance model or account-deletion workflow existed.
- Marketing consent: `UserMarketingConsent` is a separate per-user/per-channel preference model and is not reused by LR5.3.
- Seller contract acceptance: `StoreContractAcceptance` and `StoreContractOtpChallenge` remain a separate Partners onboarding/OTP domain and are not reused by LR5.3.
- Template audit: every body in `DOCUMENT_VERSION_PREFIXES` is static HTML. The only Jinja expressions found in Docs bodies are in informational operator/contact pages, which are outside the version map. The snapshot service rejects unresolved Jinja markers fail-closed.
- Migration audit: `8d4e5f6a7b9c` was the single pre-LR5.3 Alembic head. No existing published legal-version records required migration.

## 3. Schema

`legal_document_versions` stores one immutable published-version evidence row with logical family/slug, canonical prefix, globally unique identifier, positive sequential version number, title/template/content snapshots, SHA-256 integrity digest, acceptance mode, publication/effective timestamps, optional source revision, and creation timestamp. It has uniqueness for `(family, slug, version_number)`, format and chronology checks, and a current-version lookup index.

`user_legal_acceptances` stores one user's action for one exact version with server timestamp, controlled source, optional normalized IP and bounded user-agent, plus redundant identifier/hash snapshots copied from the version. `(user_id, legal_document_version_id)` is unique. Both foreign keys use `RESTRICT`; the version FK is indexed for retention/dependency lookup.

Both tables have database triggers that reject `UPDATE` and `DELETE`, as well as ORM guards. Normal evolution is append-only. The migration is schema-only and inserts zero rows.

## 4. Immutable content evidence

- Canonicalization encodes text as UTF-8 and normalizes CRLF and bare CR to LF without rewriting semantic content.
- SHA-256 is computed over those canonical bytes and stored as a lowercase 64-character integrity digest. It is not a signature.
- The snapshot is the legal article body, not site chrome, request state, CSRF data, user data, hostnames, random values, or request timestamps.
- Publication metadata comes from the server-side static registry. The service rejects empty bodies and unresolved Jinja markers, preventing a raw dynamic template from being mislabeled as what a user saw.
- User evidence copies the identifier and digest from the immutable version server-side; callers cannot submit either snapshot value.

## 5. Version identifier convention

The frozen format is `<prefix>-YYYY-MM-DD-vN`: lowercase kebab-case prefix, the real publication date in `America/Guayaquil`, and a positive sequential number. Identifier, logical version number, prefix, and Ecuador publication date are validated together. Per-document PostgreSQL advisory locks serialize sequence allocation, including v1 creation.

Canonical mapped prefixes are:

`security`, `terms`, `purchase`, `payments`, `delivery`, `returns`, `warranty`, `claims`, `restricted-products`, `privacy`, `cookies`, `data-rights`, `marketing`, `acceptable-use`, `reviews-content`, `intellectual-property`, `fraud-abuse`, `account-suspension`, `seller-how-to-sell`, `seller-policies`, `seller-products`, `seller-commissions`, `seller-payouts`, `seller-logistics`, and `seller-privacy`.

`seller-contract` is reserved but intentionally has no Docs mapping. In particular, `/docs/vendedores/contrato` is an informational article and is not the canonical Partners contract.

## 6. Acceptance semantics

- Terms (`terms`) uses `ACCEPTED`: explicit electronic acceptance/evidence.
- Privacy (`privacy`) uses `ACKNOWLEDGED`: evidence that the notice was presented/acknowledged. This is not consent to data processing and not marketing permission.
- Supporting Buyer, Privacy, Platform, and Seller documents use `NONE` unless a later owner-approved design changes that rule.
- The marketing document uses `NONE`; `UserMarketingConsent` remains separate.
- The Seller contract is excluded from the generic user-evidence path; `StoreContractAcceptance` remains separate.

The recording service permits `ACCEPTED` only for an `ACCEPTED` version and `ACKNOWLEDGED` only for an `ACKNOWLEDGED` version. It rejects `NONE`, marketing, Seller support documents, the informational Seller contract, malformed versions, and wrong actions.

## 7. Current-version algorithm

`current_legal_version(session, family, slug, at)` considers only rows whose `published_at` and `effective_at` are not later than the requested timezone-aware instant. It orders by latest effective time, publication time, version number, and UUID for deterministic resolution. A published v2 effective in the future does not displace v1. No old row is mutated when a new row becomes current.

`legal_version_history` returns every immutable version in sequence, and `legal_version_by_identifier` resolves an exact identifier. Static `historical_versions` remains empty in LR5.3; database history will become the evidentiary source for public history integration in LR5.P.

## 8. Existing-user transition

NO BACKFILL.

The migration creates no acceptance evidence, and account existence, login, browsing, purchases, Seller status, or prior registration are never treated as acceptance. LR5.4/LR5.P must define an explicit transition after real versions are published.

## 9. Marketing audit

`UserMarketingConsent` currently records one row per user/channel with status, granted/revoked timestamps, source, and `policy_version`. It remains untouched and no marketing is activated. Its unique per-channel mutable row does not preserve a fully append-only event history for repeated grant/revoke cycles; that is a later marketing-specific design gap, not a reason to merge marketing into `UserLegalAcceptance`.

## 10. Seller contract audit

`StoreContractAcceptance` records one onboarding's contract/annex labels, acceptance flags, OTP verification, time, IP, user-agent, and optional PDF key. The OTP challenge and onboarding transition remain unchanged. It is intentionally separate because the Partners contract has distinct parties, verification, and lifecycle semantics.

Known blocker: the current Seller flow does not yet provide a canonical immutable contract-body snapshot/hash and multi-version historical acceptance system. Counsel/product must define Seller Contract Canonicalization before the reserved `seller-contract-YYYY-MM-DD-vN` namespace can be used. The informational Docs article cannot fill that role.

## 11. Retention/evidence considerations

The new FKs use `RESTRICT`, so deleting a User or LegalDocumentVersion cannot silently cascade away evidence. IP is normalized as IPv4/IPv6, user-agent is stripped and deterministically capped at 500 characters, both are nullable, and no full headers, passwords, tokens, sessions, or client timestamps are stored.

This foundation does not decide the complete retention, account-deletion, blocking, anonymization, litigation-hold, or purge schedule. Before production, ECUVEL should document the lawful basis, access controls, review period, and deletion/blocking procedure for each evidence field, with counsel review where legal/commercial retention applies.

## 12. LR5.4 integration contract

Routes should use the service layer without knowing table internals:

- `current_legal_version(session, family, slug, at)` obtains current Terms or Privacy.
- `current_required_user_versions(session, at)` obtains current mandatory user-facing versions; no published/effective row means no requirement.
- `missing_user_legal_requirements(session, user_id, at)` returns exact versions still pending.
- `has_user_legal_evidence(session, user_id, legal_document_version_id)` checks exact-version evidence.
- `record_user_legal_evidence(...)` records Terms `ACCEPTED` or Privacy `ACKNOWLEDGED` idempotently with server-derived version snapshots and already-resolved request metadata.

LR5.4 may call these from Register/Account/Checkout and re-check before order creation, but must not infer Privacy consent, marketing consent, or Seller contract acceptance. Earlier evidence remains intact when v2 becomes current.

## 13. LR5.P publication contract

Future publication must be one controlled, reviewed operation:

1. Confirm counsel/product approval and product-policy alignment.
2. Resolve the logical document from the static registry and verify its canonical prefix/template.
3. Determine the real timezone-aware publication instant and derive the identifier date in `America/Guayaquil`.
4. Load the exact static legal body, reject dynamic Jinja, canonicalize newlines, and calculate SHA-256.
5. Under the service's per-document transaction lock, allocate the next positive vN and insert one immutable `LegalDocumentVersion` row.
6. In the same reviewed release, change the matching registry document to `PUBLISHED` with consistent presentation metadata.
7. Leave every prior version/evidence row intact and expose database-backed history only through the later reviewed public routes.
8. Make the published Terms/Privacy version discoverable to LR5.4 requirements.

LR5.3 does not execute any step that publishes or seeds v1.

## 14. Remaining blockers

- Seller Contract Canonicalization remains open as described above.
- Marketing needs a separate future decision if full grant/revoke event history is required.
- Evidence retention/account-deletion procedures and the lawful basis for IP/user-agent retention require an operational schedule and counsel validation before production.
- Counsel should validate the final publication and acceptance UX and the evidentiary sufficiency for ECUVEL's concrete transactions. LR5.3 provides electronic evidence, not a qualified/certified electronic or digital signature.

Official Ecuadorian sources checked on 2026-09-10:

- [Ley de Comercio Electrónico, Firmas Electrónicas y Mensajes de Datos](https://www.gob.ec/sites/default/files/regulations/2018-10/LEY%20DE%20COMERCIO%20ELECTRONICO,%20FIRMAS%20Y%20MENSAJES%20DE%20DATOS.pdf): arts. 2, 6-8 and 52-55 support legal recognition, accessibility, integrity, conservation, and case-specific valuation of messages/data as evidence; arts. 13-15 separately regulate electronic signatures. A checkbox/action record and a SHA-256 digest are therefore not labeled a certified/qualified signature.
- [Código de Comercio](https://www.supercias.gob.ec/bd_supercias/descargas/lotaip/a2/2019/JUNIO/C%C3%B3digo_de_Comercio.pdf): arts. 74-75 recognize electronic commerce and refer electronic contracting, user/consumer rights, and proof to the electronic-commerce law and other applicable laws.
- [Ley Orgánica de Protección de Datos Personales](https://spdp.gob.ec/wp-content/uploads/2024/12/03.pdf.pdf): arts. 7, 9-12 require a lawful basis, transparency, minimization/proportionality, accuracy, security, and limited conservation; acknowledgment of a notice is not itself consent.
- [Reglamento General de la LOPDP](https://spdp.gob.ec/wp-content/uploads/2024/12/04.pdf.pdf): arts. 8-11 develop proportional retention and elimination, blocking, or anonymization when the purpose and lawful retention basis end.
- [Ley Orgánica de Defensa del Consumidor](https://www.produccion.gob.ec/wp-content/uploads/2019/08/A2-LEY-ORGANICA-DE-DEFENSA-DEL-CONSUMIDOR.pdf): consumer rules remain mandatory and favorable interpretation applies; electronic evidence infrastructure does not waive substantive consumer rights.

# The brain: how sites and the centre share what they learn

## Context

The reader is replaceable. Plenty of software extracts text from a PDF. What
isn't replaceable is the accumulated knowledge of **where each supplier prints
each value** — built one supplier at a time, and worth more the more customers
contribute to it. That shared store is the brain, and this document settles how
it fills up and how it is handed back out.

Today the sharing is real but small: templates are shared inside *one* database,
so a mapping helps the next customer only if they happen to use the same
installation. Once the product runs as a desktop install and as a hosted service
at the same time, "one database" stops being true, and the brain needs to become
a service that many independent sites talk to.

This is the concrete form of M2/M3 in
[`architecture-identity-central.md`](architecture-identity-central.md). Read that
one for the identity and enrollment design; this one covers the flow, the rule
that decides what gets shared, and what crosses the wire.

## The loop, from the customer's side

A municipality office drops invoices into a folder and expects importable files
out of another. From inside, one document moves like this:

```
  inbox/faktura.pdf
        │
        ▼
  ┌─ convert ──────────────────────────────────────────┐
  │  vendor recognised → apply its template            │
  │  otherwise         → layout heuristics             │
  └────────────────────────────────────────────────────┘
        │
        ├─ complete + reconciles ──► outbox/faktura.xml ──► contribute (see below)
        │
        └─ incomplete ──► stays queued
                              │
                     (customer's cron fires)
                              │
                     pull published templates ──► retry
                              │
                     still failing after N tries
                              │
                              ▼
                     notify the responsible user
```

The important move is **pull before retry**. An invoice that failed on Tuesday
because nobody had ever taught that supplier can succeed on Wednesday without
anyone at that site doing anything, because a *different* customer taught it in
the meantime. That is the network effect made mechanical, and it is the single
most convincing thing this product does.

The retry cadence is a per-customer setting (a cron expression). The pull is
incremental — only what changed since that site's last sync.

## Contributing: success is not the same as correct

**This is the rule everything else hangs off.** The heuristics can fill every
requested field and still be wrong: take the buyer's V-tal instead of the
supplier's, read a delivery date as the invoice date, pick a subtotal as the
total. Today that produces one customer's slightly-wrong export, which they
notice and fix. If every success is pushed to the brain, that same mistake is
handed to every other customer with full confidence, and nobody can tell where
it came from. **A brain that propagates confident errors is worse than no brain
at all.**

So a conversion earning the right to be *contributed* is a higher bar than
producing output:

| Signal | Where it comes from | Why it matters |
| --- | --- | --- |
| Every requested field located | `engine.CanonicalExtraction` | A partial read says nothing about the layout |
| Arithmetic reconciles | `app/validation.py` — net + VAT = gross, lines sum to total | A wrong total field almost always breaks the sum. This is the strongest cheap signal available |
| Dates sane | `validation.py` — due not before issue | Catches the classic date-column mix-up |
| Vendor identified | V-tal located and shaped correctly | A mapping filed under the wrong supplier poisons two vendors at once |

A read that clears all four is a **candidate mapping**. A read that doesn't
still contributes a *layout observation* (see the fingerprint below), because
knowing that a layout exists is useful even when the reading of it wasn't
trusted — but it never becomes a template anyone else receives.

### Promotion

Candidates do not go straight into circulation:

| State | How it gets there | Who sees it |
| --- | --- | --- |
| `unverified` | one site contributed it | only the site that produced it |
| `corroborated` | a second, independent site produced an equivalent mapping | published automatically |
| `verified` | a human at the centre reviewed it in the studio | published |
| `retired` | superseded, or withdrawn after producing bad reads | nobody; kept for audit |

Sites pull `corroborated` and `verified` only. Two independent sites agreeing is
strong evidence: they have different invoices from the same supplier, and the
same mapping worked for both. One site agreeing with itself is not evidence, so
corroboration must come from a different site identity.

"Equivalent" means the same canonical field resolved by the same strategy to the
same label, or to a region within a small tolerance — not byte-identical JSON.

A site's own teaching always beats an incoming template, which is already how
[`app/sync.py`](../app/sync.py) merges bundles. Nothing the centre publishes can
silently overwrite a mapping a customer fixed by hand.

### Withdrawing

Promotion is reversible. Export history records, per read, whether a template
was applied and whether the result reconciled. A published template whose reads
start failing validation across sites is evidence it was wrong or the supplier
changed their layout: retire it, and let the next corroboration replace it.
Because every template change is versioned
(`vendor_template_versions`), retiring is a rollback, not a loss.

## Matching: one V-tal is not one layout

A supplier sends invoices, credit notes and statements under a single V-tal,
laid out differently. Keyed on the vendor identifier alone, whichever document
type converted most recently wins, and the others quietly get worse.

So a template is keyed on **vendor identifier + layout fingerprint**, where the
fingerprint is derived from the document's structure rather than its contents:

- the set of label tokens found on page 1, normalised (lowercased, separators
  stripped, so `V-tal` / `Vtal` / `V TAL` are one token)
- each label's position, quantised into a coarse grid so small layout drift
  doesn't produce a new fingerprint
- page size and count bucket

Hashed, that becomes a stable identifier for "this shape of document from this
supplier". A new fingerprint under a known V-tal is a new document type, not a
reason to replace the existing template.

## What crosses the wire

Customers in the public sector will ask, and the answer needs to be short,
true, and checkable.

| Sent to the centre | Never sent |
| --- | --- |
| Vendor identifier (V-tal) — a business registration number | The PDF itself, in whole or in part |
| Label text as printed on the supplier's form (`"Fakturanr"`) | Any field **value**: amounts, dates, invoice numbers, account numbers |
| Normalised label and region positions | Line items, in any form |
| Canonical field each label maps to, and its type | The customer's identity, or which customer contributed |
| Layout fingerprint hash | File names, folder paths |
| A count of corroborating observations | Anything typed by a person into the studio as free text |

The principle: **the centre learns the shape of a supplier's form, never the
contents of anyone's invoice.** A supplier's blank form is not personal data and
is not commercially sensitive — it is printed on every invoice they send to
everyone. What sits in the boxes never leaves the site.

Two things to enforce rather than assume:

- **Label text is allowlisted, not free.** A label is only contributed if it
  matches the multilingual vocabulary in `app/config/labels.yaml` or is a short
  token that appears on many documents. That stops a stray line of body text —
  which could contain a name — being contributed as though it were a label.
- **Contribution is aggregate.** The centre stores that *some* site saw a
  layout, and how many did; it does not keep a per-site log of who processed
  which supplier. Which municipality buys from which supplier is exactly the
  sort of thing not worth knowing.

## Consent

The right to contribute is granted at purchase, in the terms, in plain words:
*we learn the layout of your suppliers' forms and share that layout with other
customers; we never send, store or share the contents of your invoices.*

**Pulling is not conditional on contributing.** A customer who opts out of
contributing still receives published templates. This is deliberate: it removes
the only real objection during procurement, and it costs nothing — serving a
template that already exists has no marginal cost, while an opt-out customer
still generates the corroborations that make the brain trustworthy. Reciprocity
sounds fair and would buy nothing.

## Identity

Sites push signed, so the brain cannot be poisoned by anyone who finds the
endpoint. That is the Ed25519 keypair and enrollment flow already designed in
[`architecture-identity-central.md`](architecture-identity-central.md) M2: a
deployment generates a keypair on first boot, the public key's fingerprint is
registered centrally and activated by an admin, and every sync request carries a
short-lived token signed with the private key. Revoking a site is flipping one
row.

Corroboration counts distinct **active** site fingerprints, which is what stops
one actor manufacturing agreement with itself.

## When it can't be read at all

Some documents will never convert: a photographed receipt, a scan with no text
layer and unreadable OCR, or something that isn't an invoice. Retrying those on
a cron forever is the same invisible failure as not retrying at all — the
invoice simply sits in the folder.

So the loop gives up. After a configurable number of attempts, or a number of
days, the document is marked `needs-attention` and the **responsible user** for
that folder is notified with what was found, what was missing, and a link to map
it. Mapping it produces a candidate mapping like any other, and if it's a
supplier nobody has taught, that one act of human attention is what the rest of
the network eventually receives.

## Where the code already stands

| Piece | State |
| --- | --- |
| Bundle format, export/import, merge semantics | `app/sync.py` — versioned, idempotent, local-wins |
| Per-vendor templates and detection by V-tal | `app/repo.py`, `app/engine.py` |
| Auto-learning a template on first sight of a vendor | `app/saas.py:_maybe_learn_vendor` |
| Validation signals for the promotion gate | `app/validation.py` |
| Per-read record of template-vs-heuristic and validity | `export_records` |
| Template history and rollback | `vendor_template_versions` |
| Folder in, review loop, reprocess | `app/workflow.py` |
| **Central service and its API** | not built |
| **Site identity and enrollment** | not built |
| **Layout fingerprints** | not built |
| **Promotion, corroboration, retirement** | not built |
| **Scheduled pull/push, give-up rule, notification** | not built |

The bottom half is the work. The top half is most of the hard thinking already
done.

## Open questions

1. **Corroboration threshold.** Two independent sites, or three? Two is fast and
   probably right while the customer base is small; it can be raised later
   without changing anything else.
2. **Auto-publish on corroboration, or always a human?** Auto-publish is what
   makes the network self-sustaining; a review queue is safer but makes you the
   bottleneck for every new supplier in the country.
3. **Does the hosted service count as one site or many?** Treating it as a single
   privileged site is simplest, but then two hosted customers agreeing counts as
   one corroboration.
4. **Retry budget before escalation** — attempts, elapsed days, or both.
5. **Who is the responsible user?** Per folder, per customer, or per supplier —
   this decides the shape of the notification settings.

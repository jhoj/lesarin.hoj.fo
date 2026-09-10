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
        ├─ complete ──► outbox/faktura.xml
        │                 (fields a person confirmed ──► contribute)
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

## Contributing: a person confirms it, or it doesn't travel

Every vendor invoice gets looked at by a person at least once, and some of its
fields get mapped by hand. That single fact decides the whole sharing model:
**the brain only ever receives mappings a human confirmed.**

The parser can be confidently wrong in ways nothing catches. The usual one: it
finds a V-tal on the page and files the template under it, but the number it
found was the customer's own from the letterhead, not the supplier's. Every
field is filled. Nothing complains. Shared automatically, that reads every one
of that supplier's invoices wrong for everybody, and it looks authoritative
because it came from the centre. A person mapping the field is what rules that
out — and the incentive is right, because the data lands in their own books.

### Trust belongs to a field, not to a template

A person maps *some* fields, not all of them. A template where four fields were
confirmed and six were guessed is four answers and six guesses, and publishing
it whole would ship the guesses as though someone had checked them.

So provenance is per field:

| Field mapping | Where it goes |
| --- | --- |
| Confirmed by a person in the mapping screen | Published to the brain |
| Filled in by the parser, nobody looked | Stays on that site. Never contributed |

A guessed field earns its way out the first time somebody confirms it. Until
then it is a local convenience, which is exactly what it should be.

This is why there is no counting, no threshold and no waiting for a second
customer to agree: confirmation happens once, at the customer, by the person
who cares most whether it's right. Nobody at the centre has to approve
anything, and nobody is a bottleneck.

### What this means for auto-learning

`app/saas.py:_maybe_learn_vendor` currently learns a template the first time an
unknown supplier is uploaded, with no person involved. That's the unsupervised
path, and it should stay — it's what makes a brand-new supplier produce
something useful on the first read. But what it produces is **unconfirmed**:
useful locally, never contributed. It earns its way to the brain when a person
looks at it.

### Confirm the supplier, not only the fields

Someone mapping fields is looking at boxes, not auditing the V-tal at the top of
the page. That leaves one hole a human in the loop doesn't close: a perfectly
confirmed template filed under the wrong company, which corrupts two suppliers
at once.

So the mapping screen shows the detected supplier — *"Supplier: Effo (V-tal
314188)"* — where it can't be missed, with an obvious way to correct it. Then
identity is confirmed alongside the fields, and the last real failure mode is
covered.

### When two people disagree

Two customers can confirm different answers for the same supplier. Last
confirmation wins for that layout, the previous one stays in
`vendor_template_versions` so nothing is lost, and the disagreement is worth a
quiet flag — not to block anything, only as a signal that one of those
suppliers deserves a look. A customer's own teaching always beats an incoming
template regardless, which is already how [`app/sync.py`](../app/sync.py)
merges.

### Withdrawing

Publishing is reversible. Export history records, per read, whether a template
was applied and whether the result reconciled. A published template whose reads
start failing validation across customers is evidence the supplier changed
their layout: withdraw it, and let the next confirmation replace it. Because
every change is versioned, withdrawing is a rollback rather than a loss.

## Two kinds of knowledge

Everything above describes one kind: **this supplier prints the invoice number
here**. That only helps the next customer who receives an invoice from that same
supplier.

There is a second kind, and it is the more valuable one: **the word "Fakturanr"
generally means invoice number**. That helps with suppliers nobody has ever
seen, which is most of them, and it is why a brand-new supplier often produces a
useful read on the very first try.

So the parser contributes more than the fields it managed to map. On every read
it harvests **every label-shaped token it can find** — the ones it understood,
the ones it didn't, and where each sits relative to the value beside it. A token
it can't place today is exactly the one worth learning:

- A label seen at one customer is a curiosity.
- The same label seen at many customers, in the same position relative to the
  same kind of value, is a fact about how invoices are written in this language.

That is how `app/config/labels.yaml` stops being a list somebody maintains by
hand and starts being something the product learns. The vocabulary is shared
across every supplier at once, so it improves the cold-start case that per-vendor
templates by definition cannot touch.

### Harvesting everything without leaking anything

Sending every token found on the page collides with the rule in *What crosses
the wire*, which said label text is allowlisted against `labels.yaml`. Both
matter, so the resolution is a threshold rather than a filter:

1. **Only label-shaped tokens.** One to three words, no digits, not an amount,
   date, email or account number, and positioned like a label — immediately left
   of or above a value. Body text and values never qualify.
2. **Hashed until it's common.** A token that hasn't been reported by at least
   **k customers** (start with k = 5) is stored only as a hash and a count. The
   centre can count agreement without ever holding the string.
3. **Kept in the clear only once k customers have independently reported it.**
   At that point it is, by definition, a phrase printed on many companies'
   invoice forms — not something belonging to any one customer.

This is the same reasoning as the rest of the privacy contract, applied one
level down: the centre gets to learn what the language of invoices looks like,
and never gets to hold a phrase only one customer has ever seen.


### A third kind: what customers do with the data

There is one more thing worth collecting, and it comes from the same screen.
After the fields are mapped, the customer picks which of them they actually
want and what to call them in the output.

**Which fields they want** tells you what the market uses. If nobody ever asks
for `Currency` and everybody wants `AccountNo`, that decides extraction
priorities and the canonical vocabulary by evidence instead of guesswork.

**What they rename them to** is the more interesting half. Those names aren't
yours and aren't the supplier's — they are the vocabulary of *the bookkeeping
system on the other side*. If a dozen customers all rename `InvoiceNo` to
`Bilagsnr`, they are almost certainly importing into the same accounting
package. Which gives you two things:

- **Presets.** A new customer picks their accounting system from a list instead
  of naming ten fields by hand. That removes the last piece of configuration
  the product asks of anyone.
- **Which integration to build next.** The most common output shape names the
  system that deserves a direct API rather than a file drop.

Output names get the same k-threshold as labels, and for the same reason: one
customer might name a field `inv_no_kommuna_fin`, which is their business and
nobody else's. Hashed with a count until k customers use the identical name for
the identical field, at which point it is a convention rather than a private
detail.

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
| Label-shaped tokens, hashed — in the clear only once k customers have reported the same one | Any field **value**: amounts, dates, invoice numbers, account numbers |
| Normalised label and region positions | Line items, in any form |
| Which field a label maps to (when known), and its type | The customer's identity, or which customer contributed |
| Layout fingerprint hash | File names, folder paths |
| How many customers have seen the same thing | Anything typed by a person into the studio as free text |
| Which canonical fields a customer selects, and the names they map them to (hashed until k customers use the same one) | Which customer selected them |

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
still confirms mappings that make the brain worth having. Reciprocity sounds
fair and would buy nothing.

## Identity

Sites push signed, so the brain cannot be poisoned by anyone who finds the
endpoint. That is the Ed25519 keypair and enrollment flow already designed in
[`architecture-identity-central.md`](architecture-identity-central.md) M2: a
deployment generates a keypair on first boot, the public key's fingerprint is
registered centrally and activated by an admin, and every sync request carries a
short-lived token signed with the private key. Revoking a site is flipping one
row.

Only active, enrolled sites can push at all, so a stranger who finds the
endpoint cannot write into the brain. Where a count is kept — the label
vocabulary and the output-name presets below — it counts **customers**, not
installations: a customer running three servers is still one voice.

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

## Recommended order of implementation

Sequenced so that each stage is useful on its own, the risky parts come after
the safe parts, and nothing publishes before there's evidence it should.

**Stage A — make it work for one customer, with no brain at all.**
The first municipality can be sold and served before any of this sharing
exists. `app/workflow.py` already reads a folder, tracks status per document
and reprocesses only what isn't done; what's missing is that the importable
file is buried inside the result sidecar instead of being written to an outbox,
that nothing runs it on a schedule, and that nobody is told when a document
needs a human. Finish that loop first — outbox files, scheduled run, give-up
rule, responsible user, notification. Until it exists there is no product to
sell; after it exists, everything below only makes it cheaper to run.

**Stage B — harvest locally, publish nothing.**
Have the parser emit every label-shaped token and its position on each read,
plus the layout fingerprint, and store both in the site's own database. No
network. This is safe by construction, it immediately improves local field
suggestions, and it produces the data every later stage depends on. It also
lets you look at real harvests and tune the token rules before any of it
leaves a customer's machine.

**Stage C — the trust plane.**
Stand up `central/` with site enrollment and signed requests (M2), and one
authenticated endpoint that accepts observations and does nothing with them.
Get identity working while it carries nothing valuable. A brain anyone can push
into is not worth building.

**Stage D — accumulate, still publishing nothing.**
Sites push label observations and fingerprints on the schedule. The centre
counts. Nothing flows back yet, so nothing can break a customer's reads. Run it
long enough to answer the empirical question the thresholds depend on: how many
customers does it take before a label crosses k, and does k = 5 leave the
vocabulary too thin in a country this size.

**Stage E — publish the vocabulary.**
Push the learned label vocabulary back to sites. This is the highest value for
the lowest blast radius: a synonym list improves the cold-start case for
suppliers nobody has seen, and a wrong entry degrades a guess rather than
corrupting a specific supplier's template. It is also the stage that proves the
round trip end to end while the stakes are still low.

**Stage F — publish templates.**
Per-vendor mappings, confirmed field by field by a person at the customer. This
is the one that can hand a wrong answer to everyone, so it goes last of the
publishing stages, on machinery already proven by E.

**Stage G — keep it honest.**
Withdrawal, driven by export history: watch published templates for reads that
stop reconciling across customers, and pull them automatically. Without this the
brain only ever accumulates, including its mistakes.

A useful property of this order: you can stop after any stage and still have
something coherent. Stop after A and you have a working single-customer product.
Stop after E and you have a product that gets better for everyone without ever
risking one customer's numbers on another's mistake.

## Where the code already stands

| Piece | State |
| --- | --- |
| Bundle format, export/import, merge semantics | `app/sync.py` — versioned, idempotent, local-wins |
| Per-vendor templates and detection by V-tal | `app/repo.py`, `app/engine.py` |
| Auto-learning a template on first sight of a vendor | `app/saas.py:_maybe_learn_vendor` |
| Validation signals for the promotion gate | `app/validation.py` |
| Per-read record of template-vs-heuristic and validity | `export_records` |
| Template history and rollback | `vendor_template_versions` |
| Folder in, review loop, reprocess | `app/workflow.py` — but no outbox, schedule or notification |
| **Central service and its API** | not built |
| **Site identity and enrollment** | not built |
| **Layout fingerprints** | not built |
| **Harvesting all label-shaped tokens** | not built — the parser finds them, but only keeps what it can map |
| **Marking a field as human-confirmed** | not built — the studio saves mappings, but doesn't record who decided them |
| **Learned label vocabulary** | partly — `labels.yaml` and `OutputField.aliases` exist, but are maintained by hand |
| **Per-field provenance, publishing, withdrawing** | not built |
| **Scheduled pull/push, give-up rule, notification** | not built |

The bottom half is the work. The top half is most of the hard thinking already
done.

## Open questions

Settled above and no longer open: publishing needs no approval from the centre,
a customer counts once however they run, and the brain only receives mappings a
person confirmed. What's left:

1. **Retry budget before escalation** — attempts, elapsed days, or both.
2. **Who is the responsible user?** Per folder, per customer, or per supplier —
   this decides the shape of the notification settings.
3. **k for the vocabulary and the output-name presets.** Five is written above
   as a starting point, chosen for caution rather than from evidence. Stage D
   exists to replace it with a real number.
4. **How long are observations kept below k?** They are hashes and counts, so
   the cost is small, but "forever" is rarely the right answer to write into a
   privacy policy.
5. **Does a confirmed mapping expire?** A supplier redesigns their invoice and
   an old confirmation quietly becomes wrong. Withdrawal catches it after the
   fact; an age limit would catch it sooner, at the cost of asking people to
   re-confirm things that are still fine.

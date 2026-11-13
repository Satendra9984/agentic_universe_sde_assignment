# AI Usage Disclosure

## Tools used

Cursor, with the coding agent (Grok).

## What I used them for

- Reading the assignment and the generated files, and explaining the pipeline in plain language before any code was written.
- Choosing the module split, the SQLite tables, and the PII approach, then writing the implementation and the tests.
- Running `pytest` and a full seed-42 ingest, then checking the report invariants.

## What I wrote or decided myself

The assignment choice was A, after walking through both problems. The design that the code follows was settled in that discussion before implementation:

- SQLite rather than Postgres.
- `ingest` writes, `report` only reads.
- Phone join key is an HMAC, with the last four digits stored for the mask. The name is encrypted.
- Orphans stay stored and are linked when a later drop brings the lead.
- Reports are recomputed from the whole ledger. Incremental aggregates were left as follow-up work.
- Unknown disposition codes are kept and ignored, and they count as orphans until the event is linked.

## Where the AI was wrong or unhelpful

The first command-line parser only accepted `--db` before the subcommand. `python -m solution ingest data/drop_1 --db au_assignment.sqlite` failed with "unrecognized arguments". The natural place for the flag is after the command. The parser was changed so the flag works in either position, and the seed-42 run was repeated with the flag after `ingest`.

A second approach that was rejected while writing the disposition rule: taking whichever disposition event has the latest timestamp, override or agent label alike. The spec says any override wins, even when the agent label is later. `test_an_override_beats_a_later_agent_disposition` locks that.

## How I verified AI-generated code

- `pytest`: 20 tests, including a two-drop fixture whose five output files are compared exactly, plus idempotent re-ingest and a check that the sample name and canonical phone are absent from the database file.
- Seed 42, scale small: both drops ingested, reports written, both drops ingested again. The second run read 0 files and the five report files were byte-identical.
- Invariants on that output: batch totals equal the lead-report row count, each batch's buckets sum to its lead count, connectivity rates equal connected/total to 4 decimals, rejection rows equal `lead_rows_rejected`, stored events equal `events_total - malformed - duplicates`, and no event is both an orphan and an unknown disposition.
- Spot checks: a `renewed` override shows `completed` and `ever_success=true`. A `payment_link_sent` override stays `in_progress_dialed`.

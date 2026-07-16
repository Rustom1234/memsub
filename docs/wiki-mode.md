# Wiki mode: markdown pages as a memory source

Wiki mode makes memsub a maintenance layer for an agent-written markdown wiki
(the "LLM Wiki" pattern). The documented failure mode of that pattern is that
contradictions and stale claims accumulate as the wiki grows past ~100 pages,
with no mechanism to notice. memsub ingests the wiki's pages as memories, so the
same consolidation pass that curates transcript memories can surface cross-page
contradictions for review.

## What it does

`memsub wiki ingest` reads every `.md` page under a folder, chunks each page
into atomic claims via the LLM backend, and stores them as episodes in the
store. Each claim carries provenance recording its source page and the page's
content hash:

- `origin: wiki`
- `source_page`: the page path relative to the wiki folder
- `source_content_hash`: sha256 of the page's bytes

Ingestion is idempotent and append-only:

- **Unchanged page: no-op.** The page path is checkpointed (the same
  `extraction_checkpoints` mechanism `memsub extract` uses). Re-ingesting a
  page whose bytes have not changed does no work.
- **Changed page: re-extract and supersede.** When a page's content hash
  changes, the page's prior active claims (matched on `source_page`) get a
  `superseded` event (reason `wiki_page_updated`) and the freshly extracted
  claims are appended. Nothing is deleted; the retired claims stay in the store
  for audit. The supersede happens before the append, so a crash mid-ingest
  heals on the next run rather than leaving two live generations of a page.

## The payoff loop

After ingesting a wiki, `memsub consolidate --stage` surfaces cross-page
contradictions as pending proposal notes in the vault, without touching the
store. A human reviews each proposal in Obsidian (or any editor), sets
`verdict: accept` or `verdict: reject` in its frontmatter, and `memsub vault
apply` executes the accepted supersedes and drops the rejected proposals. See
`docs/obsidian-layer.md` for the staging / approve-before-apply mechanics.

## Commands

```bash
# ingest a wiki folder into the store
memsub wiki ingest --db DB --pages path/to/wiki --backend ollama --model qwen3:8b

# re-run after the agent edits pages: unchanged pages skipped, edited pages
# re-extracted and their old claims superseded
memsub wiki ingest --db DB --pages path/to/wiki --backend ollama --model qwen3:8b

# surface cross-page contradictions as pending proposals (store untouched)
memsub consolidate --db DB --backend ollama --model qwen3:8b --stage --vault VAULT

# after setting verdict: accept/reject in the staged notes, apply them
memsub vault --db DB apply --vault VAULT
```

Options: `--namespace` files claims under a namespace (default `default`),
`--run-id` tags the run, `--max-chars` sets the chunk size.

`scripts/wiki_demo.sh` runs this whole loop against throwaway `/tmp` dirs with a
live backend, so you can watch it end to end without touching the live store.

## What it does NOT do

Honest limits, so nobody over-trusts this:

- **Extraction quality is unmeasured.** How well the backend turns a page into
  atomic claims is not benchmarked. A small local model paraphrases, drops
  nuance, or splits/merges claims differently than you would. The claims are
  only as good as the model and prompt.
- **Contradiction detection is the existing consolidation judge, nothing more.**
  A pair is only ever judged if it lands in the embedding-similarity band
  (roughly 0.45 to 0.92 cosine with the default hashing embedder) and shares a
  kind. Contradictions phrased in dissimilar language, or split across more than
  two claims, are not detected. The judge is a single LLM call; a smaller local
  model is an inconsistent judge and will both miss real contradictions and,
  occasionally, flag non-contradictions. `--stage` exists precisely because
  these judgements need human review before anything is applied.
- **No claim of general correctness.** memsub does not verify that a wiki's
  claims are true, only that two stored claims look mutually exclusive to the
  judge. It resolves nothing on its own: every destructive action is a proposal
  a human accepts or rejects.
- **Page structure is ignored.** A page is chunked as plain-text paragraphs.
  Headings, tables, and code blocks carry no special meaning; there is no
  section-aware or link-aware parsing.

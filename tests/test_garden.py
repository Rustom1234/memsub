"""memsub garden: publish-set resolution, PII scanner, asset guard, review
manifest, static builder. Every trust rule gets a test proving its failure
mode fails closed (allowlist, asset guard, PII block, inferred double-flag,
no-silent-publish)."""

from __future__ import annotations

from memsub import garden
from memsub.cli import main


def _write(root, relpath: str, text: str):
    path = root / relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _note(
    *,
    publish: str | None = "true",
    channel: str | None = "user_stated",
    publish_inferred: str | None = None,
    content: str = "Arjun shipped the waitlist page.",
    extra_fm: str = "",
    links: str = "",
    note_id: str = "1111111122222222",
) -> str:
    lines = ["---", f'id: "{note_id}"', "namespace: \"arjun-mehta\"", "kind: \"fact\""]
    if publish is not None:
        lines.append(f'publish: "{publish}"')
    if channel is not None:
        lines.append(f'channel: "{channel}"')
    if publish_inferred is not None:
        lines.append(f'publish_inferred: "{publish_inferred}"')
    lines.append('created: "2026-05-01T10:00:00"')
    lines.append('last_updated: "2026-05-02T10:00:00"')
    if extra_fm:
        lines.append(extra_fm)
    lines.append("---")
    lines.append("")
    lines.append(f"# {content[:40]}")
    lines.append("")
    lines.append("## Content")
    lines.append("")
    lines.append(content)
    lines.append("")
    if links:
        lines.append("## Links")
        lines.append("")
        lines.append(links)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# 1. Allowlist, fail-closed
# --------------------------------------------------------------------------- #
def test_note_without_publish_key_never_enters_publish_set(tmp_path):
    _write(tmp_path, "a.md", _note(publish=None))
    notes = garden.resolve_publish_set(tmp_path)
    assert notes == {}


def test_note_with_publish_false_never_enters_publish_set(tmp_path):
    _write(tmp_path, "a.md", _note(publish="false"))
    notes = garden.resolve_publish_set(tmp_path)
    assert notes == {}


def test_note_with_publish_true_is_published(tmp_path):
    _write(tmp_path, "a.md", _note(publish="true"))
    notes = garden.resolve_publish_set(tmp_path)
    assert notes["a.md"].status == garden.STATUS_PUBLISHED


# --------------------------------------------------------------------------- #
# 2. Asset leak guard
# --------------------------------------------------------------------------- #
def test_unreferenced_asset_is_never_copied(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="See the diagram.", extra_fm=""))
    _write(vault, "referenced.png", "PNGDATA")
    _write(vault, "orphan.png", "PNGDATA")
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.assets_copied == 0
    assert not (out / "assets" / "orphan.png").exists()
    assert not (out / "assets" / "referenced.png").exists()  # not referenced by any note body yet


def test_referenced_asset_is_copied_and_unreferenced_is_not(tmp_path):
    vault = tmp_path / "vault"
    body_with_ref = _note(content="See ![diagram](diagram.png) for the layout.")
    _write(vault, "a.md", body_with_ref)
    _write(vault, "diagram.png", "PNGDATA")
    _write(vault, "orphan.png", "PNGDATA")
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.assets_copied == 1
    assert (out / "assets" / "diagram.png").exists()
    assert not (out / "assets" / "orphan.png").exists()


def test_asset_reference_with_null_byte_is_excluded_not_a_crash(tmp_path):
    # Path.resolve() raises ValueError on an embedded null byte; a note
    # linking such a "path" must be excluded like any other bad reference,
    # not crash the whole build.
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="See ![x](foo\x00bar.png) for details."))
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.assets_copied == 0


def test_asset_reference_escaping_vault_root_is_excluded(tmp_path):
    vault = tmp_path / "vault"
    outside = tmp_path / "secret.txt"
    outside.write_text("do not leak", encoding="utf-8")
    _write(vault, "a.md", _note(content="See ![x](../secret.txt) for details."))
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.assets_copied == 0
    assert not any(out.rglob("secret.txt"))


# --------------------------------------------------------------------------- #
# 3. No silent publish / review gate
# --------------------------------------------------------------------------- #
def test_build_refuses_unreviewed_addition_by_default(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    out = tmp_path / "out"

    result = garden.build_site(vault, out)
    assert result.needs_review is not None
    assert "a.md" in result.needs_review.added
    assert not out.exists() or not (out / "index.html").exists()


def test_review_then_build_succeeds(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    out = tmp_path / "out"

    review_result = garden.review(vault)
    assert review_result.diff.added == ["a.md"]

    result = garden.build_site(vault, out)
    assert result.needs_review is None
    assert result.published == 1
    assert (out / "index.html").exists()


def test_build_with_yes_bypasses_review_and_records_approval(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.needs_review is None
    assert result.published == 1

    # a subsequent unreviewed build (no new changes) now succeeds without --yes
    result2 = garden.build_site(vault, out)
    assert result2.needs_review is None


def test_build_refuses_when_approved_note_content_changes(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Original content."))
    out = tmp_path / "out"
    garden.review(vault)
    result = garden.build_site(vault, out)
    assert result.needs_review is None

    _write(vault, "a.md", _note(content="Edited content, not yet reviewed."))
    result2 = garden.build_site(vault, out)
    assert result2.needs_review is not None
    assert "a.md" in result2.needs_review.changed


def test_idempotent_rebuild_produces_same_manifest(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    out = tmp_path / "out"
    garden.build_site(vault, out, yes=True)
    manifest1 = garden.load_manifest(vault)
    garden.build_site(vault, out)
    manifest2 = garden.load_manifest(vault)
    assert manifest1["approved"] == manifest2["approved"]
    assert (out / "notes").exists()
    assert len(list((out / "notes").glob("*.html"))) == 1


# --------------------------------------------------------------------------- #
# 4. PII block
# --------------------------------------------------------------------------- #
def test_pii_email_blocks_note_from_publish_set(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Reach Arjun at arjun.mehta@example.com."))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].status == garden.STATUS_BLOCKED_PII
    assert "email address" in notes["a.md"].pii_findings


def test_pii_blocked_note_never_appears_in_build_output(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Reach Arjun at arjun.mehta@example.com."))
    out = tmp_path / "out"
    result = garden.build_site(vault, out, yes=True)
    assert result.published == 0
    assert result.blocked_pii == 1
    assert not any(out.rglob("*.html")) or not list((out / "notes").glob("*.html"))


def test_clean_note_has_no_pii_findings(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Arjun shipped the waitlist page today."))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].pii_findings == []
    assert notes["a.md"].status == garden.STATUS_PUBLISHED


# --------------------------------------------------------------------------- #
# 5. llm_inferred double-flag rule
# --------------------------------------------------------------------------- #
def test_inferred_note_without_publish_inferred_flag_is_blocked(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(channel="llm_inferred", publish_inferred=None))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].status == garden.STATUS_INFERRED_AWAITING_FLAG


def test_inferred_note_with_publish_inferred_flag_is_published_as_seedling(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(channel="llm_inferred", publish_inferred="true"))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].status == garden.STATUS_PUBLISHED
    assert notes["a.md"].stage == "seedling"
    assert notes["a.md"].verified is False


def test_user_stated_note_is_evergreen_and_verified(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(channel="user_stated"))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].stage == "evergreen"
    assert notes["a.md"].verified is True


def test_human_confirmed_flag_is_evergreen_and_verified_regardless_of_channel(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(channel="imported", extra_fm='human_confirmed: "true"'))
    notes = garden.resolve_publish_set(vault)
    assert notes["a.md"].stage == "evergreen"
    assert notes["a.md"].verified is True


# --------------------------------------------------------------------------- #
# Stable URLs survive a title change
# --------------------------------------------------------------------------- #
def test_stable_url_survives_a_title_change(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Original sentence here.", note_id="deadbeefcafebabe"))
    notes = garden.resolve_publish_set(vault)
    url_before = notes["a.md"].id_prefix

    _write(vault, "a.md", _note(content="Completely different sentence now.", note_id="deadbeefcafebabe"))
    notes2 = garden.resolve_publish_set(vault)
    url_after = notes2["a.md"].id_prefix

    assert url_before == url_after == "deadbeef"


# --------------------------------------------------------------------------- #
# Backlinks
# --------------------------------------------------------------------------- #
def test_backlinks_computed_within_published_set(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="Arjun linked to [[b]] here.", note_id="aaaaaaaa11111111"))
    _write(vault, "b.md", _note(content="This is note B, unlinked back.", note_id="bbbbbbbb22222222"))
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.published == 2
    b_note_notes = garden.resolve_publish_set(vault)
    b_html = (out / "notes" / f"{b_note_notes['b.md'].id_prefix}.html").read_text(encoding="utf-8")
    assert "Linked from" in b_html
    a_url = f"{b_note_notes['a.md'].id_prefix}.html"
    assert a_url in b_html


def test_unresolved_wikilink_renders_as_private_plain_text(tmp_path):
    vault = tmp_path / "vault"
    # The target text is a stand-in for a memsub wikilink, whose target is a
    # slugified prefix of the linked memory's content; it must never appear
    # in the published HTML.
    _write(vault, "a.md", _note(content="See [[bank-account-4000-1234-not-published]] for context."))
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.published == 1
    notes = garden.resolve_publish_set(vault)
    html_text = (out / "notes" / f"{notes['a.md'].id_prefix}.html").read_text(encoding="utf-8")
    assert "wikilink private" in html_text
    assert "bank-account-4000-1234-not-published" not in html_text


def test_wikilink_to_note_that_exists_but_is_not_published_stays_private(tmp_path):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note(content="See [[b]] for context."))
    _write(vault, "b.md", _note(publish="false", content="Private note, never published."))
    out = tmp_path / "out"

    result = garden.build_site(vault, out, yes=True)
    assert result.published == 1  # only a.md
    notes = garden.resolve_publish_set(vault)
    html_text = (out / "notes" / f"{notes['a.md'].id_prefix}.html").read_text(encoding="utf-8")
    assert "wikilink private" in html_text


# --------------------------------------------------------------------------- #
# CLI wiring
# --------------------------------------------------------------------------- #
def test_cli_garden_status_reports_counts(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    _write(vault, "b.md", _note(channel="llm_inferred", publish_inferred=None, note_id="cccccccc33333333"))
    _write(vault, "c.md", _note(content="Email me at x@y.com", note_id="dddddddd44444444"))

    rc = main(["garden", "status", "--vault", str(vault)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "published: 1" in out
    assert "eligible, blocked by PII: 1" in out
    assert "inferred, awaiting publish_inferred flag: 1" in out


def test_cli_garden_review_and_build_roundtrip(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())

    rc = main(["garden", "review", "--vault", str(vault)])
    assert rc == 0
    capsys.readouterr()

    out_dir = tmp_path / "site"
    rc = main(["garden", "build", "--vault", str(vault), "--out", str(out_dir)])
    assert rc == 0
    assert (out_dir / "index.html").exists()


def test_cli_garden_build_without_review_fails_closed(tmp_path, capsys):
    vault = tmp_path / "vault"
    _write(vault, "a.md", _note())
    out_dir = tmp_path / "site"

    rc = main(["garden", "build", "--vault", str(vault), "--out", str(out_dir)])
    assert rc == 1
    out = capsys.readouterr().out
    assert "build refused" in out
    assert not out_dir.exists()

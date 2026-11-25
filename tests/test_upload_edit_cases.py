"""The upload-edit cases (scripts/build_upload_edit_cases.py): the sealed holdout is intact and apart from training."""
import hashlib
import json
import sys
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
sys.path.insert(0, str(DATA.parent / "scripts"))

from corpus_firewall import check_text_against_index, load_firewall_hashes  # noqa: E402


def test_the_holdout_is_sealed_and_apart_from_training():
    receipt = json.loads((DATA / "upload_edits_v1_build_receipt.json").read_text())
    raw = (DATA / "eval_upload_edits_v1.jsonl").read_bytes()
    assert hashlib.sha256(raw).hexdigest() == receipt["holdout"]["sha256"]
    rows = [json.loads(ln) for ln in raw.decode().splitlines()]
    assert len(rows) == receipt["holdout"]["items"] >= 400
    assert {r["slice"] for r in rows} == {"accepted", "held_meant", "held_none"}
    for r in rows:
        assert (r["expected_plain"] is None) == (r["slice"] == "held_none") == (r["expected_changes"] == [])
        assert r["prompt"].startswith("Task: rewrite_upload_edit\n")
        assert "\nLatest message: " in r["prompt"]  # the chat's plain_text of the message, so not always its text
    assert not {r["file"]["request"] for r in rows} & set(receipt["files"]["train_requests"])

    fw = load_firewall_hashes(DATA / "upload_edits_v1_firewall_hashes.json")
    sft = [json.loads(ln) for ln in (DATA / "sft_train_v1.jsonl").read_text().splitlines() if '"rewrite_upload_edit"' in ln]
    # CI rebuilds the legacy corpus before the tests, which has none of these rows; a checkout has them all
    assert len(sft) in (0, receipt["train"]["sft_rows"])
    held = {r["prompt"] for r in rows}
    for r in sft:
        latest = r["instruction"].rsplit("\nLatest message: ", 1)[1]
        hit = check_text_against_index(latest, fw)
        assert not (hit["exact_hit"] or hit["token_bag_hit"]), latest
        assert r["instruction"] not in held
        assert r["formatted_completion"].endswith(f"<output>\n{json.dumps(r['response'])}\n</output>")

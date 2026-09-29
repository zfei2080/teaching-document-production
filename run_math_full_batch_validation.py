"""MATH-VALIDATION-005: full text-only pool LLM math validation (batch runner).

Scope (per docs/work_orders/MATH-VALIDATION-005.md):
- New standalone script only. Does NOT modify existing code/schema/config.
- Works on a READ-ONLY COPY of data/dev/teaching_docs_dev.db in a temp dir;
  the real DB is never opened by this runner (SHA-256 checked outside).
- Does NOT read question source files; does NOT print/log/commit credentials.
- Model locked: deepseek-v4-flash, per-token CNY 3.6/1M in + 3.6/1M out
  (user-confirmed). Hard cost cap CNY 30.0, 50 questions per batch.
- Evidence is append-only JSONL; resume skips already-written question_ids.

Pool definition (reuses run_math_validation_pilot.build_pool = 001/003 audit
口径): no figure hint in stem, no asset_only image options, has answer or
analysis, not already pass, answer form in short/expr/long strata.

Prefilter (conservative, no API calls): stems with strong fragment markers are
marked unsupported (reason prefilter_*) before any LLM call.

Semantic equivalence: computed vs stored answers are normalized (NFKC,
full->half width, whitespace removed, punctuation stripped, common units
unified) and compared through explicit matchers (exact, multi-answer sets,
rationals, numeric, radicals, units). Undecidable -> unsupported
(reason equivalence_undetermined); never a string-only fail.

Usage:
  python run_math_full_batch_validation.py --copy-db
  python run_math_full_batch_validation.py [--max-batches N --batch-offset K]
  python run_math_full_batch_validation.py --resume
  python run_math_full_batch_validation.py --gen-review
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import random
import re
import shutil
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_math_validation_pilot as P

ROOT = Path(__file__).resolve().parent
REAL_DB = P.DB_PATH
PILOT_DIR = P.PILOT_DIR
CRED_PATH = P.CRED_PATH

MODEL = "deepseek-v4-flash"
PROMPT_VERSION = "llm-math-proof-v1-pilot-20260803"
VALIDATOR_ID = "llm-math-proof-v1"
VALIDATOR_VERSION = "1.2.0"  # 1.1.0 + semantic-equivalence adjudication + single retry
CONTRACT_ID = "content-question-math-contract"
CONFIDENCE_THRESHOLD = 0.9
MAX_OUTPUT_TOKENS = 2000
BATCH_SIZE = 50
COST_CAP_CNY = 30.0
INPUT_CNY_PER_1M = 3.6
OUTPUT_CNY_PER_1M = 3.6
SEED = 20260802
EVENT_ID = "full-batch-20260802-005"
CHINA_TZ = timezone(timedelta(hours=8))
REVIEW_RATIO = 0.10

EVIDENCE_PATH = PILOT_DIR / "MATH-VALIDATION-005_evidence.jsonl"
POOL_PATH = PILOT_DIR / "MATH-VALIDATION-005_pool.jsonl"
SUMMARY_PATH = PILOT_DIR / "MATH-VALIDATION-005_summary.jsonl"
REVIEW_MD = PILOT_DIR / "MATH-VALIDATION-005_review.md"
REVIEW_SAMPLE_PATH = PILOT_DIR / "MATH-VALIDATION-005_review_sample.jsonl"
DEFAULT_TMP = Path(__file__).resolve().parent / "_math005_tmp"


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def stable_hash(obj: object) -> str:
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_credential(path: Path) -> tuple[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    url = str(data.get("url") or "").strip()
    key = str(data.get("key") or "").strip()
    if not url or not key:
        raise RuntimeError("math-api.txt missing url/key")
    return url, key


def copy_db_to_temp(src: Path, tmp_root: Path) -> Path:
    tmp_root.mkdir(parents=True, exist_ok=True)
    dst = tmp_root / "teaching_docs_dev_copy.db"
    shutil.copy2(src, dst)
    return dst


# ---------------------------------------------------------------------------
# Conservative prefilter (no API calls). "宁可多留 API 题也不误伤".
# ---------------------------------------------------------------------------

PREFILTER_RULES = {
    "prefilter_missing_condition": re.compile(r"\uff0c\uff0c|,,|\uff0c\s{2,}\uff0c"),
    "prefilter_stem_content_missing": re.compile(r"[:：=＝]\s*[/／]\s*$"),
    "prefilter_solution_residual": re.compile(r"[\u2234\u2235]"),
}


def prefilter_reason(stem: str) -> str | None:
    for rule, rx in PREFILTER_RULES.items():
        if rx.search(stem):
            return rule
    return None


# ---------------------------------------------------------------------------
# Normalization and semantic equivalence (work order requirement 4)
# ---------------------------------------------------------------------------

_UNIT_MAP = [
    ("\u5ea6", "\u00b0"),            # 度 -> °
    ("\u5398\u7c73", "cm"),          # 厘米 -> cm
    ("\u6beb\u7c73", "mm"),
    ("\u5343\u7c73", "km"),
    ("\u514b", "g"),
    ("\u5343\u514b", "kg"),
    ("\u5428", "t"),
    ("\u5347", "L"),
]


def normalize_answer(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    for src, dst in _UNIT_MAP:
        text = text.replace(src, dst)
    text = text.replace("\u00d7", "x").replace("\u00b7", "x")  # × · -> x
    text = "".join(text.split())
    return text.rstrip(";;\uff1b.\u3002,\uff0c\u3001\uff01!")


def split_multi(text: str) -> list[str]:
    parts = re.split(r"\u6216|\uff1b|;|,|\uff0c|\u3001|\u548c", text)
    return [p for p in (normalize_answer(x) for x in parts) if p]


def _try_int(text: str):
    try:
        return int(text)
    except (TypeError, ValueError):
        return None


def _try_float(text: str):
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _rational(clean: str):
    m = re.fullmatch(r"([+-]?\d+)/([+-]?\d+)", clean)
    if not m:
        return None
    num, den = int(m.group(1)), int(m.group(2))
    if den == 0:
        return None
    return num / den


def _radical_value(clean: str):
    # forms: "√12", "3√2", "√(12)", "3√(2)"
    m = re.fullmatch(r"(\d*)\u221a\(?(\d+)\)?", clean)
    if not m:
        return None
    coef = float(m.group(1)) if m.group(1) else 1.0
    rad = int(m.group(2))
    if rad < 0:
        return None
    return coef * math.sqrt(rad)


def _percent(clean: str):
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)%", clean)
    return float(m.group(1)) / 100.0 if m else None


def _numeric_value(clean: str):
    val = _try_int(clean)
    if val is not None:
        return float(val)
    val = _try_float(clean)
    if val is not None:
        return val
    val = _rational(clean)
    if val is not None:
        return val
    val = _percent(clean)
    if val is not None:
        return val
    val = _radical_value(clean)
    if val is not None:
        return val
    return None


_COUNT_NOUNS = frozenset(
    "\u6761\u4e2a\u540d\u4f4d\u6b21\u4eba\u672c\u652f\u53ea\u68f5\u8f66\u8f86\u5929\u5468\u6708\u5e74\u9875\u9898"
)  # 条/个/名/位/次/人/本/支/只/棵/辆/天/周/月/年/页/题
_CJK_UNITS = "\u5e73\u65b9\u5398\u7c73\u5206\u6beb\u5343\u514b\u5347\u5428\u5143"  # 平方厘米分米毫米千米克升吨元


def _unit_numeric(text: str):
    """number+unit (e.g. 40cm, 130元, 2条) -> (float, unit) or None."""
    m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([A-Za-z\u00b0%]+)", text)
    if not m:
        m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([" + _CJK_UNITS + "]{1,3})", text)
    if not m:
        m = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([\u4e00-\u9fff]{1,2})", text)
    if not m:
        return None
    val = _try_float(m.group(1))
    if val is None:
        return None
    return val, m.group(2)


def _same_set(parts_l: list[str], parts_r: list[str]) -> bool:
    if len(parts_l) != len(parts_r) or len(parts_l) < 2:
        return False
    return set(parts_l) == set(parts_r)


def semantic_equivalence(computed: str | None, stored: str | None):
    """Returns True (equivalent), False (positively different), or None
    (undetermined -> caller must use unsupported, never fail on strings)."""
    l = normalize_answer(computed)
    r = normalize_answer(stored)
    if not l or not r:
        return None
    if l == r:
        return True
    # single choice letters (A-F, case-insensitive): definitively same or
    # different. letter vs non-letter is undetermined (possible source-data
    # corruption) -> fail-closed to unsupported.
    l_letter = re.fullmatch(r"[A-Fa-f]", l) is not None
    r_letter = re.fullmatch(r"[A-Fa-f]", r) is not None
    if l_letter and r_letter:
        return l.upper() == r.upper()
    parts_l, parts_r = split_multi(l), split_multi(r)
    if _same_set(parts_l, parts_r):
        return True
    # numeric equivalence (int/float/fraction/percent/radical)
    nl, nr = _numeric_value(l), _numeric_value(r)
    if nl is not None and nr is not None:
        return math.isclose(nl, nr, rel_tol=1e-9, abs_tol=1e-9)
    # unit-aware numeric equivalence (40° vs 40度 already unified; cm vs 厘米 mapped)
    ul, ur = _unit_numeric(l), _unit_numeric(r)
    if ul is not None and ur is not None:
        if ul[1] == ur[1]:
            return math.isclose(ul[0], ur[0], rel_tol=1e-9, abs_tol=1e-9)
        if ul[1] in _COUNT_NOUNS or ur[1] in _COUNT_NOUNS:
            return math.isclose(ul[0], ur[0], rel_tol=1e-9, abs_tol=1e-9)
    # plain number vs number+count-noun (2条 == 2); count nouns carry no dimension
    ln, rn = _try_float(l), _try_float(r)
    if ln is not None and ur is not None and ur[1] in _COUNT_NOUNS:
        return math.isclose(ln, ur[0], rel_tol=1e-9, abs_tol=1e-9)
    if rn is not None and ul is not None and ul[1] in _COUNT_NOUNS:
        return math.isclose(rn, ul[0], rel_tol=1e-9, abs_tol=1e-9)
    return None


# ---------------------------------------------------------------------------
# Evidence record builders
# ---------------------------------------------------------------------------

def build_user_message(rec: dict) -> str:
    return P.build_user_message(rec)


def prefilter_record(rec: dict, reason: str) -> dict:
    content_payload = {"question_id": rec["question_id"], "stem": rec["stem"], "options": rec["options"]}
    input_payload = {"prefilter_rule": reason, "question_id": rec["question_id"], "prompt_version": PROMPT_VERSION}
    evidence = {
        "model": None,
        "prompt_version": PROMPT_VERSION,
        "reasoning_text": None,
        "answer_derivation": None,
        "confidence": None,
        "figure_used": False,
        "tokens_used": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        "warnings": [reason],
        "prefilter_rule": reason,
    }
    record = {
        "question_id": rec["question_id"],
        "question": {"stem": rec["stem"], "options": rec["options"]},
        "content_item_id": rec["content_item_id"],
        "validation_contract_id": CONTRACT_ID,
        "validator_id": VALIDATOR_ID,
        "validator_version": VALIDATOR_VERSION,
        "validation_status": "unsupported",
        "computed_answer": None,
        "source_answer": rec["answer"],
        "content_sha256": stable_hash(content_payload),
        "input_sha256": stable_hash(input_payload),
        "evidence_json": evidence,
        "evidence_sha256": "",
        "created_change_event_id": EVENT_ID,
        "created_at": now_iso(),
        "reason": reason,
        "answer_form": rec["answer_form"],
        "stratum": rec["stratum"],
        "stage_a_reason": rec["stage_a_reason"],
        "question_type": rec["question_type"],
        "prefiltered": True,
    }
    record["evidence_sha256"] = stable_hash(record)
    return record


def finalize_result(rec: dict, parsed: dict | None) -> dict:
    if parsed is None:
        return {"status": "unsupported", "reason": "unparsable_json", "computed": None, "confidence": None}
    status = str(parsed.get("status") or "").strip().lower()
    computed = str(parsed.get("computed_answer") or "").strip()
    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = None
    if status == "unsupported":
        return {"status": "unsupported", "reason": "model_unsupported", "computed": computed, "confidence": confidence}
    if confidence is None or confidence < CONFIDENCE_THRESHOLD:
        return {"status": "unsupported", "reason": "low_confidence", "computed": computed, "confidence": confidence}
    stored = rec["answer"]
    if not stored or not stored.strip():
        return {"status": "unsupported", "reason": "answer_missing_no_comparison", "computed": computed, "confidence": confidence}
    eq = semantic_equivalence(computed, stored)
    if eq is True:
        return {"status": "pass", "reason": None, "computed": computed, "confidence": confidence}
    if eq is False:
        return {"status": "fail", "reason": "answer_mismatch", "computed": computed, "confidence": confidence}
    return {"status": "unsupported", "reason": "equivalence_undetermined", "computed": computed, "confidence": confidence}


def build_record(rec: dict, result: dict, parsed: dict | None, prompt_tokens: int, completion_tokens: int) -> dict:
    content_payload = {"question_id": rec["question_id"], "stem": rec["stem"], "options": rec["options"]}
    input_payload = {
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "question_id": rec["question_id"],
        "system_prompt": P.SYSTEM_PROMPT,
        "user_message": build_user_message(rec),
    }
    evidence = {
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "reasoning_text": (parsed or {}).get("reasoning_text"),
        "answer_derivation": (parsed or {}).get("answer_derivation"),
        "confidence": (parsed or {}).get("confidence"),
        "figure_used": False,
        "tokens_used": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
        "warnings": (parsed or {}).get("warnings") or (["unparsable_json"] if parsed is None else []),
    }
    record = {
        "question_id": rec["question_id"],
        "question": {"stem": rec["stem"], "options": rec["options"]},
        "content_item_id": rec["content_item_id"],
        "validation_contract_id": CONTRACT_ID,
        "validator_id": VALIDATOR_ID,
        "validator_version": VALIDATOR_VERSION,
        "validation_status": result["status"],
        "computed_answer": result.get("computed"),
        "source_answer": rec["answer"],
        "content_sha256": stable_hash(content_payload),
        "input_sha256": stable_hash(input_payload),
        "evidence_json": evidence,
        "evidence_sha256": "",
        "created_change_event_id": EVENT_ID,
        "created_at": now_iso(),
        "reason": result.get("reason"),
        "answer_form": rec["answer_form"],
        "stratum": rec["stratum"],
        "stage_a_reason": rec["stage_a_reason"],
        "question_type": rec["question_type"],
        "prefiltered": False,
    }
    record["evidence_sha256"] = stable_hash(record)
    return record


# ---------------------------------------------------------------------------
# LLM call (single question, one retry for JSON/API failure)
# ---------------------------------------------------------------------------

def llm_validate(base_url: str, key: str, rec: dict, session) -> tuple[dict | None, int, int]:
    import requests

    url = base_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "math-validation-pilot/1.0",
    }
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": P.SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(rec)},
        ],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }
    resp = session.post(url, headers=headers, json=payload, timeout=240)
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("no choices in response")
    content = choices[0].get("message", {}).get("content") or ""
    usage = data.get("usage") or {}
    pt = int(usage.get("prompt_tokens") or 0)
    ct = int(usage.get("completion_tokens") or 0)
    return P.parse_llm_json(content), pt, ct


def is_rate_limit(exc: Exception) -> bool:
    text = str(exc)
    return "429" in text or "rate limit" in text.lower() or "too many requests" in text.lower()


def estimate_cost_cny(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * INPUT_CNY_PER_1M + completion_tokens * OUTPUT_CNY_PER_1M) / 1_000_000.0


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def load_lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def write_record(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_pool(path: Path, pool: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for rec in pool:
            slim = {
                "question_id": rec["question_id"],
                "content_item_id": rec["content_item_id"],
                "question_type": rec["question_type"],
                "source_document_id": rec["source_document_id"],
                "source_question_no": rec["source_question_no"],
                "answer_form": rec["answer_form"],
                "stratum": rec["stratum"],
                "stage_a_reason": rec["stage_a_reason"],
                "prefilter_rule": rec.get("prefilter_rule"),
            }
            fh.write(json.dumps(slim, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Review sampling (>=10%, stratified by validation_status then stratum)
# ---------------------------------------------------------------------------

def build_review_sample(records: list[dict], ratio: float, seed: int) -> list[dict]:
    rng = random.Random(seed)
    by_status: dict[str, list[dict]] = collections.defaultdict(list)
    for rec in records:
        by_status[rec["validation_status"]].append(rec)
    sampled: list[dict] = []
    for status in sorted(by_status):
        group = by_status[status]
        target = max(1, int(math.ceil(len(group) * ratio)))
        rng.shuffle(group)
        taken = group[:target]
        taken.sort(key=lambda r: (r["stratum"], r["question_id"]))
        sampled.extend(taken)
    sampled.sort(key=lambda r: (r["validation_status"], r["stratum"], r["question_id"]))
    return sampled


def build_review_md(sample: list[dict]) -> str:
    lines = [
        "# MATH-VALIDATION-005 — 复核表（独立复核）",
        "",
        "| # | 题号 | 判定 | 题目摘要 | 计算结果 | 源答案 | 置信度 | 推理摘要 | 独立复核 | 不一致/备注 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for idx, rec in enumerate(sample, start=1):
        stem = (rec.get("question") or {}).get("stem") or ""
        summary = stem[:70].replace("|", "/").replace("\n", " ")
        if len(stem) > 70:
            summary += "…"
        ev = rec.get("evidence_json") or {}
        reasoning = (ev.get("reasoning_text") or "")[:50].replace("|", "/").replace("\n", " ")
        if len(ev.get("reasoning_text") or "") > 50:
            reasoning += "…"
        qid = rec.get("question_id", "")[-12:]
        lines.append(
            f"| {idx} | `{qid}` | {rec.get('validation_status')} | {summary} | "
            f"{(rec.get('computed_answer') or '')[:40]} | {(rec.get('source_answer') or '')[:40]} | "
            f"{ev.get('confidence') or ''} | {reasoning} | 待复核 | |"
        )
    lines.append("")
    lines.append("复核说明：独立复核由执行会话对照题目/源答案与模型计算结果判定；不一致项必须标记并分析原因。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tmp", type=str, default=None, help="temp dir for the DB copy (default: %TMP%/math-005)")
    parser.add_argument("--max-batches", type=int, default=0, help="max batches (50 questions each) to process; 0=all")
    parser.add_argument("--batch-offset", type=int, default=0, help="start at this batch index")
    parser.add_argument("--resume", action="store_true", help="skip question_ids already in the evidence file")
    parser.add_argument("--gen-review", action="store_true", help="only (re)generate review sample + table from evidence")
    parser.add_argument("--review-ratio", type=float, default=REVIEW_RATIO)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    if args.gen_review:
        records = load_lines(EVIDENCE_PATH)
        if not records:
            print("no evidence records", file=sys.stderr)
            return 2
        sample = build_review_sample(records, args.review_ratio, args.seed)
        with open(REVIEW_SAMPLE_PATH, "w", encoding="utf-8") as fh:
            for rec in sample:
                slim = {
                    "question_id": rec["question_id"],
                    "validation_status": rec["validation_status"],
                    "reason": rec.get("reason"),
                    "stratum": rec["stratum"],
                    "answer_form": rec["answer_form"],
                    "computed_answer": rec.get("computed_answer"),
                    "source_answer": rec.get("source_answer"),
                    "confidence": (rec.get("evidence_json") or {}).get("confidence"),
                    "reasoning_text": (rec.get("evidence_json") or {}).get("reasoning_text"),
                    "stem": (rec.get("question") or {}).get("stem"),
                }
                fh.write(json.dumps(slim, ensure_ascii=False) + "\n")
        REVIEW_MD.write_text(build_review_md(sample), encoding="utf-8")
        print("REVIEW_SAMPLE", len(sample))
        print("REVIEW_MD", REVIEW_MD)
        return 0

    if not REAL_DB.exists():
        print("FATAL db not found", REAL_DB, file=sys.stderr)
        return 2
    db_sha_before = sha256_file(REAL_DB)

    tmp_root = Path(args.tmp) if args.tmp else Path(sys.argv[0]).resolve().parent / "_math005_tmp"
    db_copy = copy_db_to_temp(REAL_DB, tmp_root)
    print("DB_COPY", db_copy)
    print("DB_SHA256_REAL_BEFORE", db_sha_before)

    conn = sqlite3.connect(f"file:{db_copy.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    try:
        pool, stats = P.build_pool(conn)
    finally:
        conn.close()
    print("POOL_STATS", json.dumps(dict(stats), ensure_ascii=False))
    print("POOL_SIZE", len(pool))

    for rec in pool:
        rec["prefilter_rule"] = prefilter_reason(rec["stem"])
    prefiltered = [r for r in pool if r["prefilter_rule"]]
    to_run = [r for r in pool if not r["prefilter_rule"]]
    print("PREFILTERED", len(prefiltered), "TO_RUN", len(to_run))
    write_pool(POOL_PATH, pool)

    records: list[dict] = []
    done_ids = set()
    if args.resume and EVIDENCE_PATH.exists():
        for rec in load_lines(EVIDENCE_PATH):
            records.append(rec)
            done_ids.add(rec["question_id"])
    print("RESUME done", len(done_ids))

    # write prefiltered evidence records (once)
    for rec in prefiltered:
        if rec["question_id"] in done_ids:
            continue
        record = prefilter_record(rec, rec["prefilter_rule"])
        records.append(record)
        write_record(EVIDENCE_PATH, record)
        done_ids.add(rec["question_id"])
    print("PREFILTER_WRITTEN", len([r for r in records if r.get("prefiltered")]))

    to_run = [r for r in to_run if r["question_id"] not in done_ids]
    if args.batch_offset:
        to_run = to_run[args.batch_offset * BATCH_SIZE :]
    if args.max_batches:
        to_run = to_run[: args.max_batches * BATCH_SIZE]

    total_prompt = sum(r["evidence_json"]["tokens_used"]["prompt_tokens"] for r in records)
    total_completion = sum(r["evidence_json"]["tokens_used"]["completion_tokens"] for r in records)
    total_cost = sum(
        estimate_cost_cny(
            r["evidence_json"]["tokens_used"]["prompt_tokens"],
            r["evidence_json"]["tokens_used"]["completion_tokens"],
        )
        for r in records
    )

    print("TO_RUN", len(to_run), "MODEL", MODEL, "PROMPT_VERSION", PROMPT_VERSION)
    print("COST_CAP", COST_CAP_CNY, "RATES in/out", INPUT_CNY_PER_1M, OUTPUT_CNY_PER_1M, "per 1M")

    url, key = load_credential(CRED_PATH)
    base = url.rstrip("/")
    import requests

    session = requests.Session()
    stop_reason = None
    consecutive_hard_fail = 0
    for batch_idx in range(0, len(to_run), BATCH_SIZE):
        batch = to_run[batch_idx : batch_idx + BATCH_SIZE]
        print(f"BATCH idx={batch_idx} n={len(batch)} cum_cost={total_cost:.4f}", flush=True)
        for rec in batch:
            time.sleep(1.2)
            if total_cost > COST_CAP_CNY:
                stop_reason = "cost_cap_exceeded"
                print("STOP_COST_CAP", flush=True)
                break
            parsed = None
            pt = ct = 0
            last_err: Exception | None = None
            hit_rl = False
            # 429 = relay rpm/concurrency limit (input=0, no billing): fixed 30s
            # cooldown retry up to 8 attempts to ride out the quota window.
            # Other errors: retry once per work order.
            for attempt in range(8):
                try:
                    parsed, pt, ct = llm_validate(base, key, rec, session)
                    last_err = None
                    break
                except Exception as exc:
                    last_err = exc
                    is_rl = is_rate_limit(exc)
                    hit_rl = hit_rl or is_rl
                    if not is_rl and attempt >= 1:
                        break
                    if is_rl and attempt >= 7:
                        break
                    delay = 30 if is_rl else 2 * (attempt + 1)
                    print(f"  RETRY q={rec['question_id'][-12:]} attempt={attempt} rl={is_rl} err={type(exc).__name__}: {str(exc)[:140]}", flush=True)
                    time.sleep(delay)
            if last_err is not None:
                if hit_rl:
                    # transient relay quota limit: keep going, do not stop the run
                    consecutive_hard_fail = 0
                    result = {"status": "unsupported", "reason": "api_error_rate_limited", "computed": None, "confidence": None}
                    print(f"  RL q={rec['question_id'][-12:]} status=unsupported reason=api_error_rate_limited", flush=True)
                else:
                    consecutive_hard_fail += 1
                    if consecutive_hard_fail >= 3:
                        stop_reason = f"api_unavailable:{type(last_err).__name__}:{str(last_err)[:120]}"
                        print("STOP_API_UNAVAILABLE", stop_reason, flush=True)
                        break
                    result = {"status": "unsupported", "reason": "api_error", "computed": None, "confidence": None}
                record = build_record(rec, result, None, 0, 0)
            else:
                consecutive_hard_fail = 0
                result = finalize_result(rec, parsed)
                record = build_record(rec, result, parsed, pt, ct)
            records.append(record)
            write_record(EVIDENCE_PATH, record)
            total_prompt += pt
            total_completion += ct
            total_cost += estimate_cost_cny(pt, ct)
            print(
                f"  OK q={rec['question_id'][-12:]} status={result['status']} reason={result.get('reason') or '-'} "
                f"tokens={pt + ct} cum_cost={total_cost:.4f}",
                flush=True,
            )
        if stop_reason:
            break

    counter = collections.Counter(r["validation_status"] for r in records)
    reasons = collections.Counter(r.get("reason") for r in records)
    print("TOTAL_RECORDS", len(records))
    print("STATUS", json.dumps(dict(counter), ensure_ascii=False))
    print("REASONS", json.dumps(dict(reasons), ensure_ascii=False))
    print("TOKENS prompt/completion/total", total_prompt, total_completion, total_prompt + total_completion)
    print("COST_CNY", f"{total_cost:.6f}")
    print("PER_1000_CNY", f"{total_cost / max(len(records), 1) * 1000:.6f}")
    print("STOP_REASON", stop_reason)
    print("DB_SHA256_REAL_AFTER", sha256_file(REAL_DB))

    summary = {
        "pilot": "MATH-VALIDATION-005",
        "model": MODEL,
        "prompt_version": PROMPT_VERSION,
        "validator_version": VALIDATOR_VERSION,
        "pool_size": len(pool),
        "prefiltered": len(prefiltered),
        "llm_run": len(records) - len(prefiltered),
        "status": dict(counter),
        "reasons": dict(reasons),
        "tokens": {"prompt": total_prompt, "completion": total_completion, "total": total_prompt + total_completion},
        "cost_cny": total_cost,
        "cost_per_1000_cny": total_cost / max(len(records), 1) * 1000,
        "cost_basis": {"input_cny_per_1m": INPUT_CNY_PER_1M, "output_cny_per_1m": OUTPUT_CNY_PER_1M, "source": "user-confirmed per-token billing (2026-08-02)"},
        "db_sha256_before": db_sha_before,
        "db_sha256_after": sha256_file(REAL_DB),
        "stop_reason": stop_reason,
        "event_id": EVENT_ID,
    }
    with open(SUMMARY_PATH, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print("SUMMARY", SUMMARY_PATH)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



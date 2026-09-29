"""MATH-VALIDATION-003: Stage B 100-question LLM validation pilot runner.

Scope (per work order): new standalone script only. It does NOT modify any
existing code/schema/config, does NOT write the real database (read-only
SQLite URI mode + PRAGMA query_only), and does NOT read question source files.

Credential: reads <local-path> (JSON
url/key/_type). The key is used only to configure the OpenAI-compatible client
and is never printed, logged, or written to outputs.

Outputs (UTF-8):
  docs/validation_pilot/MATH-VALIDATION-003_sample.jsonl   reproducible sample
  docs/validation_pilot/MATH-VALIDATION-003_results.jsonl  evidence records
  docs/validation_pilot/MATH-VALIDATION-003_review.md      10-question review table

Usage:
  python run_math_validation_pilot.py --sample-only     # sampling dry run
  python run_math_validation_pilot.py                   # full pilot
  python run_math_validation_pilot.py --max-batches 5 --batch-offset 0
  python run_math_validation_pilot.py --resume          # skip already-written ids
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import random
import re
import sqlite3
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "dev" / "teaching_docs_dev.db"
CRED_PATH = ROOT / "math-api.txt"
PILOT_DIR = ROOT / "docs" / "validation_pilot"
RESULTS_PATH = PILOT_DIR / "MATH-VALIDATION-003_results.jsonl"
SAMPLE_PATH = PILOT_DIR / "MATH-VALIDATION-003_sample.jsonl"
REVIEW_PATH = PILOT_DIR / "MATH-VALIDATION-003_review.md"

SEED = 20260803
BATCH_SIZE = 10
TOTAL = 100
TARGET_SHORT, TARGET_EXPR, TARGET_LONG = 70, 25, 5
COST_CAP_CNY = 50.0
CONFIDENCE_THRESHOLD = 0.9
PROMPT_VERSION = "llm-math-proof-v1-pilot-20260803"
VALIDATOR_ID = "llm-math-proof-v1"
VALIDATOR_VERSION = "1.1.0"
CONTRACT_ID = "content-question-math-contract"
MODEL = "deepseek-v4-pro-thinking"
MAX_OUTPUT_TOKENS = 2000
# Conservative cost basis (CNY per 1M tokens): public DeepSeek reasoner-tier
# prices used as an upper-bound estimate for the channel; actual billing may
# differ and is reported as such. Adjust via CLI/environment if needed.
INPUT_CNY_PER_1M = 4.0
OUTPUT_CNY_PER_1M = 16.0
CNY_PER_USD = 7.2
CHINA_TZ = timezone(timedelta(hours=8))

SYSTEM_PROMPT = (
    "你是严谨的数学验证员，负责对中小学数学题目做独立推导验证。\n"
    "要求：\n"
    "1. 仅依据“题目”与“选项”独立推导答案；输入中不会出现参考答案，也不要把任何文本当作参考答案使用。\n"
    "2. 若题干条件不完整、存在歧义、需要图形信息（题干出现“图”等字样）、或无法唯一确定答案，status 必须为 \"unsupported\"。\n"
    "3. 选择题请逐项判定后给出正确选项的字母（A/B/C/D…）。\n"
    "4. 填空题/计算题请给出最终答案（数值、表达式或简要结论）。\n"
    "5. 只输出一个 JSON 对象，不要输出 JSON 以外的任何文字，不要使用代码块标记。\n"
    "JSON 格式：\n"
    "{\"status\": \"pass\" 或 \"fail\" 或 \"unsupported\", "
    "\"computed_answer\": \"推导出的最终答案\", "
    "\"confidence\": 0~1 之间的数字, "
    "\"reasoning_text\": \"简明推理摘要\", "
    "\"answer_derivation\": \"分步推导过程\", "
    "\"warnings\": [\"如条件缺失等\"]}\n"
)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

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


def db_connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_credential(path: Path) -> tuple[str, str]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    url = str(data.get("url") or "").strip()
    key = str(data.get("key") or "").strip()
    if not url or not key:
        raise RuntimeError("math-api.txt missing url/key")
    return url, key


# ---------------------------------------------------------------------------
# Database feature extraction (read-only)
# ---------------------------------------------------------------------------

def resolve_answer(conn: sqlite3.Connection, qid: str) -> str | None:
    row = conn.execute(
        "SELECT internal_payload FROM question_internal_evidence "
        "WHERE question_id=? AND field_name='answer' AND internal_payload IS NOT NULL "
        "AND trim(internal_payload)<>'' ORDER BY created_at DESC, id DESC LIMIT 1",
        (qid,),
    ).fetchone()
    if row is not None and row["internal_payload"]:
        return row["internal_payload"]
    row = conn.execute(
        "SELECT source_answer FROM content_item_math_validation_evidence "
        "WHERE question_id=? AND source_answer IS NOT NULL AND trim(source_answer)<>'' "
        "ORDER BY created_at DESC, id DESC LIMIT 1",
        (qid,),
    ).fetchone()
    if row is not None and row["source_answer"]:
        return row["source_answer"]
    row = conn.execute("SELECT answer FROM questions WHERE id=?", (qid,)).fetchone()
    return row["answer"] if row is not None else None


def resolve_analysis(conn: sqlite3.Connection, qid: str) -> str | None:
    row = conn.execute(
        "SELECT internal_payload FROM question_internal_evidence "
        "WHERE question_id=? AND field_name='analysis' AND internal_payload IS NOT NULL "
        "AND trim(internal_payload)<>'' ORDER BY created_at DESC, id DESC LIMIT 1",
        (qid,),
    ).fetchone()
    return row["internal_payload"] if row is not None else None


def parse_options(options_json: str | None) -> tuple[list[dict], bool]:
    try:
        data = json.loads(options_json or "[]")
    except Exception:
        return [], True
    if not isinstance(data, list):
        return [], True
    out: list[dict] = []
    asset_only = False
    for item in data:
        if not isinstance(item, dict):
            continue
        label = str(item.get("label") or "").strip()
        text = str(item.get("text") or "").strip()
        is_asset = bool(item.get("asset_only")) or "\x01" in text
        if is_asset:
            asset_only = True
        if label or text:
            out.append({"label": label, "text": text, "asset_only": is_asset})
    return out, asset_only


def clean_answer(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = "".join(text.split())
    return text.rstrip(";;\uff1b.\u3002,\uff0c\u3001")


def classify_answer_form(clean: str, question_type: str) -> str:
    if not clean:
        return "missing"
    if question_type == "\u9009\u62e9\u9898" and re.fullmatch(r"[A-F]", clean):
        return "choice_letter"
    if re.fullmatch(r"[A-Z]{2,5}", clean):
        return "letter_abbrev"
    if re.fullmatch(r"[+-]?\d+(?:\.\d+)?", clean):
        return "pure_number"
    if re.fullmatch(r"[+-]?\d+/\d+", clean):
        return "fraction"
    if re.fullmatch(
        r"[+-]?\d+(?:\.\d+)?(?:cm|mm|m|km|\u00b0|\u5ea6|\u5143|\u4e2a|\u540d|\u65f6|\u5206|\u79d2|g|kg|\u5428|\u5347|\u6beb\u5347|%|\uff05)",
        clean,
    ):
        return "number_with_unit"
    cjk_count = len(re.findall(r"[\u4e00-\u9fff]", clean))
    if len(clean) >= 24 or (len(clean) >= 14 and cjk_count >= 12):
        return "long_solution"
    if re.search(
        r"[\u6216\u548c/\uff1d=<>,,\u3001;;\uff1b]|\u00b0|\(|\[|x|y|a|b|c|n|m|\u2212|\u221a|\u00b1",
        clean,
    ):
        return "expression_multi"
    return "short_text"


SHORT_FORMS = frozenset(
    {"choice_letter", "letter_abbrev", "pure_number", "fraction", "number_with_unit", "short_text"}
)


def pilot_stratum(form: str) -> str:
    if form in SHORT_FORMS:
        return "short"
    if form == "expression_multi":
        return "expr"
    if form == "long_solution":
        return "long"
    return "other"


def normalize_for_match(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = "".join(text.split())
    return text.rstrip(";;\uff1b.\u3002,\uff0c\u3001")


def answers_match(computed: str | None, stored: str | None) -> bool:
    left = normalize_for_match(computed)
    right = normalize_for_match(stored)
    if left == right:
        return True
    if not left or not right:
        return False
    left_parts = [p for p in re.split(r"\u6216|\uff1b|;|,|\uff0c|\u3001", left) if p]
    right_parts = [p for p in re.split(r"\u6216|\uff1b|;|,|\uff0c|\u3001", right) if p]
    return (
        len(left_parts) > 1
        and len(left_parts) == len(right_parts)
        and set(left_parts) == set(right_parts)
    )


def stage_a_reason(question: dict) -> str:
    from independent_math_validators import validate

    result = validate(question)
    if result.status == "unsupported":
        return result.evidence
    return f"deterministic:{result.status}"


def build_pool(conn: sqlite3.Connection) -> list[dict]:
    content_item_by_qid = {
        r["question_id"]: r["content_item_id"]
        for r in conn.execute(
            "SELECT question_id, content_item_id FROM content_item_question_links"
        )
    }
    status_by_qid: dict[str, str] = {}
    for r in conn.execute(
        "SELECT e.question_id, e.validation_status FROM content_item_math_validation_evidence e "
        "WHERE e.id = (SELECT e2.id FROM content_item_math_validation_evidence e2 "
        "WHERE e2.question_id = e.question_id ORDER BY e2.created_at DESC, e2.id DESC LIMIT 1)"
    ):
        status_by_qid[r["question_id"]] = r["validation_status"]

    rows = conn.execute(
        "SELECT q.id, q.stem, q.options_json, q.question_type, q.source_question_no, "
        "q.source_document_id, q.content_hash FROM questions q "
        "JOIN content_item_question_links l ON l.question_id = q.id"
    ).fetchall()

    pool: list[dict] = []
    stats = collections.Counter()
    for row in rows:
        qid = row["id"]
        stem = row["stem"] or ""
        if "\u56fe" in stem:
            stats["excluded_figure_hint"] += 1
            continue
        options, asset_only = parse_options(row["options_json"])
        if asset_only:
            stats["excluded_asset_only_options"] += 1
            continue
        answer = resolve_answer(conn, qid)
        analysis = resolve_analysis(conn, qid)
        has_any_answer = bool(answer and answer.strip())
        has_analysis = bool(analysis and analysis.strip())
        if not (has_any_answer or has_analysis):
            stats["excluded_no_answer_no_analysis"] += 1
            continue
        if status_by_qid.get(qid) == "pass":
            stats["excluded_already_pass"] += 1
            continue
        clean = clean_answer(answer)
        form = classify_answer_form(clean, row["question_type"])
        stratum = pilot_stratum(form)
        if stratum == "other":
            stats["excluded_other_form"] += 1
            continue
        reason = stage_a_reason(
            {
                "source_question_no": row["source_question_no"],
                "stem": stem,
                "options": [{"label": o["label"], "text": o["text"]} for o in options],
                "question_type": row["question_type"],
                "answer": answer,
            }
        )
        pool.append(
            {
                "question_id": qid,
                "content_item_id": content_item_by_qid.get(qid),
                "stem": stem,
                "options": options,
                "question_type": row["question_type"],
                "source_document_id": row["source_document_id"],
                "content_hash": row["content_hash"],
                "source_question_no": row["source_question_no"],
                "answer": answer,
                "analysis": analysis,
                "answer_clean": clean,
                "answer_form": form,
                "stratum": stratum,
                "stage_a_reason": reason,
            }
        )
        stats["pool"] += 1
        stats[f"stratum_{stratum}"] += 1
    return pool, stats


def allocate_proportional(target: int, group_sizes: dict[str, int]) -> dict[str, int]:
    total = sum(group_sizes.values())
    if total <= 0:
        return {k: 0 for k in group_sizes}
    alloc = {k: target * n / total for k, n in group_sizes.items()}
    result = {k: int(v) for k, v in alloc.items()}
    remaining = target - sum(result.values())
    order = sorted(group_sizes, key=lambda k: (-(alloc[k] - result[k]), k))
    for _ in range(2):
        for k in order:
            if remaining <= 0:
                break
            cap = group_sizes[k] - result[k]
            if cap <= 0:
                continue
            add = min(remaining, cap)
            result[k] += add
            remaining -= add
    if remaining > 0:
        for k in order:
            if remaining <= 0:
                break
            cap = group_sizes[k] - result[k]
            add = min(remaining, cap)
            result[k] += add
            remaining -= add
    return result


def sample_pool(pool: list[dict], seed: int) -> list[dict]:
    rng = random.Random(seed)
    counts = {"short": TARGET_SHORT, "expr": TARGET_EXPR, "long": TARGET_LONG}
    by_stratum: dict[str, list[dict]] = collections.defaultdict(list)
    for rec in pool:
        by_stratum[rec["stratum"]].append(rec)
    sampled: list[dict] = []
    for stratum, target in counts.items():
        group = by_stratum.get(stratum, [])
        if not group:
            continue
        by_reason: dict[str, list[dict]] = collections.defaultdict(list)
        for rec in group:
            by_reason[rec["stage_a_reason"]].append(rec)
        group_sizes = {reason: len(recs) for reason, recs in by_reason.items()}
        allocation = allocate_proportional(target, group_sizes)
        taken: list[dict] = []
        for reason, recs in by_reason.items():
            rng.shuffle(recs)
            taken.extend(recs[: allocation.get(reason, 0)])
        rng.shuffle(taken)
        sampled.extend(taken)
    if len(sampled) > TOTAL:
        rng.shuffle(sampled)
        sampled = sampled[:TOTAL]
    return sampled


# ---------------------------------------------------------------------------
# LLM validation
# ---------------------------------------------------------------------------

def build_user_message(rec: dict) -> str:
    lines = ["\u9898\u76ee\uff1a", rec["stem"]]
    if rec["options"]:
        lines.append("")
        lines.append("\u9009\u9879\uff1a")
        for opt in rec["options"]:
            lines.append(f"{opt['label']}. {opt['text']}")
    return "\n".join(lines)


def parse_llm_json(content: str) -> dict | None:
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        data = json.loads(candidate)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def llm_validate(
    base_url: str,
    key: str,
    rec: dict,
    model: str,
    session: "requests.Session",
) -> tuple[dict | None, str, int, int]:
    """Call the OpenAI-compatible chat endpoint with a plain requests client.

    The channel gateway blocks the openai SDK's default request headers
    (x-stainless-* / OpenAI/Python User-Agent), so the pilot talks to the
    endpoint directly. The API key is sent only in the Authorization header.
    """
    import requests

    url = base_url.rstrip("/") + "/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "User-Agent": "math-validation-pilot/1.0",
    }
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_message(rec)},
        ],
        "temperature": 0,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "stream": False,
    }
    resp = session.post(url, headers=headers, json=payload, timeout=240)
    if resp.status_code != 200:
        detail = resp.text[:200]
        raise RuntimeError(f"HTTP {resp.status_code}: {detail}")
    data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("no choices in response")
    content = choices[0].get("message", {}).get("content") or ""
    usage = data.get("usage") or {}
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    parsed = parse_llm_json(content)
    return parsed, content, prompt_tokens, completion_tokens

def finalize_result(rec: dict, parsed: dict | None) -> dict:
    if parsed is None:
        return {"status": "unsupported", "reason": "unparsable_json", "computed": None}
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
    if answers_match(computed, stored):
        return {"status": "pass", "reason": None, "computed": computed, "confidence": confidence}
    return {"status": "fail", "reason": "answer_mismatch", "computed": computed, "confidence": confidence}


def build_record(
    rec: dict,
    result: dict,
    parsed: dict | None,
    model: str,
    prompt_version: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> dict:
    content_payload = {
        "question_id": rec["question_id"],
        "stem": rec["stem"],
        "options": rec["options"],
    }
    input_payload = {
        "prompt_version": prompt_version,
        "model": model,
        "question_id": rec["question_id"],
        "system_prompt": SYSTEM_PROMPT,
        "user_message": build_user_message(rec),
    }
    evidence = {
        "model": model,
        "prompt_version": prompt_version,
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
        "created_change_event_id": "pilot-run-20260803",
        "created_at": now_iso(),
        "reason": result.get("reason"),
        "answer_form": rec["answer_form"],
        "stratum": rec["stratum"],
        "stage_a_reason": rec["stage_a_reason"],
        "question_type": rec["question_type"],
    }
    record["evidence_sha256"] = stable_hash(record)
    return record


def estimate_cost_cny(prompt_tokens: int, completion_tokens: int) -> float:
    return (prompt_tokens * INPUT_CNY_PER_1M + completion_tokens * OUTPUT_CNY_PER_1M) / 1_000_000.0


# ---------------------------------------------------------------------------
# Review table
# ---------------------------------------------------------------------------

def build_review_table(results: list[dict], seed: int) -> str:
    rng = random.Random(seed)
    chosen = list(results)
    rng.shuffle(chosen)
    chosen = chosen[:10]
    lines = [
        "# MATH-VALIDATION-003 \u2014 \u590d\u6838\u8868\uff0810 \u9053\uff0c\u72ec\u7acb\u590d\u6838\uff09",
        "",
        "| # | \u9898\u53f7 | \u9898\u76ee\u6458\u8981 | \u8ba1\u7b97\u7ed3\u679c | \u6e90\u7b54\u6848 | \u6a21\u578b\u5224\u5b9a | \u7f6e\u4fe1\u5ea6 | \u63a8\u7406\u6458\u8981 | \u72ec\u7acb\u590d\u6838 | \u4e0d\u4e00\u81f4 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for idx, rec in enumerate(chosen, start=1):
        stem = (rec.get("question") or {}).get("stem") or ""
        summary = stem[:80].replace("|", "/").replace("\n", " ")
        if len(stem) > 80:
            summary += "\u2026"
        ev = rec.get("evidence_json") or {}
        reasoning = (ev.get("reasoning_text") or "")[:60].replace("|", "/").replace("\n", " ")
        if len(ev.get("reasoning_text") or "") > 60:
            reasoning += "\u2026"
        qid = rec.get("question_id", "")[-12:]
        lines.append(
            f"| {idx} | `{qid}` | {summary} | {rec.get('computed_answer') or ''} | "
            f"{rec.get('source_answer') or ''} | {rec.get('validation_status')} | "
            f"{ev.get('confidence') or ''} | {reasoning} | \u5f85\u590d\u6838 | |"
        )
    lines.append("")
    lines.append("\u590d\u6838\u8bf4\u660e\uff1a\u72ec\u7acb\u590d\u6838\u7531\u6267\u884c\u4f1a\u8bdd\u5bf9\u7167\u9898\u76ee/\u6e90\u7b54\u6848\u4e0e\u6a21\u578b\u8ba1\u7b97\u7ed3\u679c\u5224\u5b9a\uff1b\u4e0d\u4e00\u81f4\u9879\u987b\u6807\u8bb0\u5e76\u5206\u6790\u539f\u56e0\u3002")
    return "\n".join(lines)
# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def load_done_ids(results_path: Path) -> set[str]:
    if not results_path.exists():
        return set()
    done: set[str] = set()
    with open(results_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except Exception:
                continue
            qid = data.get("question_id")
            if qid:
                done.add(qid)
    return done


def write_record(results_path: Path, record: dict) -> None:
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with open(results_path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def write_sample_list(sample_path: Path, sample: list[dict]) -> None:
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sample_path, "w", encoding="utf-8") as fh:
        for rec in sample:
            slim = {
                "question_id": rec["question_id"],
                "content_item_id": rec["content_item_id"],
                "question_type": rec["question_type"],
                "source_document_id": rec["source_document_id"],
                "source_question_no": rec["source_question_no"],
                "answer_form": rec["answer_form"],
                "stratum": rec["stratum"],
                "stage_a_reason": rec["stage_a_reason"],
                "stem": rec["stem"],
                "options": rec["options"],
                "answer": rec["answer"],
                "analysis": rec["analysis"],
            }
            fh.write(json.dumps(slim, ensure_ascii=False) + "\n")


def summarize(results: list[dict], completed: int) -> str:
    statuses = collections.Counter(r["validation_status"] for r in results)
    by_stratum = collections.defaultdict(collections.Counter)
    for r in results:
        by_stratum[r["stratum"]][r["validation_status"]] += 1
    prompt_tokens = sum(
        r["evidence_json"]["tokens_used"]["prompt_tokens"] for r in results
    )
    completion_tokens = sum(
        r["evidence_json"]["tokens_used"]["completion_tokens"] for r in results
    )
    cost_cny = sum(
        estimate_cost_cny(
            r["evidence_json"]["tokens_used"]["prompt_tokens"],
            r["evidence_json"]["tokens_used"]["completion_tokens"],
        )
        for r in results
    )
    out = [
        f"COMPLETED={completed}",
        f"STATUSES={dict(statuses)}",
        f"BY_STRATUM={ {k: dict(v) for k, v in sorted(by_stratum.items())} }",
        f"TOKENS prompt={prompt_tokens} completion={completion_tokens} total={prompt_tokens + completion_tokens}",
        f"COST_EST_CNY={cost_cny:.4f} USD~{cost_cny / CNY_PER_USD:.4f}",
        f"MODEL={MODEL} PROMPT_VERSION={PROMPT_VERSION}",
    ]
    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage B LLM validation pilot")
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--cred", type=Path, default=CRED_PATH)
    parser.add_argument("--results", type=Path, default=RESULTS_PATH)
    parser.add_argument("--sample", type=Path, default=SAMPLE_PATH)
    parser.add_argument("--review", type=Path, default=REVIEW_PATH)
    parser.add_argument("--sample-only", action="store_true", help="build and dump the sample list only")
    parser.add_argument("--resume", action="store_true", help="skip question_ids already present in results file")
    parser.add_argument("--batch-offset", type=int, default=0, help="start at this batch index (0-based)")
    parser.add_argument("--max-batches", type=int, default=0, help="max batches to process (0 = all)")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--seed", type=int, default=SEED)
    args = parser.parse_args()

    if not args.db.exists():
        print("FATAL db not found", args.db, file=sys.stderr)
        return 2
    db_sha_before = sha256_file(args.db)

    conn = db_connect_ro(args.db)
    try:
        pool, stats = build_pool(conn)
    finally:
        conn.close()

    print("POOL_STATS", json.dumps(stats, ensure_ascii=False))
    sample = sample_pool(pool, args.seed)
    strata = collections.Counter(rec["stratum"] for rec in sample)
    print("SAMPLE_STRATA", dict(strata))
    print("SAMPLE_TOTAL", len(sample))
    write_sample_list(args.sample, sample)

    if args.sample_only:
        print("SAMPLE_ONLY done:", args.sample)
        return 0

    url, key = load_credential(args.cred)
    base = url.rstrip("/")

    import requests

    session = requests.Session()

    done = load_done_ids(args.results) if args.resume else set()
    results: list[dict] = []
    if args.resume and args.results.exists():
        with open(args.results, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    results.append(json.loads(line))

    to_run = [rec for rec in sample if rec["question_id"] not in done]
    if args.batch_offset:
        to_run = to_run[args.batch_offset * BATCH_SIZE :]
    if args.max_batches:
        to_run = to_run[: args.max_batches * BATCH_SIZE]

    total_prompt = sum(r["evidence_json"]["tokens_used"]["prompt_tokens"] for r in results)
    total_completion = sum(r["evidence_json"]["tokens_used"]["completion_tokens"] for r in results)
    total_cost_cny = sum(
        estimate_cost_cny(
            r["evidence_json"]["tokens_used"]["prompt_tokens"],
            r["evidence_json"]["tokens_used"]["completion_tokens"],
        )
        for r in results
    )

    print("DB_SHA256_BEFORE", db_sha_before)
    print("TO_RUN", len(to_run), "ALREADY_DONE", len(done))
    print("MODEL", args.model, "PROMPT_VERSION", PROMPT_VERSION)
    print("COST_CAP_CNY", COST_CAP_CNY, "COST_BASIS", f"in={INPUT_CNY_PER_1M}/1M out={OUTPUT_CNY_PER_1M}/1M")

    stop_reason = None
    for batch_idx in range(0, len(to_run), BATCH_SIZE):
        batch = to_run[batch_idx : batch_idx + BATCH_SIZE]
        print(f"BATCH start idx={batch_idx} n={len(batch)} cumulative_cost_est_cny={total_cost_cny:.4f}", flush=True)
        for rec in batch:
            if total_cost_cny > COST_CAP_CNY:
                stop_reason = f"cost_cap_exceeded after batch {batch_idx}"
                break
            parsed = None
            content = ""
            prompt_tokens = completion_tokens = 0
            last_error: Exception | None = None
            for attempt in range(3):
                try:
                    parsed, content, prompt_tokens, completion_tokens = llm_validate(base, key, rec, args.model, session)
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    print(f"  RETRY q={rec['question_id'][-12:]} attempt={attempt} err={type(exc).__name__}: {str(exc)[:160]}", flush=True)
                    time.sleep(2 * (attempt + 1))
            if last_error is not None:
                stop_reason = f"api_error:{type(last_error).__name__}:{str(last_error)[:120]}"
                print("STOP_API_ERROR", stop_reason, flush=True)
                break
            result = finalize_result(rec, parsed)
            record = build_record(
                rec, result, parsed, args.model, PROMPT_VERSION, prompt_tokens, completion_tokens
            )
            results.append(record)
            write_record(args.results, record)
            total_prompt += prompt_tokens
            total_completion += completion_tokens
            total_cost_cny += estimate_cost_cny(prompt_tokens, completion_tokens)
            print(
                f"  OK q={rec['question_id'][-12:]} status={result['status']} "
                f"tokens={prompt_tokens + completion_tokens} cost_est_cny={total_cost_cny:.4f}",
                flush=True,
            )
        if stop_reason:
            break

    print(summarize(results, len(results)))
    if results:
        review_md = build_review_table(results, args.seed)
        args.review.parent.mkdir(parents=True, exist_ok=True)
        args.review.write_text(review_md, encoding="utf-8")
        print("REVIEW_TABLE", args.review)
    print("STOP_REASON", stop_reason)
    print("DB_SHA256_AFTER", sha256_file(args.db))
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

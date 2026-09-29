"""MATH-VALIDATION-004: multi-model comparison + batching feasibility pilot.

Scope (per work order docs/work_orders/MATH-VALIDATION-004.md):
- New standalone script only. Does NOT modify existing code/schema/config.
- Does NOT write the real database (read-only SQLite URI + PRAGMA query_only).
- Does NOT read question source files.
- Does NOT print, log, or commit API credentials (math-api.txt is git-ignored).
- Hard cost cap: CNY 30.0. Accumulated by call count for per-call models
  (deepseek-v4-pro-thinking, CNY 0.54/call) and by tokens for per-token models
  (per the relay price list recorded in the work order).

Reuses read-only helpers from run_math_validation_pilot.py (import-only; that
file is NOT modified).

Outputs (UTF-8) under docs/validation_pilot/:
  MATH-VALIDATION-004_sample.jsonl          20-question sampling list
  MATH-VALIDATION-004_single_<model>.jsonl  per-model single-question evidence
  MATH-VALIDATION-004_batch_5.jsonl         batching experiment (5 questions/call)
  MATH-VALIDATION-004_batch_10.jsonl        batching experiment (10 questions/call)
  MATH-VALIDATION-004_summary.jsonl         one-line machine-readable summary

Usage:
  python run_math_model_comparison_pilot.py --sample-only
  python run_math_model_comparison_pilot.py --models agnes-1.5-flash --skip-batch
  python run_math_model_comparison_pilot.py --skip-single
  python run_math_model_comparison_pilot.py --summary-only
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import run_math_validation_pilot as P

ROOT = Path(__file__).resolve().parent
PILOT_DIR = P.PILOT_DIR
DB_PATH = P.DB_PATH
CRED_PATH = P.CRED_PATH

SEED = 20260803
NEW_TARGETS = {"short": 7, "expr": 2, "long": 1}
# Full question ids of the 10 MATH-VALIDATION-003 human-reviewed questions,
# mapped to the expected status derived from the independent review table
# (MATH-VALIDATION-003_review.md). 2C7AB86EE2C2 is expected "pass": the human
# reviewer judged it solvable (answer C); 003's unsupported was a JSON-format
# failure, not a math judgment, hence "disagree".
REVIEW_EXPECTED = {
    "source-question:5487B9BB9E82E13C85E29E18F5EDFF90242D4FF04E97AC3482AD238F884D01FC": "unsupported",
    "source-question:A5052FA2978202B2CC39114F5F41648C78D41C5607BD29EC33CF17CF29C5E1CA": "unsupported",
    "source-question:EC3B17729ECCE19C9F2C80D793C9C67C96D83537C01C3EE6DB37BB86F50E5F2B": "unsupported",
    "source-question:AFA983137C6403AD85470FC0BF02F55C189C8B76E807BC466E9B2D995D1371F0": "unsupported",
    "source-question:2FA0309418367188F2189B24C97848F3B48FEB79B0C7416539F2B8946244DCB1": "pass",
    "source-question:B70BC1BBB62DFDAA4394CF09CDF5636931F9B1671E8D560F4E952C7AB86EE2C2": "pass",
    "source-question:277B23B51BA655BC21D1A88D41034138D5C21027E73F9ABAA4A957F3BE3E7831": "unsupported",
    "source-question:30B6C71B75D977C11A536D66FE5D07A01E370A12F771CEEF9AEA47705C0338FC": "unsupported",
    "source-question:D364977E6B8C14C326BEA2635ABD7A45C498447C4C9C82BC88E309ACACC5E1FA": "unsupported",
    "source-question:079167B2F51BCD3D0F1A614D29058C9C4DF32D692F14D476AF9496874C0D52D0": "pass",
}
MODELS_SINGLE = [
    "agnes-1.5-flash",
    "deepseek-v4-flash",
    "claude-haiku-4-5",
    "gpt-5.4-mini",
    "deepseek-v4-pro-thinking",
]
BATCH_MODEL = "deepseek-v4-pro-thinking"
BATCH_SIZES = [5, 10]
COST_CAP_CNY = 30.0
PER_CALL_CNY = {"deepseek-v4-pro-thinking": 0.54}
PER_TOKEN_CNY = {
    "agnes-1.5-flash": (0.54, 0.54),
    "deepseek-v4-flash": (9.0, 9.0),
    "claude-haiku-4-5": (3.6, 18.0),
    "gpt-5.4-mini": (5.4, 32.4),
}
PROMPT_VERSION = P.PROMPT_VERSION
BATCH_PROMPT_VERSION = "llm-math-proof-v1-batch-20260803"
EVENT_ID = "pilot-run-20260803-004"
CHINA_TZ = timezone(timedelta(hours=8))
MAX_OUTPUT_TOKENS_SINGLE = 2000
MAX_OUTPUT_TOKENS_BATCH = 8000

BATCH_SYSTEM_PROMPT = (
    "你是严谨的数学验证员，负责对一批中小学数学题目做独立推导验证。\n"
    "要求：\n"
    "1. 仅依据每题的“题目”与“选项”独立推导答案；输入中不会出现参考答案，也不要把任何文本当作参考答案使用。\n"
    "2. 若某题题干条件不完整、存在歧义、需要图形信息（题干出现“图”等字样）、或无法唯一确定答案，该题 status 必须为 \"unsupported\"。\n"
    "3. 选择题请逐项判定后给出正确选项的字母（A/B/C/D…）。\n"
    "4. 填空题/计算题请给出最终答案（数值、表达式或简要结论）。\n"
    "5. 只输出一个 JSON 对象，不要输出 JSON 以外的任何文字，不要使用代码块标记。\n"
    "JSON 格式：\n"
    "{\"results\": [{\"question_id\": \"输入的完整 question_id\", "
    "\"status\": \"pass\" 或 \"fail\" 或 \"unsupported\", "
    "\"computed_answer\": \"推导出的最终答案\", "
    "\"confidence\": 0~1 之间的数字, "
    "\"reasoning_text\": \"简明推理摘要\"}]}\n"
    "必须为输入列表中的每一道题都输出一条 results 记录，且 question_id 与输入完全一致，不要遗漏任何一题。"
)


def now_iso() -> str:
    return datetime.now(CHINA_TZ).isoformat(timespec="seconds")


def load_lines(path) -> list:
    if not Path(path).exists():
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


def cost_of_call(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    if model in PER_CALL_CNY:
        return PER_CALL_CNY[model]
    rates = PER_TOKEN_CNY.get(model)
    if rates is None:
        raise RuntimeError(f"no cost basis for model {model}")
    return (prompt_tokens * rates[0] + completion_tokens * rates[1]) / 1_000_000.0


def cost_basis(model: str) -> str:
    return "per_call" if model in PER_CALL_CNY else "per_token"


def sample_new(pool, seed: int, targets: dict):
    rng = random.Random(seed)
    by_stratum = collections.defaultdict(list)
    for rec in pool:
        by_stratum[rec["stratum"]].append(rec)
    sampled = []
    for stratum, target in targets.items():
        group = by_stratum.get(stratum, [])
        if not group:
            continue
        by_reason = collections.defaultdict(list)
        for rec in group:
            by_reason[rec["stage_a_reason"]].append(rec)
        group_sizes = {reason: len(recs) for reason, recs in by_reason.items()}
        allocation = P.allocate_proportional(target, group_sizes)
        taken = []
        for reason, recs in by_reason.items():
            rng.shuffle(recs)
            taken.extend(recs[: allocation.get(reason, 0)])
        rng.shuffle(taken)
        sampled.extend(taken)
    return sampled


def build_sample(conn):
    pool, stats = P.build_pool(conn)
    sample003_ids = set()
    if P.SAMPLE_PATH.exists():
        for rec in load_lines(P.SAMPLE_PATH):
            sample003_ids.add(rec["question_id"])
    by_id = {r["question_id"]: r for r in pool}
    reviewed = []
    for qid, expected in REVIEW_EXPECTED.items():
        if qid not in by_id:
            raise RuntimeError(f"reviewed question not in pool: {qid}")
        rec = dict(by_id[qid])
        rec["sample_source"] = "003-review"
        rec["review_expected_status"] = expected
        reviewed.append(rec)
    fresh_pool = [r for r in pool if r["question_id"] not in sample003_ids]
    new_recs = sample_new(fresh_pool, SEED, NEW_TARGETS)
    if len(new_recs) != sum(NEW_TARGETS.values()):
        raise RuntimeError(f"new sample size mismatch: {len(new_recs)}")
    for rec in new_recs:
        rec["sample_source"] = "new-20260803"
        rec["review_expected_status"] = None
    return reviewed + new_recs, stats


def write_sample(path: Path, sample: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
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
                "sample_source": rec["sample_source"],
                "review_expected_status": rec["review_expected_status"],
            }
            fh.write(json.dumps(slim, ensure_ascii=False) + "\n")


def build_single_record(rec, parsed, result, model, prompt_tokens, completion_tokens):
    record = P.build_record(
        rec, result, parsed, model, PROMPT_VERSION, prompt_tokens, completion_tokens
    )
    record["created_change_event_id"] = EVENT_ID
    record["pilot"] = "MATH-VALIDATION-004"
    return record


def llm_single_call(base_url, key, session, model, rec, max_tokens=MAX_OUTPUT_TOKENS_SINGLE):
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
            {"role": "system", "content": P.SYSTEM_PROMPT},
            {"role": "user", "content": P.build_user_message(rec)},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
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
    parsed = P.parse_llm_json(content)
    return parsed, content, pt, ct


def build_batch_user_message(recs):
    lines = [f"请验证以下 {len(recs)} 道题，逐题独立推导：", ""]
    for i, rec in enumerate(recs, start=1):
        lines.append(f"题目{i}（question_id: {rec['question_id']}）：")
        lines.append(rec["stem"])
        if rec["options"]:
            lines.append("选项：")
            for opt in rec["options"]:
                lines.append(f"{opt['label']}. {opt['text']}")
        lines.append("")
    return "\n".join(lines)


def parse_batch_json(content, expected_ids):
    if not content:
        return None
    parsed = P.parse_llm_json(content)
    if not parsed:
        return None
    results = parsed.get("results")
    if not isinstance(results, list):
        return None
    valid_statuses = {"pass", "fail", "unsupported"}
    by_id = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        qid = str(item.get("question_id") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        if status not in valid_statuses:
            return None
        if qid in expected_ids:
            by_id[qid] = item
        else:
            for exp in expected_ids:
                if qid == exp[-12:] or qid.endswith(exp[-12:]):
                    by_id[exp] = item
                    break
    if set(expected_ids) <= set(by_id):
        return {qid: by_id[qid] for qid in expected_ids}
    return None


def llm_batch_call(base_url, key, session, model, recs, max_tokens=MAX_OUTPUT_TOKENS_BATCH):
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
            {"role": "system", "content": BATCH_SYSTEM_PROMPT},
            {"role": "user", "content": build_batch_user_message(recs)},
        ],
        "temperature": 0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    resp = session.post(url, headers=headers, json=payload, timeout=600)
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
    expected_ids = [r["question_id"] for r in recs]
    parsed_items = parse_batch_json(content, expected_ids)
    return parsed_items, content, pt, ct


def is_auth_error(exc) -> bool:
    text = str(exc)
    return ("401" in text) or ("403" in text) or ("authentication" in text.lower()) or ("unauthorized" in text.lower())


def is_unavailable_error(exc) -> bool:
    text = str(exc).lower()
    if "no available channel" in text:
        return True
    if "model not found" in text or "model does not exist" in text:
        return True
    if "invalid model" in text:
        return True
    return False


def run_single_models(base_url, key, session, sample, models, resume, results_dir, cost_state):
    for model in models:
        path = results_dir / f"MATH-VALIDATION-004_single_{model}.jsonl"
        done = set()
        if resume and path.exists():
            for rec in load_lines(path):
                done.add(rec["question_id"])
        to_run = [r for r in sample if r["question_id"] not in done]
        print(f"SINGLE model={model} total={len(sample)} done={len(done)} to_run={len(to_run)}", flush=True)
        model_skipped = False
        for start in range(0, len(to_run), 5):
            chunk = to_run[start : start + 5]
            for rec in chunk:
                if cost_state["total_cny"] > COST_CAP_CNY:
                    cost_state["stopped"] = f"cost_cap_exceeded at model={model}"
                    print("STOP_COST_CAP", flush=True)
                    return
                parsed = None
                content = ""
                pt = ct = 0
                last_err = None
                for attempt in range(3):
                    try:
                        max_tokens = MAX_OUTPUT_TOKENS_SINGLE if attempt == 0 else 1024
                        parsed, content, pt, ct = llm_single_call(base_url, key, session, model, rec, max_tokens)
                        last_err = None
                        break
                    except Exception as exc:
                        last_err = exc
                        print(
                            f"  RETRY q={rec['question_id'][-12:]} attempt={attempt} "
                            f"err={type(exc).__name__}: {str(exc)[:140]}",
                            flush=True,
                        )
                        time.sleep(2 * (attempt + 1))
                if last_err is not None:
                    if is_auth_error(last_err) or is_unavailable_error(last_err):
                        cost_state["skipped_models"].append(f"{model} ({str(last_err)[:100]})")
                        print(f"MODEL_SKIPPED model={model} err={str(last_err)[:140]}", flush=True)
                        model_skipped = True
                        break
                    result = {"status": "unsupported", "reason": "api_error", "computed": None, "confidence": None}
                    record = build_single_record(rec, None, result, model, 0, 0)
                    with open(path, "a", encoding="utf-8") as fh:
                        fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                    print(f"  ERR q={rec['question_id'][-12:]} status=unsupported reason=api_error", flush=True)
                    continue
                result = P.finalize_result(rec, parsed)
                record = build_single_record(rec, parsed, result, model, pt, ct)
                with open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, ensure_ascii=False) + "\n")
                cost = cost_of_call(model, pt, ct)
                entry = cost_state["by_model"][model]
                entry["calls"] += 1
                entry["cost_cny"] += cost
                entry["tokens"]["prompt"] += pt
                entry["tokens"]["completion"] += ct
                cost_state["total_cny"] += cost
                print(
                    f"  OK q={rec['question_id'][-12:]} status={result['status']} "
                    f"tokens={pt + ct} cum_cost_cny={cost_state['total_cny']:.4f}",
                    flush=True,
                )
            if model_skipped:
                break


def run_batch(base_url, key, session, sample, batch_size, resume, results_dir, cost_state):
    path = results_dir / f"MATH-VALIDATION-004_batch_{batch_size}.jsonl"
    done_sets = set()
    if resume and path.exists():
        for call in load_lines(path):
            ids = tuple(call.get("question_ids") or [])
            if ids:
                done_sets.add(frozenset(ids))
    chunks = [sample[i : i + batch_size] for i in range(0, len(sample), batch_size)]
    entry = cost_state["batch"][str(batch_size)]
    for ci, chunk in enumerate(chunks):
        id_set = frozenset(r["question_id"] for r in chunk)
        if id_set in done_sets:
            print(f"BATCH size={batch_size} call={ci} already done", flush=True)
            continue
        if cost_state["total_cny"] > COST_CAP_CNY:
            cost_state["stopped"] = f"cost_cap_exceeded at batch_size={batch_size}"
            print("STOP_COST_CAP", flush=True)
            return
        parsed_items = None
        content = ""
        pt = ct = 0
        last_err = None
        for attempt in range(2):
            try:
                max_tokens = MAX_OUTPUT_TOKENS_BATCH if attempt == 0 else 4000
                parsed_items, content, pt, ct = llm_batch_call(base_url, key, session, BATCH_MODEL, chunk, max_tokens)
                last_err = None
                break
            except Exception as exc:
                last_err = exc
                print(
                    f"  BATCH_RETRY size={batch_size} call={ci} attempt={attempt} "
                    f"err={type(exc).__name__}: {str(exc)[:140]}",
                    flush=True,
                )
                time.sleep(3)
        if last_err is not None and (is_auth_error(last_err) or is_unavailable_error(last_err)):
            cost_state["skipped_models"].append(f"{BATCH_MODEL}_batch{batch_size} ({str(last_err)[:100]})")
            print(f"BATCH_MODEL_SKIPPED size={batch_size} err={str(last_err)[:140]}", flush=True)
            return
        format_ok = parsed_items is not None
        per_question = {}
        for rec in chunk:
            qid = rec["question_id"]
            if format_ok and qid in parsed_items:
                item = parsed_items[qid]
                result = P.finalize_result(rec, item)
                per_question[qid] = {
                    "status": result["status"],
                    "reason": result.get("reason"),
                    "computed_answer": result.get("computed"),
                    "confidence": result.get("confidence"),
                }
            else:
                per_question[qid] = {
                    "status": "unsupported",
                    "reason": "batch_format_failed" if not format_ok else "batch_item_missing",
                    "computed_answer": None,
                    "confidence": None,
                }
        cost = cost_of_call(BATCH_MODEL, pt, ct) if last_err is None else 0.0
        call_rec = {
            "run": "MATH-VALIDATION-004",
            "experiment": "batch",
            "batch_size": batch_size,
            "call_index": ci,
            "model": BATCH_MODEL,
            "prompt_version": BATCH_PROMPT_VERSION,
            "format_ok": format_ok,
            "question_ids": [r["question_id"] for r in chunk],
            "per_question": per_question,
            "tokens_used": {
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "total_tokens": pt + ct,
            },
            "cost_cny": cost,
            "error": str(last_err) if last_err else None,
            "created_at": now_iso(),
        }
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(call_rec, ensure_ascii=False) + "\n")
        entry["calls"] += 1
        entry["cost_cny"] += cost
        entry["tokens"]["prompt"] += pt
        entry["tokens"]["completion"] += ct
        entry["format_ok"] += int(format_ok)
        cost_state["total_cny"] += cost
        print(
            f"  BATCH size={batch_size} call={ci} format_ok={format_ok} "
            f"tokens={pt + ct} cum_cost_cny={cost_state['total_cny']:.4f}",
            flush=True,
        )


def summarize(sample, results_dir, skipped_models, stopped, db_sha_before):
    expected = {
        r["question_id"]: r["review_expected_status"]
        for r in sample
        if r.get("review_expected_status")
    }
    summary = {
        "run": "MATH-VALIDATION-004",
        "seed": SEED,
        "new_targets": NEW_TARGETS,
        "sample_total": len(sample),
        "sample_reviewed": len(expected),
        "prompt_version_single": PROMPT_VERSION,
        "prompt_version_batch": BATCH_PROMPT_VERSION,
        "models": {},
        "batch": {},
        "skipped_models": skipped_models,
        "stopped": stopped,
        "cost_cap_cny": COST_CAP_CNY,
        "total_cost_cny": 0.0,
        "db_sha256_before": db_sha_before,
        "created_at": now_iso(),
    }
    for model in MODELS_SINGLE:
        path = results_dir / f"MATH-VALIDATION-004_single_{model}.jsonl"
        recs = load_lines(path)
        statuses = collections.Counter(r["validation_status"] for r in recs)
        prompt_tokens = sum(r["evidence_json"]["tokens_used"]["prompt_tokens"] for r in recs)
        completion_tokens = sum(r["evidence_json"]["tokens_used"]["completion_tokens"] for r in recs)
        cost = sum(
            cost_of_call(
                model,
                r["evidence_json"]["tokens_used"]["prompt_tokens"],
                r["evidence_json"]["tokens_used"]["completion_tokens"],
            )
            for r in recs
        )
        review = {}
        agree = 0
        for r in recs:
            if r["question_id"] in expected:
                exp = expected[r["question_id"]]
                match = r["validation_status"] == exp
                agree += int(match)
                review[r["question_id"][-12:]] = {
                    "status": r["validation_status"],
                    "expected": exp,
                    "match": match,
                }
        calls_without_usage = sum(
            1
            for r in recs
            if r["evidence_json"]["tokens_used"]["prompt_tokens"]
            + r["evidence_json"]["tokens_used"]["completion_tokens"]
            == 0
        )
        summary["models"][model] = {
            "calls": len(recs),
            "statuses": dict(statuses),
            "review_agreement": f"{agree}/{len(expected)}",
            "review_agreement_ratio": round(agree / len(expected), 4) if expected else None,
            "review": review,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            "calls_without_usage": calls_without_usage,
            "cost_cny": round(cost, 4),
            "cost_basis": cost_basis(model),
            "cost_per_1000_cny": round(cost * 1000.0 / len(recs), 2) if recs else None,
            "if_per_call_cny_20": (
                round(20 * PER_CALL_CNY.get(model, 0.54), 2) if model not in PER_CALL_CNY else None
            ),
        }
        summary["total_cost_cny"] += cost
    single_path = results_dir / "MATH-VALIDATION-004_single_deepseek-v4-pro-thinking.jsonl"
    single_status = {r["question_id"]: r["validation_status"] for r in load_lines(single_path)}
    for bs in BATCH_SIZES:
        path = results_dir / f"MATH-VALIDATION-004_batch_{bs}.jsonl"
        calls = load_lines(path)
        format_ok = sum(1 for c in calls if c.get("format_ok"))
        cost = sum(c.get("cost_cny") or 0 for c in calls)
        prompt_tokens = sum(c["tokens_used"]["prompt_tokens"] for c in calls)
        completion_tokens = sum(c["tokens_used"]["completion_tokens"] for c in calls)
        per_q = {}
        agree = 0
        total = 0
        for c in calls:
            for qid, item in (c.get("per_question") or {}).items():
                st = item.get("status")
                single = single_status.get(qid)
                match = st == single
                agree += int(match)
                total += 1
                per_q[qid[-12:]] = {"batch_status": st, "single_status": single, "match": match}
        summary["batch"][str(bs)] = {
            "calls": len(calls),
            "format_ok": format_ok,
            "format_success_rate": round(format_ok / len(calls), 4) if calls else None,
            "agreement_with_single": f"{agree}/{total}",
            "agreement_ratio": round(agree / total, 4) if total else None,
            "per_question": per_q,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            "cost_cny": round(cost, 4),
            "cost_per_question_cny": round(cost / 20, 4) if calls else None,
            "cost_per_1000_cny": round(cost * 1000.0 / 20, 2) if calls else None,
        }
        summary["total_cost_cny"] += cost
    summary["total_cost_cny"] = round(summary["total_cost_cny"], 4)
    return summary


def print_summary(summary):
    print("=== SUMMARY ===")
    for model, m in summary["models"].items():
        s = m["statuses"]
        print(
            f"{model:24s} pass={s.get('pass', 0):3d} fail={s.get('fail', 0):3d} "
            f"unsupported={s.get('unsupported', 0):3d} review_agree={m['review_agreement']} "
            f"tokens={m['tokens']['total']} cost_cny={m['cost_cny']:.4f} "
            f"basis={m['cost_basis']} per1k_cny={m['cost_per_1000_cny']}"
        )
    for bs, b in summary["batch"].items():
        print(
            f"BATCH size={bs} calls={b['calls']} format_ok={b['format_ok']} "
            f"format_rate={b['format_success_rate']} agree_single={b['agreement_with_single']} "
            f"cost_cny={b['cost_cny']:.4f} per_q_cny={b['cost_per_question_cny']} "
            f"per1k_cny={b['cost_per_1000_cny']}"
        )
    print("TOTAL_COST_CNY", summary["total_cost_cny"], "STOPPED", summary["stopped"])
    print("DB_SHA256_BEFORE", summary["db_sha256_before"])
    print("=== END SUMMARY ===")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample-only", action="store_true", help="write sample list only, no API calls")
    parser.add_argument("--summary-only", action="store_true", help="recompute summary from existing files")
    parser.add_argument("--skip-single", action="store_true", help="skip single-question runs")
    parser.add_argument("--skip-batch", action="store_true", help="skip batching experiments")
    parser.add_argument("--resume", action="store_true", help="resume: skip already-written question ids / call sets")
    parser.add_argument("--models", default=",".join(MODELS_SINGLE), help="comma-separated model list for single runs")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--cred", default=str(CRED_PATH))
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print("FATAL db not found", db_path, file=sys.stderr)
        return 2
    db_sha_before = P.sha256_file(db_path)
    print("DB_SHA256_BEFORE", db_sha_before)

    conn = P.db_connect_ro(db_path)
    try:
        sample, stats = build_sample(conn)
    finally:
        conn.close()
    print("POOL_STATS", json.dumps(stats, ensure_ascii=False))
    print(
        "SAMPLE_TOTAL", len(sample),
        "REVIEWED", sum(1 for r in sample if r.get("review_expected_status")),
        "NEW", sum(1 for r in sample if not r.get("review_expected_status")),
    )
    print("SAMPLE_STRATA", dict(collections.Counter(r["stratum"] for r in sample)))
    sample_path = PILOT_DIR / "MATH-VALIDATION-004_sample.jsonl"
    write_sample(sample_path, sample)
    print("SAMPLE_WRITTEN", sample_path)

    if args.sample_only:
        print("SAMPLE_ONLY done")
        return 0

    if args.summary_only:
        summary = summarize(sample, PILOT_DIR, [], None, db_sha_before)
        print_summary(summary)
        summary_path = PILOT_DIR / "MATH-VALIDATION-004_summary.jsonl"
        with open(summary_path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
        print("DB_SHA256_AFTER", P.sha256_file(db_path))
        print("DONE_SUMMARY_ONLY")
        return 0

    url, key = P.load_credential(Path(args.cred))
    base = url.rstrip("/")

    import requests

    session = requests.Session()
    headers = {"Authorization": f"Bearer {key}", "User-Agent": "math-validation-pilot/1.0"}
    try:
        resp = session.get(base + "/v1/models", headers=headers, timeout=60)
        if resp.status_code != 200:
            print("MODELS_LIST_HTTP", resp.status_code, resp.text[:200], file=sys.stderr)
            return 2
        available = {m.get("id") for m in (resp.json().get("data") or [])}
    except Exception as exc:
        print("MODELS_LIST_ERR", type(exc).__name__, str(exc)[:160], file=sys.stderr)
        return 2
    print("MODELS_LIST_OK count", len(available))

    requested = [m.strip() for m in args.models.split(",") if m.strip()] or MODELS_SINGLE
    skipped = []
    run_models = []
    for m in requested:
        if m in available:
            run_models.append(m)
        else:
            skipped.append(m)
            print("MODEL_UNAVAILABLE", m)
    print("MODELS_TO_RUN", run_models, "SKIPPED", skipped)

    cost_state = {
        "total_cny": 0.0,
        "stopped": None,
        "skipped_models": [],
        "by_model": {
            m: {"calls": 0, "cost_cny": 0.0, "tokens": {"prompt": 0, "completion": 0}}
            for m in run_models
        },
        "batch": {
            str(bs): {"calls": 0, "cost_cny": 0.0, "tokens": {"prompt": 0, "completion": 0}, "format_ok": 0}
            for bs in BATCH_SIZES
        },
    }

    if not args.skip_single and run_models:
        run_single_models(base, key, session, sample, run_models, args.resume, PILOT_DIR, cost_state)
    if not args.skip_batch and BATCH_MODEL in available:
        for bs in BATCH_SIZES:
            run_batch(base, key, session, sample, bs, args.resume, PILOT_DIR, cost_state)
    print("TOTAL_COST_CNY", round(cost_state["total_cny"], 4), "STOPPED", cost_state["stopped"])

    summary = summarize(sample, PILOT_DIR, skipped + cost_state["skipped_models"], cost_state["stopped"], db_sha_before)
    print_summary(summary)
    summary_path = PILOT_DIR / "MATH-VALIDATION-004_summary.jsonl"
    with open(summary_path, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(summary, ensure_ascii=False) + "\n")
    print("SUMMARY_WRITTEN", summary_path)

    db_sha_after = P.sha256_file(db_path)
    print("DB_SHA256_AFTER", db_sha_after)
    print("DB_UNCHANGED", db_sha_before == db_sha_after)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

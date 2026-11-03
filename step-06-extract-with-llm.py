"""Step 06: Extract structured information from consolidated job descriptions.

Reads data/step-05-consolidated.jsonl and uses an LLM to extract strictly
targeted structured data (Title, Tech Stack, Keywords) into a clean JSON schema.
Ensures all extracted text is translated to English.

Outputs data/step-06-extracted.jsonl with a resumable cache at data/step-06-cache.jsonl.

Runs on the Hermes model backend (mimo-v2.6-pro via an OpenAI-compatible
endpoint) — configured through LLM_MODEL / LLM_BASE_URL / LLM_API_KEY env vars
or the Hermes config. See llm.py.
"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from crawlers.base import read_jsonl
from tqdm import tqdm

import llm

CONCURRENCY_LIMIT = 20

# Minimal JSON Schema based on your exact requirements
JSON_SCHEMA = {
    "title_cleaned": "string (The core job role in English, stripped of seniority, location, or department. E.g., if raw title is 'Senior Python Backend Developer (HN/HCM)', extract 'Backend Developer' or 'Python Developer')",
    "tech_stack": [
        "string (ONLY programming languages, major frameworks, and databases explicitly required. E.g., ['Python', 'Django', 'PostgreSQL']. Exclude soft skills and generic tools like Jira)"
    ],
    "keywords": [
        "string (3 to 8 important domain concepts, architectural patterns, cloud platforms, or methodologies in English. E.g., ['AWS', 'Microservices', 'CI/CD', 'Agile', 'Fintech']. Do not duplicate items from tech_stack)"
    ],
}

SYSTEM = (
    "You are an expert IT recruiter AI. Extract structured data from the provided job "
    "title and Markdown description.\n\n"
    "CRITICAL REQUIREMENT: All extracted text MUST be in English. If the original description "
    "or title contains Vietnamese or another language, you must translate the extracted values "
    "into professional English before outputting them.\n\n"
    "You must respond ONLY with a raw, valid JSON object matching this exact schema "
    "and strictly following the formatting constraints provided in the parentheses:\n"
    f"{json.dumps(JSON_SCHEMA, indent=2)}\n\n"
    "Do not include markdown code blocks (like ```json), commentary, or any preamble. "
    "Output raw JSON only. If a field is not mentioned, use null for numbers/strings "
    "or an empty list [] for arrays."
)

USER_TEMPLATE = "Title: {title}\n\nDescription:\n{description}"


def build_user(row: dict) -> str:
    desc = (row.get("description") or "").strip()
    return USER_TEMPLATE.format(title=row.get("title") or "", description=desc[:12000])


def parse_reply(text: str) -> dict:
    """Safely parse the JSON, stripping markdown fences if the model accidentally includes them."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse JSON: {e}\nRaw output: {text}")


async def process_row(
    row: dict,
    sem: asyncio.Semaphore,
    cache: dict,
    cache_path: Path,
    pbar: tqdm,
):
    async with sem:
        try:
            raw_text = await llm.complete(SYSTEM, build_user(row), max_tokens=1000)
            extracted_data = parse_reply(raw_text)

            merged = {**row, "extracted_info": extracted_data}

            cache[row["job_url"]] = merged
            with cache_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(merged, ensure_ascii=False) + "\n")

            pbar.update(1)

        except Exception as e:
            pbar.write(f"Error processing {row.get('job_url')}: {e}")


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract minimal tech data with the Hermes model backend (mimo-v2.6-pro) in English"
    )
    parser.add_argument(
        "--in", dest="src", type=str, default="data/step-05-consolidated.jsonl"
    )
    parser.add_argument("--out", type=str, default="data/step-06-llm-extracted.jsonl")
    parser.add_argument(
        "--cache", type=str, default="data/step-06-llm-extracted-cache.jsonl"
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = read_jsonl(args.src)
    dedup = {}
    for r in rows:
        dedup.setdefault(r.get("job_url"), r)
    rows = list(dedup.values())
    print(f"{len(rows)} jobs to extract from", flush=True)

    cache = {r["job_url"]: r for r in read_jsonl(args.cache)}
    todo = [r for r in rows if r.get("job_url") not in cache]
    if args.limit:
        todo = todo[: args.limit]

    if not todo:
        print("all cached")
        return

    print(f"Will bill ~{len(todo)} LLM calls (model {llm.get_model()}).", flush=True)

    sem = asyncio.Semaphore(CONCURRENCY_LIMIT)

    cache_path = Path(args.cache)
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    with tqdm(total=len(todo), desc="Extracting JSON", unit="job") as pbar:
        tasks = [process_row(row, sem, cache, cache_path, pbar) for row in todo]
        await asyncio.gather(*tasks)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    with out.open("w", encoding="utf-8") as fh:
        for row in rows:
            merged = cache.get(row["job_url"], row)
            fh.write(json.dumps(merged, ensure_ascii=False) + "\n")

    print(f"wrote {out}")


if __name__ == "__main__":
    asyncio.run(async_main())

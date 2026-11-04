"""Step 05: consolidate tech job descriptions into clean, complete English Markdown.

Reads data/tech_filtered.jsonl and rewrites each job's raw `description` into a
clean, full, nicely-formatted English job description — stripping HTML artifacts,
marketing/legal boilerplate, and non-informational text. NOT a summary: every
meaningful requirement, responsibility, qualification, and concrete benefit stays.

Outputs data/consolidated.jsonl (metadata kept, `description` replaced by the
clean version), with a resumable cache at data/consolidate-cache.jsonl.

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

CONCURRENCY_LIMIT = 32  # Adjust based on your API rate limits

SYSTEM = (
    "You clean up raw IT job descriptions. Given a noisy job posting "
    "(HTML-stripped, possibly mixed Vietnamese/English, full of boilerplate), you "
    "rewrite it into a clean, complete, full English job description in Markdown. "
    "Output ONLY the section headings and their content — no commentary, no code "
    "fences, no preamble. "
    "Use these sections, in this order, omitting any with no content: "
    "## Company (company name plus a brief factual description of what the company "
    "does — industry or product — stripped of marketing fluff), "
    "## Details (a short bulleted list: Location, Seniority/Level, Salary if stated, "
    "Work model), "
    "## Responsibilities, ## Requirements / Qualifications, ## Benefits. "
    "Do NOT summarize or condense: keep every meaningful responsibility, requirement, "
    "qualification, and concrete benefit. Strip all boilerplate: HTML artifacts, "
    "navigation/header/footer text, 'Top 3 reasons to join us' / "
    "'Why you'll love working here' company marketing, 'Apply now' / application "
    "instructions, equal-opportunity and legal boilerplate, phishing alerts, and "
    "repeated title/company lines."
)

USER_TEMPLATE = """Rewrite the job description below into clean English Markdown.

Use these sections, in this order, omitting any section that has no content in the source:
## Company
## Details
## Responsibilities
## Requirements / Qualifications
## Benefits

Job Title: {title}
Company: {company}
Location: {location}
Salary: {salary}
Experience: {experience_level}
Work model: {work_model}
Skills: {skills}

Raw description:
{description}"""


def build_user(row: dict) -> str:
    desc = (row.get("description") or "").strip()
    return USER_TEMPLATE.format(
        title=row.get("title") or "",
        company=row.get("company") or "",
        location=row.get("location") or "",
        salary=row.get("salary") or "",
        experience_level=row.get("experience_level") or "",
        work_model=row.get("work_model") or "",
        skills=", ".join(row.get("skills") or []),
        description=desc[:12000],
    )


def parse_reply(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def process_row(
    row: dict,
    sem: asyncio.Semaphore,
    cache: dict,
    cache_path: Path,
    pbar: tqdm,
):
    async with sem:
        try:
            raw_text = await llm.complete(SYSTEM, build_user(row), max_tokens=2000)

            clean = parse_reply(raw_text)
            merged = {**row, "description": clean}

            cache[row["job_url"]] = merged
            with cache_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(merged, ensure_ascii=False) + "\n")

            pbar.update(1)

        except Exception as e:
            # Use pbar.write instead of print to avoid breaking the progress bar visuals
            pbar.write(f"Error processing {row.get('job_url')}: {e}")


async def async_main() -> None:
    parser = argparse.ArgumentParser(
        description="Consolidate tech descriptions with the Hermes model backend (mimo-v2.6-pro)"
    )
    parser.add_argument("--in", dest="src", type=str, default="data/step-04-tech.jsonl")
    parser.add_argument("--out", type=str, default="data/step-05-consolidated.jsonl")
    parser.add_argument(
        "--cache", type=str, default="data/step-05-consolidate-cache.jsonl"
    )
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    rows = read_jsonl(args.src)
    dedup = {}
    for r in rows:
        dedup.setdefault(r.get("job_url"), r)
    rows = list(dedup.values())
    print(f"{len(rows)} unique tech jobs", flush=True)

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

    # Initialize tqdm progress bar
    with tqdm(total=len(todo), desc="Consolidating jobs", unit="job") as pbar:
        tasks = [process_row(row, sem, cache, cache_path, pbar) for row in todo]
        await asyncio.gather(*tasks)

    # Write final consolidated output
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

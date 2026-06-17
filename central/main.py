from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator
import tempfile
import os
import subprocess
import httpx
import asyncio
import time

app = FastAPI()

REQUEST_TIMEOUT = 300.0
WORKERS = os.environ.get(
    "WORKERS",
    "https://smile-penalty-tagged-ship.trycloudflare.com,"
    "https://nails-carey-pond-another.trycloudflare.com,"
    "https://definitions-suggestions-film-blackberry.trycloudflare.com,"
    "http://localhost:8004"
).split(",")

class QueryRequest(BaseModel):
    sequence: str
    query_id: str
    top_n: int = 5

    @field_validator('sequence')
    @classmethod
    def sequence_must_be_long_enough(cls, v):
        if len(v) < 20:
            raise ValueError('Sequence must be at least 20bp')
        return v

    @field_validator('top_n')
    @classmethod
    def top_n_must_be_positive(cls, v):
        if v < 1:
            raise ValueError('top_n must be at least 1')
        return v


class Candidate(BaseModel):
    id: str
    sequence: str


class WorkerResponse(BaseModel):
    candidates: list[Candidate]
    timing: dict


async def query_worker(client, worker_url, req) -> WorkerResponse:
    print(f"\n=== Querying {worker_url} ===")

    response = await client.post(
        f"{worker_url}/search",
        json={"sequence": req.sequence, "query_id": req.query_id},
        timeout=REQUEST_TIMEOUT,
    )

    print(f"{worker_url}: status={response.status_code}")
    print(f"{worker_url}: body={repr(response.text[:500])}")

    try:
        return WorkerResponse(**response.json())
    except Exception as e:
        print(f"\nFAILED TO PARSE RESPONSE FROM {worker_url}")
        print("Status:", response.status_code)
        print("Body:", repr(response.text))
        raise

def _extract_candidates(responses: list[WorkerResponse]) -> list[Candidate]:
    seen_sequences = {}

    for response in responses:
        for candidate in response.candidates:
            if candidate.sequence not in seen_sequences:
                seen_sequences[candidate.sequence] = candidate

    return list(seen_sequences.values())


def _run_blast(query_seq: str, query_id: str, candidates: list[Candidate]) -> str:

    with tempfile.TemporaryDirectory() as tmpdir:  # Deletes itself when finished
        query_path = os.path.join(tmpdir, "query.fasta")
        candidates_path = os.path.join(tmpdir, "candidates.fasta")

        with open(query_path, "w") as f:
            f.write(f">{query_id}\n{query_seq}\n")  # Temp fasta file

        with open(candidates_path, "w") as f:
            for candidate in candidates:
                f.write(f">{candidate.id}\n{candidate.sequence}\n")

        command = [
            "blastn",
            "-query",
            query_path,
            "-subject",
            candidates_path,
            "-outfmt",
            "6",
        ]

        process = subprocess.run(command, capture_output=True, text=True)

        if process.returncode != 0:
            print("Error running BLASTN:", process.stderr)
            raise HTTPException(status_code=500, detail="BLASTN search failed")

        return process.stdout


def _parse_blast_output(blast_output: str) -> list[dict]:
    hits = []
    for line in blast_output.strip().split("\n"):
        if line == "":
            continue
        fields = line.split("\t")
        hit = {
            "query_id": fields[0],
            "subject_id": fields[1],
            "percent_identity": float(fields[2]),
            "alignment_length": int(fields[3]),
            "mismatches": int(fields[4]),
            "gap_opens": int(fields[5]),
            "query_start": int(fields[6]),
            "query_end": int(fields[7]),
            "subject_start": int(fields[8]),
            "subject_end": int(fields[9]),
            "evalue": float(fields[10]),
            "bitscore": float(fields[11]),
        }
        hits.append(hit)
    return hits


def _format_report(req: QueryRequest, worker_stats: list, candidates_count: int, hits: list) -> str:
    top_hits = hits[:req.top_n]
    lines = [
        "====================================================",
        "DISTRIBUTED BLAST PIPELINE REPORT",
        "====================================================",
        f"Query ID:        {req.query_id}",
        f"Query length:    {len(req.sequence)} bp",
        f"Nodes queried:   {len(worker_stats)}",
        "",
        "WORKER STATISTICS",
        "-----------------",
    ]
    for w in worker_stats:
        lines.append(f"  {w['worker_url']}: {w['candidates_found']} candidates ({w['search_time_seconds']}s)")
    lines += [
        "",
        f"Total candidates after deduplication: {candidates_count}",
        "",
        f"TOP {req.top_n} HITS (sorted by E-value)",
        "-------------------------------",
    ]
    for i, hit in enumerate(top_hits, 1):
        lines.append(f"{i}. {hit['subject_id']}")
        lines.append(f"   Identity: {hit['percent_identity']}% | Length: {hit['alignment_length']} | E-value: {hit['evalue']} | Score: {hit['bitscore']}")
    lines += [
        "",
        "NOTE: E-values are relative to the candidate pool size, not the full",
        "dataset. Rankings are valid but absolute values are not comparable to",
        "standard full-database BLAST results.",
        "====================================================",
    ]
    return "\n".join(lines)


@app.post("/query")
async def query(req: QueryRequest):
    pipeline_start = time.time()
    tasks = []
    results = []
    async with httpx.AsyncClient() as client:
        for worker in WORKERS:
            task = query_worker(client, worker, req)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

    worker_stats = []
    for i, result in enumerate(results):
        worker_stats.append(
            {
                "worker_url": WORKERS[i],
                "candidates_found": len(result.candidates),
                "search_time_seconds": result.timing["search_time_seconds"],
            }
        )

    candidates = _extract_candidates(results)

    if len(candidates) == 0:
        return {
            "query_id": req.query_id,
            "query_length": len(req.sequence),
            "workers_queried": len(WORKERS),
            "worker_stats": worker_stats,
            "candidates_after_dedup": 0,
            "hits": [],
            "note": "No candidates found across all workers",
            "report": _format_report(req, worker_stats, len(candidates), []),
            "total_pipeline_time_seconds": round(time.time() - pipeline_start, 3),
        }

    blast_output = _run_blast(req.sequence, req.query_id, candidates)
    hits = _parse_blast_output(blast_output)
    hits.sort(key=lambda h: h["evalue"])

    return {
        "query_id": req.query_id,
        "query_length": len(req.sequence),
        "workers_queried": len(WORKERS),
        "worker_stats": worker_stats,
        "candidates_after_dedup": len(candidates),
        "hits": hits,
        "report": _format_report(req, worker_stats, len(candidates), hits),
        "total_pipeline_time_seconds": round(time.time() - pipeline_start, 3),
    }

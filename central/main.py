from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import tempfile
import os
import subprocess
import httpx
import asyncio
import time

app = FastAPI()

REQUEST_TIMEOUT = 60.0
WORKERS = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
    "http://localhost:8004",
]
WORKERS = os.environ.get("WORKERS", "http://localhost:8001,http://localhost:8002,http://localhost:8003,http://localhost:8004").split(",")

class QueryRequest(BaseModel):
    sequence: str
    query_id: str


class Candidate(BaseModel):
    id: str
    sequence: str


class WorkerResponse(BaseModel):
    candidates: list[Candidate]
    timing: dict


async def query_worker(client, worker_url, req) -> WorkerResponse:
    response = await client.post(
        f"{worker_url}/search",
        json={"sequence": req.sequence, "query_id": req.query_id},
        timeout=REQUEST_TIMEOUT,
    )
    return WorkerResponse(**response.json())


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
        "total_pipeline_time_seconds": round(time.time() - pipeline_start, 3),
    }

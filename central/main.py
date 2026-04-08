from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import tempfile
import os
import subprocess
import httpx
import asyncio

app = FastAPI()

REQUEST_TIMEOUT = 60.0
WORKERS = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003",
    "http://localhost:8004",
]


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


def _run_blast(query_seq: str, candidates: list[Candidate]) -> str:

    with tempfile.TemporaryDirectory() as tmpdir:  # Deletes itself when finished
        query_path = os.path.join(tmpdir, "query.fasta")
        candidates_path = os.path.join(tmpdir, "candidates.fasta")

        with open(query_path, "w") as f:
            f.write(f">query\n{query_seq}\n")  # Temp fasta file

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


@app.post("/query")
async def query(req: QueryRequest):
    tasks = []
    results = []
    async with httpx.AsyncClient() as client:
        for worker in WORKERS:
            task = query_worker(client, worker, req)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

    candidates = _extract_candidates(results)

    result = _run_blast(req.sequence, candidates)

    return {"result": result}

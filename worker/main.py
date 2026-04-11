#!/usr/bin/env python3

import os
import tempfile
import time
import subprocess
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from Bio import SeqIO
from contextlib import asynccontextmanager

SHARD_PATH = os.environ.get("SHARD_PATH", "/data/shard.fasta")
DB_PATH = os.environ.get("DB_PATH", "/data/mmseqs_db")
fasta_index = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Build the MMseqs2 database if it's not already there
    if not os.path.exists(f"{DB_PATH}.index"):
        subprocess.run(["mmseqs", "createdb", SHARD_PATH, DB_PATH], check=True)
        subprocess.run(
            ["mmseqs", "createindex", DB_PATH, "/tmp", "--search-type", "3"], check=True
        )
        print("Database compiled")
    else:
        print("Database already exists. Skipping compilation.")

    global fasta_index
    fasta_index = SeqIO.index(SHARD_PATH, "fasta")
    print("Worker startup complete")

    yield

    print("Worker shutting down")
    fasta_index.close()


app = FastAPI(lifespan=lifespan)


class QueryRequest(BaseModel):
    sequence: str
    query_id: str


@app.post("/search")
def search_sequence(req: QueryRequest):
    start_time = time.time()

    with tempfile.TemporaryDirectory() as tmpdir:  # Deletes itself when finished
        query_path = os.path.join(tmpdir, "query.fasta")
        result_path = os.path.join(tmpdir, "results.m8")

        with open(query_path, "w") as f:
            f.write(f">{req.query_id}\n{req.sequence}\n")  # Temp fasta file

        command = [
            "mmseqs",
            "easy-search",
            query_path,
            DB_PATH,
            result_path,
            tmpdir,
            "--format-output",
            "target",
            "--search-type",
            "3",
            "-s",
            "5.0",
        ]

        process = subprocess.run(command, capture_output=True, text=True)

        if process.returncode != 0:
            print("Error running MMseqs2:", process.stderr)
            raise HTTPException(status_code=500, detail="MMseqs2 search failed")

        hit_ids = set()
        if os.path.exists(result_path):
            with open(result_path, "r") as f:
                for line in f:
                    clean_line = line.strip()
                    # Ignore empty lines and comment lines
                    if clean_line != "" and not clean_line.startswith("#"):
                        hit_ids.add(clean_line)

        candidates = []
        for hit_id in hit_ids:
            if hit_id in fasta_index:
                matched_sequence = str(fasta_index[hit_id].seq)
                candidates.append({"id": hit_id, "sequence": matched_sequence})

    elapsed_time = time.time() - start_time

    return {
        "candidates": candidates,
        "timing": {
            "search_time_seconds": round(elapsed_time, 3),
            "total_matches_found": len(candidates),
        },
    }

@app.get("/health")
def health_check():
    return {"status": "ok", "sequences_loaded": len(fasta_index)}

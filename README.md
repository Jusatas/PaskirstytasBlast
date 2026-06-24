# Distributed BLAST with Pre-filtering

A distributed DNA sequence search system combining MMseqs2 pre-filtering with BLAST. The system uses a scatter-gather architecture - a central coordinator node distributes queries to worker nodes, each holding a shard of the database, and runs BLAST only on the filtered candidates returned.

## Architecture

```
User → Central Node (FastAPI + BLAST)
           ├── Worker 1 (MMseqs2, Shard 1)
           ├── Worker 2 (MMseqs2, Shard 2)
           ├── Worker 3 (MMseqs2, Shard 3)
           └── Worker 4 (MMseqs2, Shard 4)
```

## Requirements

- Python 3.10+
- `mmseqs2`
- `ncbi-blast+`
- Docker + Docker Compose (optional)

## Setup

### 1. Prepare Data

Data must be split into 4 FASTA shards under `data/shard_1.fasta` ... `data/shard_4.fasta`.

Download example dataset (~175 MB):
```bash
wget -P data/ ftp://ftp.sra.ebi.ac.uk/vol1/fastq/SRR258/003/SRR2584863/SRR2584863_1.fastq.gz
```

Convert FASTQ to FASTA:
```bash
zcat data/SRR2584863_1.fastq.gz | head -1600000 \
  | awk 'NR%4==1{print ">"substr($0,2)} NR%4==2{print}' > data/all_reads.fasta
```

Split into 4 shards of 100,000 reads each:
```python
python3 -c "
total_reads = 400000
per_shard = total_reads // 4
outfiles = [open(f'data/shard_{i+1}.fasta', 'w') for i in range(4)]
read_count = 0
with open('data/all_reads.fasta') as f:
    for line in f:
        if line.startswith('>'):
            shard_idx = min(read_count // per_shard, 3)
            read_count += 1
        outfiles[shard_idx].write(line)
for f in outfiles: f.close()
"
```

Build MMseqs2 indexes:
```bash
./build_indexes
```

---

## Running

### Option A: Manual

Install dependencies:
```bash
sudo apt install mmseqs2 ncbi-blast+
python3 -m venv .venv
source .venv/bin/activate
pip install -r worker/requirements.txt
pip install -r central/requirements.txt
```

Start workers:
```bash
./start_workers
```

Start central node (in a new terminal):
```bash
uvicorn central.main:app --port 8080
```

Stop workers:
```bash
./stop_workers
```

### Option B: Docker

```bash
docker compose up
```

---

## Usage

Send a query via curl:
```bash
curl -X POST http://localhost:8080/query \
  -H "Content-Type: application/json" \
  -d "{
    \"sequence\": \"$(sed -n '2p' data/shard_1.fasta)\",
    \"query_id\": \"test\",
    \"top_n\": 3
  }"
```

### Request Fields

| Field | Required | Description |
|-------|----------|-------------|
| `sequence` | + | DNA sequence to search |
| `query_id` | + | Identifier for this query |
| `top_n` | - | Number of results to return (default: all) |

### Response Fields

| Field | Description |
|-------|-------------|
| `query_id` | Query identifier |
| `query_length` | Query length in base pairs |
| `workers_queried` | Number of active worker nodes |
| `worker_stats` | Per-worker candidate count and search time |
| `candidates_after_dedup` | Total candidates after deduplication |
| `hits` | BLAST results |
| `report` | Human-readable summary report |
| `total_pipeline_time_seconds` | Total pipeline duration |

### Example Response

```json
{
  "query_id": "test",
  "query_length": 150,
  "workers_queried": 4,
  "worker_stats": [
    {"worker_url": "http://worker1:8000", "candidates_found": 7, "search_time_seconds": 0.8},
    {"worker_url": "http://worker2:8000", "candidates_found": 5, "search_time_seconds": 2.0},
    {"worker_url": "http://worker3:8000", "candidates_found": 2, "search_time_seconds": 29.0},
    {"worker_url": "http://worker4:8000", "candidates_found": 4, "search_time_seconds": 12.8}
  ],
  "candidates_after_dedup": 17,
  "hits": [...],
  "report": "...",
  "total_pipeline_time_seconds": 29.4
}
```

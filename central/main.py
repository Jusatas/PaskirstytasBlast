from fastapi import FastAPI
from pydantic import BaseModel
import httpx
import asyncio

app = FastAPI()

REQUEST_TIMEOUT = 60.0
WORKERS = [
    "http://localhost:8001",
]


class QueryRequest(BaseModel):
    sequence: str
    query_id: str


async def query_worker(client, worker_url, req):
    response = await client.post(
        f"{worker_url}/search",
        json={"sequence": req.sequence, "query_id": req.query_id},
        timeout=REQUEST_TIMEOUT,
    )
    return response.json()


@app.post("/query")
async def query(req: QueryRequest):
    tasks = []
    async with httpx.AsyncClient() as client:
        for worker in WORKERS:
            task = query_worker(client, worker, req)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

    return {"results": results}

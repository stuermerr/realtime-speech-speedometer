from fastapi import FastAPI


app = FastAPI(title="Speech Speedometer")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}

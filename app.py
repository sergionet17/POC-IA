from fastapi import FastAPI, Request, Query

@app.get("/webhook")
def verify(
    mode: str = Query("", alias="hub.mode"),
    challenge: str = Query("", alias="hub.challenge"),
    verify_token: str = Query("", alias="hub.verify_token"),
):
    if mode == "subscribe" and verify_token == os.getenv("VERIFY_TOKEN", "mipoc123"):
        try:
            return int(challenge)
        except Exception:
            return challenge
    return {"error": "not verified"}

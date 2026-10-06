"""Browser <-> Nova Sonic relay. uv run server.py [port]"""

import asyncio
import json
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse

from sonic import MODEL_ID, REGION, Event, SonicSession

DEFAULT_PROMPT = (
    "あなたは落ち着いた話し相手です。ユーザーと音声で自然に会話します。"
    "返答は短く、普段は一、二文で話してください。"
)

app = FastAPI()
INDEX = Path(__file__).with_name("index.html")


@app.get("/")
def index():
    return FileResponse(INDEX)


@app.get("/config")
def config():
    return {"model": MODEL_ID, "region": REGION, "prompt": DEFAULT_PROMPT}


def to_client(ev: Event, stages: dict[str, str]) -> dict | None:
    """Translate one model event into a browser message, or None to drop it."""
    match ev.kind:
        case "audioOutput":
            return {"t": "audio", "d": ev.body["content"]}
        case "contentStart" if ev.body.get("type") == "TEXT":
            fields = json.loads(ev.body.get("additionalModelFields") or "{}")
            stages[ev.body["contentId"]] = stages["_last"] = fields.get("generationStage", "FINAL")
            return None
        case "textOutput":
            text: str = ev.body["content"]
            role = ev.body.get("role")
            if text.startswith("{") and '"interrupted"' in text:
                return {"t": "interrupt"}
            stage = stages.get(ev.body.get("contentId", ""), stages.get("_last", "FINAL"))
            if role == "ASSISTANT" and stage != "SPECULATIVE":
                return None
            return {"t": "text", "role": role, "text": text}
        case "contentEnd" if ev.body.get("type") == "AUDIO" and ev.body.get("stopReason") == "END_TURN":
            return {"t": "turn_end"}
        case "toolUse":
            return {"t": "text", "role": "SYSTEM", "text": f"toolUse: {ev.body.get('toolName')}"}
        case _:
            return None


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    params = sock.query_params
    prompt = params.get("prompt") or DEFAULT_PROMPT
    voice = params.get("voice") or "tiffany"
    try:
        async with SonicSession(prompt, voice=voice) as s:
            await sock.send_json({"t": "ready"})

            async def upstream():
                while True:
                    msg = json.loads(await sock.receive_text())
                    match msg["t"]:
                        case "audio":
                            await s.audio(msg["d"])
                        case "text":
                            await s.text(msg["text"])

            async def downstream():
                stages: dict[str, str] = {}
                async for ev in s.events():
                    out = to_client(ev, stages)
                    if out:
                        await sock.send_json(out)

            done, pending = await asyncio.wait(
                [asyncio.create_task(upstream()), asyncio.create_task(downstream())],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            for t in done:
                if (e := t.exception()) and not isinstance(e, WebSocketDisconnect):
                    raise e
    except WebSocketDisconnect:
        return
    except Exception as e:
        print("session error:", repr(e), file=sys.stderr)
        try:
            await sock.send_json({"t": "error", "message": f"{type(e).__name__}: {e}"})
            await sock.close()
        except Exception:
            pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 18420
    uvicorn.run(app, host="127.0.0.1", port=port)

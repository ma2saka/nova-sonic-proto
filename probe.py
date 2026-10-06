"""Send one text turn and save the spoken reply: uv run probe.py "<text>" out.wav"""

import asyncio
import base64
import sys
import time
import wave

from sonic import OUTPUT_RATE, SonicSession


async def main(text: str, path: str):
    pcm = bytearray()
    t0 = time.monotonic()
    first = None
    async with SonicSession("あなたは親しみやすいアシスタントです。短く答えてください。") as s:
        async def silence():
            chunk = base64.b64encode(bytes(3200)).decode()  # 100ms @16kHz
            while True:
                await s.audio(chunk)
                await asyncio.sleep(0.1)

        feeder = asyncio.create_task(silence())
        await asyncio.sleep(0.3)
        await s.text(text)
        async for ev in s.events():
            match ev.kind:
                case "audioOutput":
                    first = first or time.monotonic() - t0
                    pcm += base64.b64decode(ev.body["content"])
                case "textOutput":
                    print(f"[{ev.body.get('role')}] {ev.body['content']}")
                case "contentStart" | "contentEnd" | "completionStart" | "usageEvent":
                    print("  ", ev.kind, {k: v for k, v in ev.body.items() if k not in ("promptName", "sessionId", "completionId")})
                case _:
                    print("  ", ev.kind, ev.body)
            if ev.kind == "contentEnd" and ev.body.get("stopReason") == "END_TURN" and ev.body.get("type") == "AUDIO":
                break
        feeder.cancel()
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(OUTPUT_RATE); w.writeframes(pcm)
    print(f"first audio {first:.2f}s, {len(pcm)/2/OUTPUT_RATE:.1f}s of audio -> {path}")


asyncio.run(main(sys.argv[1], sys.argv[2]))

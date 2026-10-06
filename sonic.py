"""Nova Sonic bidirectional stream session."""

import json
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from functools import cache

import boto3
from aws_sdk_bedrock_runtime.client import AsyncBedrockRuntimeClient
from aws_sdk_bedrock_runtime.config import Config, HTTPAuthSchemeResolver, SigV4AuthScheme
from aws_sdk_bedrock_runtime.models import (
    BidirectionalInputPayloadPart,
    InvokeModelWithBidirectionalStreamInputChunk,
    InvokeModelWithBidirectionalStreamOperationInput,
)
from smithy_aws_core.identity.components import AWSCredentialsIdentity, AWSIdentityProperties
from smithy_core.aio.interfaces.identity import IdentityResolver

MODEL_ID = os.environ.get("SONIC_MODEL_ID", "amazon.nova-2-5-sonic")
REGION = os.environ.get("SONIC_REGION", "ap-northeast-1")
INPUT_RATE = 16000
OUTPUT_RATE = 24000


@cache
def _boto3_credentials():
    creds = boto3.Session().get_credentials()
    assert creds is not None, "no AWS credentials; run `aws sso login`"
    return creds


class Boto3CredentialsResolver(IdentityResolver[AWSCredentialsIdentity, AWSIdentityProperties]):
    """Resolves credentials through boto3 (AWS_PROFILE, SSO cache, etc.), refreshing near expiry."""

    async def get_identity(self, *, properties: AWSIdentityProperties) -> AWSCredentialsIdentity:
        frozen = _boto3_credentials().get_frozen_credentials()
        return AWSCredentialsIdentity(
            access_key_id=frozen.access_key,
            secret_access_key=frozen.secret_key,
            session_token=frozen.token,
        )


def make_client() -> AsyncBedrockRuntimeClient:
    return AsyncBedrockRuntimeClient(Config(
        endpoint_uri=f"https://bedrock-runtime.{REGION}.amazonaws.com",
        region=REGION,
        aws_credentials_identity_resolver=Boto3CredentialsResolver(),
        auth_scheme_resolver=HTTPAuthSchemeResolver(),
        auth_schemes={"aws.auth#sigv4": SigV4AuthScheme(service="bedrock")},
    ))


@dataclass(frozen=True)
class Event:
    """One output event from the model: `kind` is the event key, `body` its payload."""

    kind: str
    body: dict


class SonicSession:
    """One prompt on one bidirectional stream. Use `async with`."""

    def __init__(self, system_prompt: str, voice: str = "tiffany", tools: list[dict] | None = None):
        """`tools` are Bedrock toolSpec dicts; answer each `toolUse` event with `tool_result`."""
        self.system_prompt = system_prompt
        self.voice = voice
        self.tools = tools or []
        self.prompt = str(uuid.uuid4())
        self.audio_content: str | None = None

    async def __aenter__(self):
        _boto3_credentials()
        self.client = make_client()
        self.stream = await self.client.invoke_model_with_bidirectional_stream(
            InvokeModelWithBidirectionalStreamOperationInput(model_id=MODEL_ID)
        )
        await self.send({"sessionStart": {
            "inferenceConfiguration": {"maxTokens": 1024, "topP": 0.9, "temperature": 0.7},
            "turnDetectionConfiguration": {"endpointingSensitivity": "MEDIUM"},
        }})
        await self.send({"promptStart": {
            "promptName": self.prompt,
            "textOutputConfiguration": {"mediaType": "text/plain"},
            "audioOutputConfiguration": {
                "mediaType": "audio/lpcm", "sampleRateHertz": OUTPUT_RATE, "sampleSizeBits": 16,
                "channelCount": 1, "voiceId": self.voice, "encoding": "base64", "audioType": "SPEECH",
            },
            "toolUseOutputConfiguration": {"mediaType": "application/json"},
            "toolConfiguration": {"tools": [{"toolSpec": t} for t in self.tools]},
        }})
        await self.text(self.system_prompt, role="SYSTEM")
        return self

    async def __aexit__(self, *exc):
        try:
            if self.audio_content:
                await self.send({"contentEnd": {"promptName": self.prompt, "contentName": self.audio_content}})
            await self.send({"promptEnd": {"promptName": self.prompt}})
            await self.send({"sessionEnd": {}})
            await self.stream.input_stream.close()
        except Exception:
            pass

    async def send(self, event: dict):
        data = json.dumps({"event": event}).encode()
        await self.stream.input_stream.send(
            InvokeModelWithBidirectionalStreamInputChunk(value=BidirectionalInputPayloadPart(bytes_=data))
        )

    async def text(self, content: str, role: str = "USER"):
        name = str(uuid.uuid4())
        await self.send({"contentStart": {
            "promptName": self.prompt, "contentName": name, "type": "TEXT", "interactive": True,
            "role": role, "textInputConfiguration": {"mediaType": "text/plain"},
        }})
        await self.send({"textInput": {"promptName": self.prompt, "contentName": name, "content": content}})
        await self.send({"contentEnd": {"promptName": self.prompt, "contentName": name}})

    async def tool_result(self, tool_use_id: str, result: dict):
        name = str(uuid.uuid4())
        await self.send({"contentStart": {
            "promptName": self.prompt, "contentName": name, "type": "TOOL", "interactive": False,
            "role": "TOOL", "toolResultInputConfiguration": {
                "toolUseId": tool_use_id, "type": "TEXT", "textInputConfiguration": {"mediaType": "text/plain"},
            },
        }})
        await self.send({"toolResult": {"promptName": self.prompt, "contentName": name, "content": json.dumps(result, ensure_ascii=False)}})
        await self.send({"contentEnd": {"promptName": self.prompt, "contentName": name}})

    async def audio(self, b64_pcm16: str):
        """Append a base64 16kHz mono PCM16 chunk to the user's continuous audio content."""
        if self.audio_content is None:
            self.audio_content = str(uuid.uuid4())
            await self.send({"contentStart": {
                "promptName": self.prompt, "contentName": self.audio_content, "type": "AUDIO",
                "interactive": True, "role": "USER",
                "audioInputConfiguration": {
                    "mediaType": "audio/lpcm", "sampleRateHertz": INPUT_RATE, "sampleSizeBits": 16,
                    "channelCount": 1, "audioType": "SPEECH", "encoding": "base64",
                },
            }})
        await self.send({"audioInput": {"promptName": self.prompt, "contentName": self.audio_content, "content": b64_pcm16}})

    async def events(self) -> AsyncIterator[Event]:
        _, out = await self.stream.await_output()
        while True:
            result = await out.receive()
            if result is None:
                return
            if result.value and result.value.bytes_:
                payload = json.loads(result.value.bytes_.decode())["event"]
                for kind, body in payload.items():
                    yield Event(kind, body)


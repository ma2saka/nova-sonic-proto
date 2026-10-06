# nova-sonic-proto

Amazon Nova 2.5 Sonic (`amazon.nova-2-5-sonic`) とブラウザで音声対話するサンプルプログラムです。

ブラウザのマイク音声を WebSocket でローカルサーバーに渡し、サーバーが Amazon Bedrock の双方向ストリーム API（`InvokeModelWithBidirectionalStream`）へ中継します。返ってきた音声はブラウザで再生し、文字起こしを画面に表示します。テキストで話しかけることもできます。

## 必要なもの

- Python 3.13 以上と [uv](https://docs.astral.sh/uv/)
- Amazon Bedrock で Nova 2.5 Sonic を呼べる AWS 認証情報
  - IAM 権限: `bedrock:InvokeModelWithBidirectionalStream`（対象 `arn:aws:bedrock:<region>::foundation-model/amazon.nova-2-5-sonic`）
- マイクが使えるブラウザ（Chrome / Edge で確認）

## 使い方

```sh
uv sync
AWS_PROFILE=<your-profile> uv run server.py        # http://localhost:18420
```

ブラウザで http://localhost:18420 を開き、「話す」を押してマイクを許可します。設定欄で声（voiceId）とシステムプロンプトを変えられます。

テキスト 1 ターンだけを試して返答音声を WAV に保存するスクリプトもあります。

```sh
AWS_PROFILE=<your-profile> uv run probe.py "こんにちは" out.wav
```

認証情報は boto3 の標準の解決順（環境変数、`AWS_PROFILE`、SSO など）で取得します。

| 環境変数 | 既定値 |
| --- | --- |
| `SONIC_MODEL_ID` | `amazon.nova-2-5-sonic` |
| `SONIC_REGION` | `ap-northeast-1` |

サーバーは `127.0.0.1` でだけ待ち受け、認証はありません。ローカルでの動作確認用です。

## ファイル

- `sonic.py`: Bedrock 双方向ストリームのセッション（`SonicSession`）
- `server.py`: ブラウザと Sonic の間の WebSocket 中継。ツール呼び出しもここで処理する
- `tools.py`: Sonic に渡すツール（現在の日時を返す `getCurrentDateTime`）
- `index.html`: マイク入力（16kHz PCM16）の送信、返答音声（24kHz PCM16）の再生、文字起こし表示
- `probe.py`: テキスト 1 ターンの動作確認

## 実装上の注意

- イベント形式は Nova 2 Sonic と同じです。
- テキストだけで話しかける場合も、音声入力の content を開いて音声（無音でよい）を流し続ける必要があります。ブラウザ版はマイク音声を常に流しています。
- `aws-sdk-bedrock-runtime` は 0.9.0 に固定しています。0.10 以降は Config の作り方とトランスポートの指定が変わっています。
- SDK はイベントに署名するたびに認証情報リゾルバを呼びます。呼ばれるたびに `boto3.Session()` を作ると `ValidationException: Invalid input request` になりました。`sonic.py` では認証情報オブジェクトをモジュールレベルでキャッシュしています。
- ツールは `promptStart` の `toolConfiguration` で渡します。モデルが `toolUse` イベントを出したら、`TOOL` ロールの content で `toolResult` を返します（`SonicSession.tool_result`）。ツールを足すときは `tools.py` の `TOOLS` に `Tool` を追加します。
- 話者分離はできません。ユーザー側の文字起こしイベントには役割（`USER`）、本文、確定段階、言語ラベル（`[日本語]` など）が含まれ、話者を区別する項目はありません。複数人が話しても、すべて一人の `USER` の発言として扱われます。
- 1 回の接続は 8 分までです（Bedrock の `InvokeModelWithBidirectionalStream` の制約）。2.5 Sonic で試すと、接続から約 480 秒で `ModelTimeoutException: Model has timed out in processing the request.` が返って切れました。応答の途中でも切れます。このサンプルは再接続に対応していないので、切れたら「話す」を押し直してください。

## ライセンス

MIT

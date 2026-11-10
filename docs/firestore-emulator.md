# Firestore Emulatorで分析状態管理を検証する

本番のFirestoreデータベース作成・課金なしで、実Firestore SDKとローカルEmulatorの間を検証する。
Geminiは固定モックにし、実際のVertex AI呼出をテストで禁止する。

## 準備と起動

Google Cloud CLIのFirestore EmulatorコンポーネントとJava 21以上が必要。
この作業では既存のJava 25を使用し、gcloudへEmulator 1.23.0を追加した。

```bash
gcloud components install cloud-firestore-emulator
# バックエンドの8080番と衝突しないポートを使う
gcloud emulators firestore start --host-port=127.0.0.1:8787 --project=demo-waiwai-emulator
```

macOSで標準javaが17の場合は、Java 21以上を選んで起動する。
今回確認した環境の例：

```bash
env JAVA_HOME=/opt/homebrew/opt/openjdk@25/libexec/openjdk.jdk/Contents/Home PATH=/opt/homebrew/opt/openjdk@25/bin:/opt/homebrew/bin:/usr/bin:/bin gcloud emulators firestore start --host-port=127.0.0.1:8787 --project=demo-waiwai-emulator
```

## テスト

起動後、別ターミナルでリポジトリルートから実行する。

```bash
FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 .venv/bin/python -m pytest -q backend/tests/test_firestore_emulator.py
```

- この変数がなければテストはskip。外部ホストを指定するとテストを拒否する。
- テストはdemo-waiwai-emulatorという架空プロジェクトと固有の通話IDを使用する。
- ANALYSIS_STATE_BACKENDをfirestore、ANALYSIS_BACKENDをmockに固定する。
- Geminiのクライアント生成は例外に置き換え、誤って外部AIへ送ろうとしたら失敗する。
- SDKはEmulator用の匿名認証を使う。キャッシュはEmulator接続先も区別する。
- 通常のpytestでは4件がskipされるため、Emulator実行と区別して結果を記録する。

## 確認すること

1. 4件では204、5件ごとの3バッチで200、再送・分析済みとの重複で204。
2. 別のストアインスタンスでも実行中・分析済みが共有され、通話間ではIDが混ざらない。
3. HTTP 504の後に予約が解除され、同じ発言IDで再試行可能。
4. 同時トランザクションで同じ窓を予約した場合、所有者は1つだけ。

## 結果（2026-11-09）

Emulator 1.23.0、Java 25、google-cloud-firestore 2.34.1で **4 passed（5.41秒）**。
HTTP API→Firestore SDK→Emulatorへの実通信で上記を確認した。
本番Firestore・Vertex AIへのリクエストやデータベース作成は行っていない。

終了時はEmulatorのターミナルでCtrl+C。Emulator内のテストデータは起動をまたいで保持されない。
Emulatorでの成功は、本番のIAM権限・実課金・本番の競合特性・応答時間の確認を代替しない。

## 手動でバックエンドをつなぐ場合

```bash
cd backend
FIRESTORE_EMULATOR_HOST=127.0.0.1:8787 GCP_PROJECT_ID=demo-waiwai-emulator ANALYSIS_STATE_BACKEND=firestore ANALYSIS_BACKEND=mock ../.venv/bin/python -m uvicorn app.main:app --reload --port 8080 --env-file ../.env
```

SwaggerのAuthorizeにはBACKEND_API_KEYを設定する。
同じsessionIdでu1〜u5→200、同じ5件の再送→204、新規u6〜u10→200を確認する。
返る判定は固定モックであり、AIの精度評価ではない。

参考：[Google公式Emulator手順](https://docs.cloud.google.com/firestore/native/docs/emulator)。

## 40発言での実通信の読み書き計測（2026-11-09）

再実行用スクリプト：

```bash
.venv/bin/python scripts/measure_firestore_emulator.py --start-emulator
```

このスクリプトはEmulatorを起動・停止し、架空のdemoプロジェクトと合成発言を使う。
Geminiクライアントの生成は例外に置き換え、実AI呼出を禁止する。
実際のSDKのBatchGetDocumentsの返却文書（不存在の応答も含む）と、Commit成功時の書込文書数を計測する。
RPCは本物のEmulatorへ委譲しており、保存処理を偽物に置き換えた集計ではない。
読み書き数はEmulator実通信の観測値であり、本番の請求レポートではない。

| 40発言の送り方 | API呼出 | 分析処理（mock） | 204待機 | 文書読取 | 文書書込 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 新規5件ごとの8バッチ | 8 | 8 | 0 | 16 | 16 |
| 1件増えるごとに累積配列を送信 | 40 | 8 | 32 | 44 | 16 |

5件単位は予約と完了で各1読取・1書込、8回で16読取・16書込。
毎発言の送信では最初の4回はDBアクセスなし、5〜40件目の36回で確認読み取り、
8回の分析完了で追加8読み取りとなり、計44読み取り。書き込みは分析した8回分のみ。
読み取りだけのトランザクションでもCommit RPCは発生するが、書き込み文書数0と区別している。

両方式とも保存された分析済みID40件を別途読み戻して確認した。
この検算は方式ごとに追加1読み取りで、表の分析処理の数には含めない。
今回の2方式の検証全体では検算を含めて62文書読み取り・32文書書き込み。
セッション・検知レポート保存APIは呼んでおらず、それらの読み書きは含まない。
本番FirestoreとGeminiへのアクセスは0。Emulatorは実行終了時に停止した。

結果は `output/emulator-measurements/20261109T095835491517Z/` に保存。
`summary.json`、API呼出ごとの `events.jsonl`、`emulator.log` があり、履歴の読み戻しも検証済み。
これらはローカルのGit管理外の成果物。

## 最新の再検証

同時予約時のロック競合を検出・修正した後、4件の統合テストと40発言計測を再実行して成功。
[再検証結果・証跡](verification.md) に記録した。競合時は追加の読み書きが発生する可能性がある。

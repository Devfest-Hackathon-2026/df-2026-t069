# #12・#13 再検証結果

実施日：2026-11-09。今回の再検証では本番Firestore・Geminiを呼ばず、ローカルEmulatorと固定モックを使用。
保存API連携・本番IAM・実際のAI精度はこの結果で保証しない。

## 最終結果

| 確認内容 | 結果 | 範囲 |
| --- | --- | --- |
| 通常のバックエンドテスト | 122 passed / 4 skipped（0.89秒） | 4件のskipはEmulator専用で、別途下記で実行 |
| 実Firestore Emulator統合テスト | 4 passed（3.00秒） | 5件バッチ・再送204・失敗時解除・同時予約 |
| Ruff | 成功 | backend/pyproject.tomlの設定、今回関連するPython実装・テスト・スクリプト |
| JavaScript構文 | 成功 | extension/src/analysis-api.js |
| 評価サンプルの検証 | 25ケース成功 | 調整用16・評価用9。外部APIは呼ばない |
| 40発言のEmulator読み書き計測 | 成功 | 下表 |
| git diff --check | 成功 | 空白エラー等を確認 |

Emulator 1.23.0、Java 25、google-cloud-firestore 2.34.1。
通常テストのskipを成功として数えず、Emulatorの実実行と区別した。

## 再検証で見つけて修正したこと

最初の再実行では通常120件成功後、Emulatorの同時予約テストだけが失敗した。
同時に同じ文書を予約すると、トランザクションのロック競合が3回続き、SDKの再試行が尽きるケースだった。
二重予約ではなく、競合側がエラーになる不安定さを検出した。

修正内容：各トランザクションは1試行でrollbackまで完了させ、Abortedに由来する競合の場合だけ、
短い指数バックオフと揺らぎを入れ、新しいトランザクションで最大3試行する。
権限不足や設定エラーなどを無制限に再試行しない。再試行・新規トランザクションの回帰テスト2件を追加。
最終的に通常122件とEmulator4件の両方が成功した。

併せて、検証スクリプトのimport位置・整形、子プロセス実行パス、
Emulator停止用のローカルHTTP通信を、プロジェクトのRuff設定に合わせて整えた。
SQLiteファイルの既存コメントの内容は維持した。

## 40発言の再実測

| 送り方 | APIリクエスト | 分析処理（mock） | 204 | 文書読取 | 文書書込 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 新規5件ごと | 8 | 8 | 0 | 16 | 16 |
| 1件増えるたびに累積配列 | 40 | 8 | 32 | 44 | 16 |

両方で分析済みID40件を読み戻して確認した（方式ごとに別途1読取、表から除外）。
計測は実SDKをEmulatorに接続したRPCの返却文書数・コミット成功の書込文書数。
検知レポート保存・セッション保存は実行しておらず、これらの使用量は含まない。
本番の請求レポートではなく、競合・再試行なしのケースでの実通信の観測値。

## ローカルの証跡

最終成功のログ・JUnit XML：
`output/verification/20261109T102220948989Z/`

- unit-tests.log / unit-tests.xml
- emulator-tests.log / emulator-tests.xml
- ruff.log / javascript.log / sample-validation.log / diff-check.log
- 40-utterance-measurement.log / emulator.log / summary.json

40発言の呼出ごとの履歴・実測値：
`output/emulator-measurements/20261109T102227633022Z/`

失敗した最初の再検証の証跡も削除せず保持：
`output/verification/20261109T101808111776Z/`

これらはGit管理外のローカル成果物。ドキュメントの結果表と、生の証跡ファイルを区別する。
Emulatorは各実行の終了時に停止済み。

## 再実行方法

通常の確認（リポジトリルート）：

```bash
.venv/bin/python -m pytest -q backend/tests
node --check extension/src/analysis-api.js
.venv/bin/python scripts/evaluate_analysis.py
git diff --check
```

Emulatorは [起動・統合テスト手順](firestore-emulator.md) に従う。
40発言計測は以下（gcloudのEmulatorコンポーネントとJava 21以上が必要）：

```bash
.venv/bin/python scripts/measure_firestore_emulator.py --start-emulator
```

残作業・保存側との仕様調整は [連携方針](analysis-storage-handoff.md) と
[#12の引継ぎ](issue12-handoff.md) を参照。コミット・pushはこの再検証では実施しない。

# Python 3.12 インストール・開発環境構築手順

## 1. Pythonのバージョン

本プロジェクトでは **Python 3.12系** を使用する。

インストール後、必ずバージョンを確認すること。

## 2. OS別インストール方法

### 2.1 macOS

Homebrewを利用する場合、ターミナルで以下を実行する。

```bash
brew install python@3.12
```

インストール確認：

```bash
python3.12 --version
```

`Python 3.12.x` と表示されればOK。

※ Homebrewが未導入の場合は、公式サイトからインストールする。

https://brew.sh/ja/

### 2.2 Windows

1. [Python公式ダウンロードページ](https://www.python.org/downloads/windows/)を開く。
2. Python 3.12系のWindows用インストーラーを選択する。
3. インストーラーを起動する。
4. `Install Python` を実行する。
5. インストール完了後、PowerShellまたはコマンドプロンプトを開く。

インストール確認：

```powershell
py -3.12 --version
```

`Python 3.12.x` と表示されればOK。

### 2.3 Linux（Ubuntu）

ターミナルで以下を実行する。

```bash
sudo apt update
sudo apt install python3.12 python3.12-venv
```

インストール確認：

```bash
python3.12 --version
```

`Python 3.12.x` と表示されればOK。

※ Python 3.12のパッケージが見つからない場合は、Ubuntuのバージョンやディストリビューションによって手順が異なるため、無理に別のリポジトリを追加せず、管理者に確認する。

## 3. プロジェクトの開発環境構築

Pythonのインストールが完了したら、リポジトリのルートディレクトリで以下を実行する。

### 3.1 macOS / Linux

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
```

### 3.2 Windows（PowerShell）

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

PowerShellでスクリプト実行が制限されている場合は、必要に応じて以下を実行する。

```powershell
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser
```

### 3.3 Pythonバージョンの確認

仮想環境を有効化した状態で実行する。

```bash
python --version
```

`Python 3.12.x` と表示されることを確認する。

## 4. 必要なパッケージのインストール

仮想環境を有効化した状態で実行する。

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python -m pip install -r requirements-dev.txt
```

## 5. 環境変数の設定

`backend` ディレクトリで実行する。

### macOS / Linux

```bash
cp ../.env.example .env
```

### Windows（PowerShell）

```powershell
Copy-Item ..\.env.example .env
```

`.env` の設定方法は `docs/setup-gcp.md` を参照する。

## 6. 環境構築の確認

以下を実行する。

```bash
python --version
python -m pip --version
python -m pytest
```

Python 3.12系が表示され、テストが実行できればローカル開発環境の準備完了。

## 7. 注意事項

- Python 3.12系を使用すること。
- パッケージは必ず仮想環境（`.venv`）にインストールする。
- `sudo pip install` は使用しない。
- `.venv` と `.env` はGitにコミットしない。
- PythonのインストールとGCPへのアクセス権限は別のもの。Gemini疎通確認には別途GCPの認証・IAM権限が必要。

## 8. 参考資料

- [Python公式サイト](https://www.python.org/)
- [Python公式 Windows向けダウンロード](https://www.python.org/downloads/windows/)
- [Python公式 venvドキュメント](https://docs.python.org/3/library/venv.html)
- [Homebrew公式サイト](https://brew.sh/ja/)
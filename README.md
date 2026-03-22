# GOV-1000 -- US Government Scoring Platform

## 1. プロジェクト概要

GOV-1000は、アメリカ合衆国の連邦省庁・州政府・自治体の財政健全性を1000点満点でスコアリングするWebアプリケーションである。USAspending.govやCensus Bureauなどの公開データをリアルタイムで取得し、5つの評価軸（各200点）で自動スコアリングを行い、レーダーチャートやランキングとして可視化する。

Streamlit上で動作し、Streamlit Community Cloudにデプロイ済み。

---

## 2. 主な機能

### Dashboard タブ
- 15の主要連邦省庁を並列取得し、全省庁のスコアカードを一覧表示
- Government Health Score（全省庁平均スコア）を算出
- スコア変動の大きい省庁をTop Movers / Bottom Moversとして表示
- 全50州のスコアをChoroplethマップ上に色分け表示（年度スライダー付き、2017-2023）
- マップをクリックすると州の詳細スコアがその場で表示される
- GOV-1000スコアとS&P信用格付けの散布図による相関分析
- Illinois / New Jersey / Texasの時系列ケーススタディ（信用格付け予測の検証）

### Agency Detail タブ
- 省庁をドロップダウンで選択して詳細分析
- 5軸レーダーチャートと各軸の「Why X?」展開パネル（計算根拠を表示）
- Budget Snapshot（予算権限・義務額・支出額・連邦予算比率）
- Budget History（複数年の予算推移チャート）
- Daily Score Tracker（`scores_history.json`を基にした日次スコア推移）
- Save/Clear による2省庁の比較表示
- PDF / CSVエクスポート（ホワイトラベル対応: 会社名を入れた顧客向けレポート）
- Google News RSSによる最新ニュース表示

### State Scores タブ
- 州を選択して5軸の詳細分析（レーダー + メトリクスカード）
- 2017-2023年のスコア推移チャート
- Revenue vs Expenditure 推移チャート
- Save/Clear による2州の比較
- PDF / CSVエクスポート
- Google Newsによる州の最新ニュース

### City Scores タブ
- 人口上位100自治体のスコアリング
- 5軸レーダーチャートと詳細メトリクス
- PDF / CSVエクスポート

### Rankings タブ
- 全省庁・全50州・上位100都市のランキングテーブル

---

## 3. スコアリングロジック

すべてのスコアは5軸 x 200点 = 合計1000点満点で評価される。各スコアは0-200の範囲にクランプされる。

### 3.1 省庁スコアリング（Federal Agency）

対象: 15の主要連邦省庁（USDA, DOD, HHS, VA, SSAなど）

| 軸 | 最大点 | 計算式 | 説明 |
|---|---|---|---|
| Budget Efficiency | 200 | `(Outlay / Budget Authority) * 120 + (Obligated / Budget Authority) * 80` | 予算に対する実際の支出・義務付けの効率性。高いほど予算を有効活用している |
| Transparency | 200 | `CJ URL有無(60点) + サブ省庁報告数*5(最大80点) + データ完全性(最大60点)` | Congressional Justification公開の有無、サブ省庁のレポート数、APIデータの充実度 |
| Performance | 200 | `(取引件数/50000)*100(最大120点) + (新規Award数/10000)*80(最大80点)` | 当該年度のトランザクション処理量と新規Award発行量 |
| Fiscal Discipline | 200 | `Growth Score(最大150点) + Unobligated Score(最大50点)` | Growth Score: `150 - |YoY予算成長率| * 5`。急激な予算変動を減点。Unobligated Score: `50 - 未義務比率 * 100`。余らせすぎも減点 |
| Accountability | 200 | `200 - GAO指摘件数 * 20` | GAO（政府説明責任局）の監査指摘件数。`gao_findings.json`に手動で格納。件数が少ないほど高スコア |

### 3.2 州スコアリング（State）

対象: 全50州（DCを除く）。データはCensus Bureau State Government Finances APIから取得。

| 軸 | 最大点 | 計算式 | 説明 |
|---|---|---|---|
| Budget Balance | 200 | 黒字: `100 + ((Revenue - Expenditure) / Revenue) * 500`、赤字: `100 + ratio * 300` | 歳入が歳出をどれだけ上回っているか。黒字に重みを付けて評価 |
| Debt Burden | 200 | `200 - (Debt / Revenue) * 100` | 歳入に対する債務残高の比率。低いほど高スコア |
| Revenue Independence | 200 | `200 - (Federal Revenue / Total Revenue) * 400` | 連邦政府からの移転収入への依存度。自前の税収が多いほど高スコア |
| Spending Efficiency | 200 | `(1 - Interest Ratio) * 120 + Capital Ratio * 400` | 利払い比率が低く、かつ資本支出（インフラ投資等）の比率が高いほど高スコア |
| Fiscal Reserve | 200 | `(Cash & Securities / Expenditure) * 100` | 歳出に対する手元資金・有価証券の厚さ。財政バッファの指標 |

### 3.3 自治体スコアリング（Municipal / City）

対象: Census Bureau Individual Unit Finance File の人口上位100都市。固定幅テキストファイルをパースして使用。

| 軸 | 最大点 | 計算式 | 説明 |
|---|---|---|---|
| Budget Balance | 200 | 黒字: `100 + ratio * 500`、赤字: `100 + ratio * 300` | 州と同じロジック |
| Tax Base Strength | 200 | `(Taxes / Revenue) * 300` | 自前の税収が歳入に占める割合。高いほど安定した税基盤 |
| Revenue Independence | 200 | `200 - (IG Revenue / Revenue) * 400` | 政府間移転収入への依存度。低依存ほど高スコア |
| Spending Efficiency | 200 | `(Revenue / Expenditure) * 120` | 歳入で歳出をどれだけカバーできているか |
| Fiscal Capacity | 200 | `(Revenue per Capita) / 50` | 1人あたり歳入。$10,000/capita で200点満点 |

---

## 4. データソース

| データソース | 用途 | 取得方法 |
|---|---|---|
| [USAspending.gov API v2](https://api.usaspending.gov) | 連邦省庁の予算・支出・Award・サブ省庁データ | REST API（認証不要） |
| [Census Bureau State Government Finances](https://www.census.gov/programs-surveys/gov-finances.html) | 州の歳入・歳出・債務・税収など8項目 | Census Bureau timeseries API（認証不要） |
| Census Bureau Individual Unit Finance Files | 自治体の財務データ（PIDファイル + Financeファイル） | テキストファイルを`census_data/`にローカル配置 |
| `gao_findings.json` | GAO監査指摘件数（省庁別） | 手動管理のJSONファイル |
| `state_ratings.json` | S&P信用格付け（州別） | 手動管理のJSONファイル |
| Google News RSS | 省庁・州に関連する最新ニュース | RSSフィード取得 |

---

## 5. ファイル構成

```
gov-scoring-app/
├── app.py                  # メインアプリ。Streamlitの全タブUI定義、チャート、エクスポート
├── data_logic.py           # 連邦省庁スコアリング。USAspending.gov APIの呼び出しと5軸スコア計算
├── state_data.py           # 州スコアリング。Census Bureau APIの呼び出しと5軸スコア計算
├── municipal_data.py       # 自治体スコアリング。Census固定幅ファイルのパーサーと5軸スコア計算
├── ui_components.py        # 共通UIコンポーネント（CSS注入、レーダーチャート描画）
├── pdf_report.py           # PDF生成（fpdf2使用）。ホワイトラベル対応のA4レポート
├── record_scores.py        # GitHub Actions用のスタンドアロンスクリプト。日次でスコアを記録
├── gao_findings.json       # GAO監査指摘件数（手動管理）
├── state_ratings.json      # S&P信用格付けデータ（手動管理）
├── scores_history.json     # 日次スコア記録の蓄積ファイル（GitHub Actionsが自動更新）
├── requirements.txt        # Python依存パッケージ
├── census_data/            # Census Bureau Individual Unit Financeファイル（2022, 2023）
│   ├── 2022/
│   └── 2023/
└── .github/
    └── workflows/
        └── record_scores.yml  # 毎日UTC 02:00にスコア記録を実行するワークフロー
```

---

## 6. セットアップ方法

### ローカル実行

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# アプリ起動
streamlit run app.py
```

### 必要なパッケージ（requirements.txt）

```
streamlit
pandas
plotly
requests
fpdf2
```

### 自治体データの配置

City Scoresタブを使用する場合、Census BureauのIndividual Unit Finance Filesを以下のパスに配置する必要がある。

```
census_data/2022/2022_Individual_Unit_File/Fin_PID_2022.txt
census_data/2022/2022_Individual_Unit_File/2022FinEstDAT_06052025modp_pu.txt
census_data/2023/2023_Individual_Unit_Files/Fin_PID_2023.txt
census_data/2023/2023_Individual_Unit_Files/2023FinEstDAT_06052025modp_pu.txt
```

ファイルは [Census Bureau Annual Survey of State and Local Government Finances](https://www.census.gov/programs-surveys/gov-finances.html) から取得できる。

### Streamlit Community Cloudへのデプロイ

特別な環境変数やSecrets設定は不要。すべてのAPIは認証なしで利用可能。

---

## 7. GitHub Actions（日次スコア記録）

`.github/workflows/record_scores.yml` により、毎日UTC 02:00に `record_scores.py` が実行される。

### 処理の流れ

1. USAspending.gov APIから省庁一覧を取得（最大3回リトライ）
2. 15省庁それぞれについて5軸スコアを計算（省庁間に2秒のディレイ、APIコール間に1秒のディレイ）
3. 各APIコールには最大5回のリトライ（429/500/502/503/504エラー時、15秒ずつ増加するバックオフ）
4. 3省庁未満しかスコアリングできなかった場合はエラー終了（保存しない）
5. 結果を `scores_history.json` に日付キーで追記
6. GitHub Actionsがコミット＆プッシュ

### 手動実行

ワークフローは `workflow_dispatch` にも対応しているため、GitHub上のActionsタブから手動実行も可能。

---

## 8. 技術スタック

| 技術 | 用途 |
|---|---|
| Python 3.11 | 実行環境 |
| Streamlit | WebアプリフレームワークおよびUI |
| Plotly | レーダーチャート、Choroplethマップ、時系列チャート、散布図 |
| pandas | CSVエクスポート用のデータフレーム変換 |
| fpdf2 | PDF生成（Helveticaフォント、A4レイアウト） |
| requests | 外部API呼び出し（USAspending.gov, Census Bureau, Google News RSS） |
| GitHub Actions | 日次スコア記録の自動実行とコミット |
| Streamlit Community Cloud | 本番デプロイ環境 |

---

## 9. 免責事項

このツールは公開データに基づく情報提供を目的としたものであり、投資助言、信用評価、財務分析の代替として利用することを意図したものではない。スコアはUSAspending.gov、Census Bureau等の公開データから機械的に算出されたものであり、実際の財政状態を正確に反映することを保証するものではない。S&P信用格付けの引用は公開情報に基づいており、本ツールはS&P Globalとは一切関連がない。利用にあたっては、自己の責任と判断で行うこと。

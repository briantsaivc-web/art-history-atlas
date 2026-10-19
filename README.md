# 西洋藝術流派大全集 · Art History Atlas

互動式西洋繪畫流派大全集：時間軸、流派說明與關係圖、3,426 件作品（2,624 件有圖）、542 位畫家（含生平／作品特色／軼事）、依流派／時間／畫家／館藏／主題五種索引、看圖小測驗。

線上版（GitHub Pages）：`https://briantsaivc-web.github.io/art-history-atlas/`

## 檔案結構

```
index.html          單頁應用，啟動時 fetch data/*.json
data/
  movements.json    流派（id, zh, en, start, end, era, color, desc, traits, from[], against[], artists[]）
  artists.json      畫家 { "英文名": {zh, birth, death, nat} }
  works.json        作品（見下）
  museums.json      館藏 { id: {en, zh, city, country, count} }
  themes.json       主題清單 [{id, zh}]
  bios.json         畫家介紹 { "英文名": {bio, style[], anecdote, keyworks[], aka[], confidence} }
  meta.json         版本、統計、備註
img/thumb/<id>.webp 縮圖（長邊 260px）
img/800/<id>.webp   大圖（長邊 800px）
tools/validate.py   資料一致性檢查
```

### works.json 欄位
| 欄位 | 說明 |
|---|---|
| id | `c0001`（canon）或 `n12345`（NGA 編號）；新作品請用新前綴或延續編號 |
| m | 流派 id（須存在於 movements.json） |
| a | 畫家英文名（須存在於 artists.json） |
| t / z | 題名英文 / 中文 |
| y | 年代字串（`c. 1503–1506`、`540 BC`） |
| mu / mu_id | 館藏名稱 / 館藏 id（須存在於 museums.json） |
| th | 主題 id 陣列（須存在於 themes.json） |
| n | 中文導覽式說明（編者撰寫） |
| pd | 是否公共領域 |
| img / thumb | 本 repo 內的圖檔相對路徑；無圖留空 |
| src / src_url / img_ext | 圖源（Commons / NGA）、來源頁、原始外部圖片 URL |
| verif | 圖檔查證狀態 |

## 如何擴充

1. **新增畫家**：在 `artists.json` 加一筆；若要列入某流派的代表畫家，把英文名加進該流派的 `artists[]`。
2. **新增作品**：在 `works.json` 加一筆，圖片放進 `img/800/` 與 `img/thumb/`（WebP）；沒有圖就把 `img`/`thumb` 設為 `null`，頁面會顯示連結。
3. **新增流派／館藏／主題**：對應 JSON 加一筆即可，索引頁與篩選器會自動出現。
4. 改完執行 `python3 tools/validate.py`，確認「OK 無問題」再 push。

## 資料品質分級
- `tier: canon`／`nga`（v7 以前）：圖檔經人工或 API 逐件確認。
- `tier: v8`／`v9`：題名、年代、館藏、說明由 AI 依知識整理；圖檔以 Commons 站內搜尋自動配對（抽樣 40 件正確率約 95%）。每件的 `note_src`／`verif` 欄位記錄查證狀態。有圖者另以 Wikidata 比對：`wd` 欄位存 Wikidata 的題名／年代／館藏，`wd_flag` 標出不符者（可能是同題材的另一版本或複製品），請優先人工複核這些條目。發現錯誤請直接改 works.json。
- `tier: v10`（2026-10-19）：作品條目直接取自 Wikidata SPARQL（創作者 P170＋圖檔 P18＋P31=painting），題名／年代／館藏為 Wikidata 結構化資料（`wd.q` 為條目 Q 編號），圖檔即該條目的 P18；中文題名與 45–80 字說明由 AI 撰寫，資料稀少者照實寫短。館藏為 Wikidata 佔位值者記為館藏不詳。
- Gemini 稽核（2026-10-19）：201 筆 `wd_flag` 條目經 gemini-3-flash-preview 稽核（未啟用 Google 搜尋，結論未經網路查證），結果存於 `audit` 欄位：`verdict` A＝圖檔為另一版本、B＝我方登錄有誤（僅在與 Wikidata 一致時套用修正，原值存 `audit.applied`）、C＝同一作品寫法差異、D＝無法判定。`audit.html` 為稽核工具頁，金鑰只存於瀏覽器 localStorage。
- 畫家介紹（bios.json）由 AI 整理，未逐條查證原始文獻；`confidence: low` 者史料稀少。

## 資料來源與版權
- 圖片：Wikimedia Commons（公共領域作品）、美國國家藝廊 NGA 開放資料（CC0）。受著作權保護的作品不嵌圖，僅給連結。
- 說明文字為編者撰寫；主題分類為依題名初分，標「其他／待分類」者尚未人工校正。

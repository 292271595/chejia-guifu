# 法规原文存档（原始素材，不要直接改）

这里放《道路交通安全法》和《实施条例》的官方文本原件，以及从原件抽出的纯文本。
知识库里的 `road_traffic_safety_law.md` / `road_traffic_safety_regulation.md`
由 `tools/import_laws.py` 从下面两个 `.txt` 生成，**不要手工改这两个 md**，
否则下次重新导入会覆盖。

## 文件清单

| 文件 | 内容 | 说明 |
|---|---|---|
| `law_raw.txt` | 道交法 2021 修正版纯文本 | 从 `road_traffic_safety_law.docx` 抽取，124 条 |
| `regulation_body.txt` | 实施条例 2017 修订版纯文本 | 从国务院公报网页抽取，115 条 |
| `law_crosscheck.html` | 第二来源（蓬安县人民政府） | 仅用于逐条比对，不参与建库 |
| `reg_cross_zc.doc` | 第二来源（镇赉县人民政府） | 仅用于逐条比对，不建库 |
| `road_traffic_safety_law.docx` | 中国法院网原始 docx | 原件，留档 |
| `regulation.html` | 中国政府网国务院公报原始页面 | 原件，留档 |

## 来源

- **道路交通安全法（2021 年修正）**
  中国法院网公布文本，第三次修正后版本（主席令第八十一号，2021-04-29 施行）
  `https://img4.chinacourt.cn/mup/uploadfile/2026/08/22/20/933d7b32935afac36165ddba9855bc36.docx`
  第二来源：蓬安县人民政府《县交通运输局：会前学法之〈道路交通安全法〉2021 年修正版》
  `https://www.pengan.gov.cn/xwdt/ztzl/fzzf/gzdt/202211/t20221104_1742607.html`

- **实施条例（2017 年修订）**
  中国政府网国务院公报 2019 年第 1 号增刊
  `https://www.gov.cn/gongbao/content/2019/content_5468932.htm`
  （2004-04-30 国务院令第 405 号公布，根据 2017-10-07《国务院关于修改部分行政法规的决定》修订）
  第二来源：镇赉县人民政府公开的 2017 年修订版
  `https://www.zc.gov.cn/zx/ztjj/yjgl/ywwk/fgk/201711/W020171103103384186871.doc`

抓取时间：2026-11-04。

## 做过的校验（不是"看着差不多"）

1. **条号连续性**：道交法 1–124 连续；实施条例 1–115 连续。无缺号、无重号、严格递增。
2. **逐条文本比对**（与第二来源，按条切分后全文精确比对）：
   - 道交法：124 条中 123 条逐字一致；唯一差异是第 30 条，
     docx 写作「坑漕」，蓬安版写作「坑槽」，**已按官方用字更正为「坑槽」**。
   - 实施条例：115 条全部一致。差异项均为我的切分脚本剥离章节标题造成的假差异，
     以及另一版本把「瞭望」误作「了望」（公报版「瞭望」为准）。
3. **关键条抽查**：道交法第 24、76、99、101 条，实施条例第 16、91、92 条，
   逐条确认包含预期关键词（见 `tools/import_laws.py` 的 `checks`）。
4. **导入后复查**：`python tools/check_chunks.py` 中两个法规文件的
   保留率 100%、标题错位 0、缺号 0。

## 重新生成

```bash
python tools/import_laws.py      # 由 txt 生成知识库 md，自带三道校验
python tools/check_chunks.py     # 分块体检
python tools/rebuild_kb.py       # 重建向量库（需 .env 里有 EMBED_API_KEY）
```

原件若丢失，可按上面的 URL 重新下载；下载后重新抽取纯文本即可，
抽取脚本见 git 历史中的 `tools/import_laws.py` 注释。

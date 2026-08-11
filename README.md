# Calligraphy Collector · 书法集字作品生成器

从 [书法字典](http://shufazidian.com)（400 万+ 字形库）自动搜取指定书法家的单字真迹，按排版格式整合成集字作品。

## 快速开始

```bash
# 安装依赖
pip install playwright Pillow requests

# 生成作品
python scripts/collector.py --text "宁静致远" --font "行书" --author "王羲之" --layout "横排"
```

## 特性

- 支持 **行/楷/草/隶/篆/魏碑** 6 种书体
- 支持 **横排/竖排/斗方/对联** 4 种排版
- **缺字自动回退**：主书体无结果 → 相似书体 → 相似字形
- **多版本备选**：每字最多 3 个版本，优先匹配指定书法家
- **智能过滤**：URL 白名单过滤字形集锦图、多字图、App 推广图
- **单文件输出**：HTML 页面含 base64 内嵌图片 + 宣纸色背景 + 集字印章

## 参数

| 参数 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `--text` | ✅ | — | 集字文本 |
| `--font` | | 行书 | 书体：行书/楷书/草书/隶书/篆书/魏碑 |
| `--author` | | 王羲之 | 书法家 |
| `--layout` | | 横排 | 排版：横排/竖排/斗方/对联 |
| `--size` | | 200 | 单字尺寸（像素） |
| `--output` | | `output/calligraphy_collection.html` | 输出路径 |

## 示例

```bash
# 行书横幅
python scripts/collector.py --text "厚德载物" --font "行书" --author "王羲之" --layout "横排"

# 楷书对联
python scripts/collector.py --text "春风大雅能容物，秋水文章不染尘" --font "楷书" --author "颜真卿" --layout "对联"

# 魏碑竖排
python scripts/collector.py --text "守正创新" --font "魏碑" --layout "竖排"
```

## 工作流程

```
解析输入 → 逐字 Playwright 搜取 → 白名单过滤 → 书体回退 → 相似字形 → PIL 合成 → HTML 输出
```

## 书体回退链

| 主书体 | 回退顺序 |
|--------|---------|
| 魏碑 | 楷书 → 行书 |
| 草书 | 行书 → 楷书 |
| 篆书 | 隶书 → 简牍 → 楷书 |
| 楷书 | 行书 → 魏碑 |
| 行书 | 楷书 → 草书 |
| 隶书 | 楷书 → 行书 |

## 依赖

- Python 3.9+
- [Playwright](https://playwright.dev/python/)（浏览器自动化，复用系统 Chrome）
- [Pillow](https://python-pillow.org/)（图片处理与合成）
- [requests](https://docs.python-requests.org/)（图片下载）

## 已知限制

- shufazidian.com 需 JavaScript 执行环境（系统需安装 Chrome）
- 冷门字 + 冷门书体可能匹配为 0，会自动回退
- 请求频率受限，每字间隔 1-2.5 秒

## License

MIT

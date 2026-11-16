# 猎豹 V4.6 — 事件数据 Schema

## CSV 导入格式

```csv
event_id,event_time,published_time,event_type,title,source,themes,assets,direction,confidence,horizon,review_status
```

| 列 | 类型 | 必填 | 说明 |
|----|------|------|------|
| event_id | str | 可选 | 不填则自动生成 |
| event_time | str | 是 | ISO 格式，如 2026-07-20T09:00 |
| published_time | str | 是 | ISO 格式 |
| event_type | str | 否 | 默认 GENERAL |
| title | str | 是 | 事件标题 |
| source | str | 否 | 默认 CSV_IMPORT |
| themes | str | 否 | 分号分隔，如 "半导体;芯片" |
| assets | str | 否 | 分号分隔资产代码 |
| direction | str | 否 | BULLISH/BEARISH/NEUTRAL |
| confidence | float | 否 | 默认 0.5 |
| horizon | str | 否 | SHORT/MEDIUM/LONG |
| review_status | str | 否 | 默认 UNREVIEWED |

## JSON 存储格式

```json
{
  "event_id": "E001",
  "event_time": "2026-07-20T09:00",
  "published_time": "2026-07-20T09:30",
  "tradable_time": "2026-07-20T09:31",
  "event_type": "POLICY",
  "title": "半导体新政出台",
  "source": "MANUAL",
  "related_themes": ["半导体"],
  "related_assets": ["512480", "159813"],
  "direction": "BULLISH",
  "confidence": 0.7,
  "expected_horizon": "MEDIUM",
  "review_status": "REVIEWED"
}
```

## 事件类型

| 类型 | 说明 |
|------|------|
| POLICY | 政策类事件 |
| INDUSTRY | 行业类事件 |
| MACRO | 宏观类事件 |
| BLACK_SWAN | 黑天鹅事件 |
| EARNINGS | 财报/业绩类 |
| GEOPOLITICAL | 地缘政治 |
| GENERAL | 一般事件 |

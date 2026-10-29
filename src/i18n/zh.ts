const zh = {
  // Header
  "app.title": "旅游路线与必吃推荐助手",
  "app.subtitle": "输入想去的地方、想吃的美食和玩几天，自动规划不折返可行路线与每餐推荐",

  // Empty state
  "empty.title": "旅游路线生成 + 必吃推荐",
  "empty.hint": "告诉我目的地、想去的景点、想吃的美食特色（正餐/小吃/夜市）以及游玩天数。排程由确定性算法生成，自动消除折返并校验营业时间。",
  "empty.features": "2-opt 动线优化 · 高德实时数据 · 餐饮双轨制 · 会话记忆",

  // Chat input
  "chat.placeholder": "发消息… 例如：去成都玩 3 天，想去大熊猫基地和宽窄巷子，必须吃地道火锅",
  "chat.hint": "由 OpenAI Agents SDK + 高德地图 Web API + Python 排程引擎驱动",

  // Preset questions
  "preset.1": "去成都玩 3 天，想去宽窄巷子、大熊猫基地、锦里，想吃地道老火锅和特色小吃，住春熙路附近。",
  "preset.2": "想去北京玩 2 天，想去故宫、天坛和颐和园，想吃烤鸭和北京小吃，怎么安排路线不绕路？",

  // Debug panel toggle
  "dev.open": "调试与源码",
  "dev.close": "收起调试",
  "dev.title": "切换调试面板与源码",

  // Tool indicators
  "tool.geocode": "位置解析",
  "tool.weather": "天气查询",
  "tool.spots": "景点检索",
  "tool.food": "美食推荐",
  "tool.itinerary": "算法排程",

  // Status & errors
  "status.error": "⚠️ 请求失败，请检查后端服务是否启动。",
  "status.stopped": "⏹ *已停止生成*",
  "status.backendError": "⚠️ 后端中断请求失败，服务端可能仍在运行。",

  // Debug panel
  "debug.title": "传输流",
  "debug.events": "事件",
  "debug.clear": "清除",
  "debug.empty": "等待 SSE 事件...",
  "debug.emptyHint": "发送消息后，所有原始后端数据将在此处显示。",

  // Conversation sidebar
  "sidebar.label": "会话列表",
  "sidebar.title": "会话",
  "sidebar.newChat": "新建聊天",
  "sidebar.loading": "正在加载会话...",
  "sidebar.loadMore": "加载更多",
  "sidebar.loadingMore": "加载中...",
  "sidebar.emptyTitle": "暂无会话",
  "sidebar.emptyHint": "点击「新建聊天」开始第一段对话。",
  "sidebar.delete": "删除会话",
  "sidebar.deleteConfirm": "确定要永久删除这个会话吗？此操作不可恢复。",

  // Aria labels (button hover/screen-reader)
  "aria.send": "发送",
  "aria.clearHistory": "清除历史",
  "aria.stopGeneration": "停止生成",

  // Language toggle
  "lang.switch": "English",

  // ─── Floating bottom-right action badges ─────────────────────────────
} as const;

export default zh;

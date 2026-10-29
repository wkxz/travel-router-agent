export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  /**
   * True while the assistant is actively producing this message
   * (between the first text_delta and the final done/error event).
   * Drives the in-bubble blinking caret to give the user feedback
   * that more content is still streaming. Cleared once done/error fires.
   */
  streaming?: boolean;
  /** 排程引擎生成的行程：经 SSE plan 事件直接推送，不经模型转述 */
  plan?: TripPlan;
}

/* ── 行程数据：结构与 app/engine/renderer.py::render_dict 一致 ── */

export interface PlanLocation {
  name: string;
  lng: number;
  lat: number;
  poi_id?: string;
}

export type PlanItemCategory = 'hotel' | 'spot' | 'meal' | 'snack' | 'transit' | 'free';

export interface PlanItem {
  time_slot: string;
  category: PlanItemCategory;
  title: string;
  description: string;
  transit_info: string | null;
  cost: number | null;
  open_time: string;
  rating: number | null;
  warnings: string[];
  location: PlanLocation | null;
}

export interface PlanDay {
  day_number: number;
  date: string;
  theme: string;
  total_spots: number;
  total_cost: number;
  has_unknown_cost: boolean;
  estimated_distance_km: number;
  items: PlanItem[];
}

export interface TripPlan {
  kind: 'trip_plan';
  city: string;
  total_days: number;
  hotel: (PlanLocation & { address: string }) | null;
  summary: string;
  notes: string[];
  tips: string[];
  unassigned_spots: string[];
  /** 没排进行程的必吃美食及原因；旧的历史记录里没有这个字段 */
  unmet_must_eat?: string[];
  total_cost: number;
  has_unknown_cost: boolean;
  total_distance_km: number;
  days: PlanDay[];
}

export interface ToolLampState {
  id: string;
  label: string;
  icon: string;
  active: boolean;
  animKey: number;   // Incremented on each activation to remount and replay animation
}

/**
 * Lightweight summary of a conversation, returned by /conversations.
 * Used to render the left sidebar — does NOT contain full message content.
 */
export interface ConversationSummary {
  id: string;
  title: string;
  preview?: string;
  lastMessageAt?: number;
  createdAt?: number;
  userId?: string;
  messageCount?: number;
}

export interface ListConversationsParams {
  userId: string;
  limit?: number;
  order?: 'asc' | 'desc';
  after?: string;
  before?: string;
}

export interface ListConversationsResponse {
  conversations: ConversationSummary[];
  nextCursor?: string;
  previousCursor?: string;
}

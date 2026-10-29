import type React from 'react';
import styles from './CodeViewer.module.css';

/* ── Token factory ── */
const token = (cls: string) =>
  function Token({ t }: { t: string }) { return <span className={cls}>{t}</span>; };

const Cmt = token(styles.cmt);
const Dec = token(styles.dec);
const Kw  = token(styles.kw);
const Fn  = token(styles.fn);
const Ty  = token(styles.ty);
const Str = token(styles.str);
const Doc = token(styles.doc);
const Op  = token(styles.op);
const Va  = token(styles.va);

interface LineProps { n: number; children?: React.ReactNode }
const L = ({ n, children }: LineProps) => (
  <div className={styles.line}>
    <span className={styles.ln}>{String(n).padStart(2, ' ')}</span>
    <span className={styles.lc}>{children ?? ' '}</span>
  </div>
);

/* Indent helpers */
const I1 = ({ children }: { children?: React.ReactNode }) => (
  <><span className={styles.indent} />{children}</>
);
const I2 = ({ children }: { children?: React.ReactNode }) => (
  <><span className={styles.indent} /><span className={styles.indent} />{children}</>
);

export default function CodeViewer() {
  return (
    <div className={styles.panel}>
      {/* ── Header ── */}
      <div className={styles.header}>
        <div className={styles.headerLeft}>
          <span className={styles.fileIcon}>⬡</span>
          <span className={styles.filename}>app/agent.py</span>
        </div>
        <span className={styles.badge}>READ ONLY</span>
      </div>

      {/* ── Code body ── */}
      <div className={styles.body}>
        {/* CRT scanline overlay */}
        <div className={styles.scanline} aria-hidden />

        <div className={styles.code}>
          {/* ── Imports ── */}
          <L n={1}><Kw t="from " /><Va t="openai" /><Kw t=" import " /><Ty t="AsyncOpenAI" /></L>
          <L n={2}><Kw t="from " /><Va t="agents" /><Kw t=" import " /><Ty t="Agent" /><Op t=", " /><Ty t="OpenAIChatCompletionsModel" /><Op t=", " /><Ty t="Runner" /><Op t=", " /><Dec t="function_tool" /></L>
          <L n={3} />

          {/* ── 1. Tool definition ── */}
          <L n={4}><Cmt t="# 1. Define tools with @function_tool" /></L>
          <L n={5}><Dec t="@function_tool" /></L>
          <L n={6}>
            <Kw t="def " /><Fn t="geocode_location" /><Op t="(" />
            <Va t="address" /><Op t=": " /><Ty t="Annotated" /><Op t="[" />
            <Ty t="str" /><Op t=", " /><Str t='"Address to geocode"' />
            <Op t="]) -> " /><Ty t="str" /><Op t=":" />
          </L>
          <L n={7}>
            <I1><Doc t='"""Resolve an address into lng,lat via AMap Geocode API."""' /></I1>
          </L>
          <L n={8}>
            <I1><Cmt t="# ..." /></I1>
          </L>
          <L n={9} />
          <L n={10}>
            <Va t="TOOLS" /><Op t=" = [" /><Fn t="geocode_location" /><Op t=", " /><Fn t="get_city_weather" /><Op t=", " /><Fn t="search_scenic_spots" /><Op t=", " /><Fn t="search_food_and_snacks" /><Op t=", " /><Fn t="generate_trip_itinerary" /><Op t="]" />
          </L>
          <L n={11} />

          {/* ── 2. Agent ── */}
          <L n={12}><Cmt t="# 2. Agent: the model only understands, searches and explains" /></L>
          <L n={13}>
            <Va t="agent" /><Op t=" = " /><Ty t="Agent" /><Op t="(" /><Va t="name" /><Op t="=" /><Str t='"旅游路线与必吃推荐助手"' /><Op t=", " />
            <Va t="instructions" /><Op t="=" /><Fn t="build_instructions" /><Op t="," />
          </L>
          <L n={14}>
            <I1><Va t="model" /><Op t="=" /><Va t="llm_model" /><Op t=", " /><Va t="tools" /><Op t="=" /><Va t="TOOLS" /><Op t=")" /></I1>
          </L>
          <L n={15} />

          {/* ── 3. One turn as an SSE stream ── */}
          <L n={16}><Cmt t="# 3. One turn → SSE frames (app/agent.py)" /></L>
          <L n={17}>
            <Kw t="async def " /><Fn t="chat_events" /><Op t="(" /><Va t="message" /><Op t=", " /><Va t="conversation_id" /><Op t=", " /><Va t="session" /><Op t=", " /><Va t="cancel_event" /><Op t="):" />
          </L>
          <L n={18}>
            <I1><Va t="result" /><Op t=" = " /><Ty t="Runner" /><Op t="." /><Fn t="run_streamed" /><Op t="(" /><Va t="agent" /><Op t=", " /><Va t="input" /><Op t="=" /><Va t="message" /><Op t=", " /><Va t="session" /><Op t="=" /><Va t="session" /><Op t=")" /></I1>
          </L>
          <L n={19}>
            <I1><Kw t="async for " /><Va t="event" /><Kw t=" in " /><Va t="result" /><Op t="." /><Fn t="stream_events" /><Op t="():" /></I1>
          </L>
          <L n={20}>
            <I2><Kw t="if " /><Va t="cancel_event" /><Op t="." /><Fn t="is_set" /><Op t="(): " /><Kw t="break" /></I2>
          </L>
          <L n={21}>
            <I2><Kw t="yield " /><Fn t="sse_event" /><Op t="(...)" /><Cmt t="  # text_delta / tool_called / plan" /></I2>
          </L>
          <L n={22}>
            <I1><Kw t="yield " /><Fn t="sse_event" /><Op t="(" /><Str t='"done"' /><Op t=", {})" /></I1>
          </L>
          <L n={23} />

          {/* ── 4. HTTP server ── */}
          <L n={24}><Cmt t="# 4. Server: POST /chat (server/app.py)" /></L>
          <L n={25}>
            <Kw t="async def " /><Fn t="chat" /><Op t="(" /><Va t="request" /><Op t="):" />
          </L>
          <L n={26}>
            <I1><Va t="session" /><Op t=" = " /><Va t="store" /><Op t="." /><Fn t="agent_session" /><Op t="(" /><Va t="cid" /><Op t=")" /><Cmt t="  # SQLite conversation memory" /></I1>
          </L>
          <L n={27}>
            <I1><Kw t="return " /><Ty t="StreamingResponse" /><Op t="(" /><Fn t="chat_events" /><Op t="(" /><Va t="message" /><Op t=", " /><Va t="cid" /><Op t=", " /><Va t="session" /><Op t=", " /><Va t="cancel" /><Op t="), " /><Va t="media_type" /><Op t="=" /><Str t='"text/event-stream"' /><Op t=")" /></I1>
          </L>
        </div>
      </div>

      {/* ── Footer tag ── */}
      <div className={styles.footer}>
        <span className={styles.footerDot} />
        <span>OpenAI Agents SDK · Starlette</span>
      </div>
    </div>
  );
}

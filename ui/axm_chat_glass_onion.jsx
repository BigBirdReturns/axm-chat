import { useState, useCallback, useEffect, useRef, useMemo } from "react";

// ═══════════════════════════════════════════════════════════════════
// AXM Chat Glass Onion
// Drop your Claude / ChatGPT export. Own the knowledge.
// Netlify-deployable. Server optional (demo mode runs in-browser).
// ═══════════════════════════════════════════════════════════════════

// Auto-detected at startup. If the local server responds, live mode.
// If not, demo mode. No manual editing required.
const SERVER_URL = "http://localhost:8410";
let _backendUrl = null;  // set by auto-detection in root component
const DEMO_MODE_INIT = true; // overridden once health check completes

// ─── Demo data ────────────────────────────────────────────────────
const DEMO_SHARDS = [
  { name: "shard_a1b2c3", title: "AXM architecture decisions", claims: 34, is_decision: true,  verified: true,  created: "2026-02-14T11:22:00Z", merkle: "a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4" },
  { name: "shard_d4e5f6", title: "Bangkok relocation planning",  claims: 18, is_decision: false, verified: true,  created: "2026-01-30T09:10:00Z", merkle: "d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1" },
  { name: "shard_g7h8i9", title: "fakesoap editorial voice",     claims: 27, is_decision: true,  verified: false, created: "2026-03-01T14:05:00Z", merkle: "g7h8i9a1b2c3g7h8i9a1b2c3g7h8i9a1" },
  { name: "shard_j0k1l2", title: "Legal strategy session",       claims: 11, is_decision: false, verified: true,  created: "2026-02-20T18:30:00Z", merkle: "j0k1l2a1b2c3j0k1l2a1b2c3j0k1l2a1" },
];

const DEMO_DECISIONS = [
  { subject: "axm-genesis", predicate: "adopted",   object: "immutable cryptographic kernel pattern", reasoning: "Separating the signing layer from the orchestration layer allows spokes to trust the protocol without trusting any specific hub.", alternatives: "monolithic repo, vendored deps", decided_at: "2026-02-14T11:22:00Z", speaker: "user" },
  { subject: "deployment",  predicate: "rejected",  object: "vendor cloud for shard storage",          reasoning: "Defeats the purpose of sovereign knowledge. Local-first is the invariant.", alternatives: "S3, GCS, IPFS", decided_at: "2026-02-14T11:45:00Z", speaker: "user" },
  { subject: "axm-chat",    predicate: "adopted",   object: "decision provenance as primary use case",  reasoning: "Chat exports are the highest-density source of undocumented reasoning. Distilling them into signed claims closes the provenance gap.", alternatives: "code history, email threads", decided_at: "2026-02-15T09:00:00Z", speaker: "user" },
  { subject: "fakesoap",    predicate: "revised",   object: "monetize after voice is established",      reasoning: "Audience trust requires the writing to precede the product pitch. Reversed sequencing would corrupt the signal.", alternatives: "subscription launch, sponsorships", decided_at: "2026-03-01T14:05:00Z", speaker: "user" },
];

const DEMO_QUERY_RESULTS = {
  "what decisions have we made": {
    columns: ["subject", "predicate", "object", "date"],
    rows: DEMO_DECISIONS.map(d => [d.subject, d.predicate, d.object, d.decided_at?.slice(0,10)]),
    sql: "SELECT subject, predicate, object, decided_at FROM claims WHERE shard_type = 'decision' ORDER BY decided_at DESC",
  },
  "what did we reject": {
    columns: ["subject", "object", "reasoning"],
    rows: DEMO_DECISIONS.filter(d => d.predicate === "rejected").map(d => [d.subject, d.object, d.reasoning]),
    sql: "SELECT subject, object, reasoning FROM claims WHERE predicate = 'rejected'",
  },
  default: {
    columns: ["subject", "predicate", "object"],
    rows: DEMO_DECISIONS.slice(0,3).map(d => [d.subject, d.predicate, d.object]),
    sql: "SELECT subject, predicate, object FROM claims LIMIT 10",
  },
};

// ─── Utility ──────────────────────────────────────────────────────
function delay(ms) { return new Promise(r => setTimeout(r, ms)); }

async function sha256hex(str) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(str));
  return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2,"0")).join("");
}

// ─── Glass Onion SVG ──────────────────────────────────────────────
function GlassOnion({ mode, onModeChange, shardCount }) {
  const modeForLayer = { world: null, spoke: "import", core: "distill", genesis: "distill", kernel: "query" };
  const activeRing = { import: "spoke", distill: "core", query: "kernel" }[mode];

  const fills = { world: "#e8e0d0", spoke: "#d4c8b4", core: "#b8a890", genesis: "#887c64", kernel: "#1a3a6e" };
  const layers = [
    { id: "world", r: 215 },
    { id: "spoke", r: 170 },
    { id: "core",  r: 125 },
    { id: "genesis", r: 80 },
    { id: "kernel", r: 40 },
  ];

  return (
    <svg viewBox="0 0 470 470" style={{ width: "100%", maxWidth: 320, cursor: "default", filter: "drop-shadow(0 6px 24px rgba(0,0,0,0.4))" }}>
      <defs>
        <radialGradient id="csheen" cx="38%" cy="32%" r="52%">
          <stop offset="0%"   stopColor="rgba(255,255,255,0.16)" />
          <stop offset="100%" stopColor="rgba(255,255,255,0)" />
        </radialGradient>
        <filter id="cglow">
          <feGaussianBlur stdDeviation="4" result="b"/>
          <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
        </filter>
      </defs>

      {[...layers].reverse().map(l => {
        const target = modeForLayer[l.id];
        const isActive = activeRing === l.id;
        const canClick = !!target;
        const dimmed = !!mode && !isActive;
        return (
          <g key={l.id}
            style={{ cursor: canClick ? "pointer" : "default", opacity: dimmed ? 0.3 : 1, transition: "opacity 0.3s" }}
            onClick={() => canClick && onModeChange(target)}>
            <circle cx={235} cy={235} r={l.r}
              fill={fills[l.id]}
              stroke={isActive ? "rgba(255,255,255,0.7)" : "rgba(0,0,0,0.1)"}
              strokeWidth={isActive ? 2.5 : 1}
              style={{ transition: "all 0.3s", filter: isActive ? "url(#cglow)" : undefined }} />
          </g>
        );
      })}

      <circle cx={235} cy={235} r={215} fill="url(#csheen)" pointerEvents="none" />

      {mode === "query"  && <circle cx={235} cy={235} r={40}  fill="none" stroke="rgba(90,140,220,0.5)"  strokeWidth={10} style={{ animation: "cpulse 2s ease-in-out infinite" }} pointerEvents="none" />}
      {mode === "distill"&& <circle cx={235} cy={235} r={125} fill="none" stroke="rgba(60,160,80,0.3)"   strokeWidth={8}  style={{ animation: "cpulse 1.5s ease-in-out infinite" }} pointerEvents="none" />}
      {mode === "import" && <circle cx={235} cy={235} r={170} fill="none" stroke="rgba(200,168,75,0.2)"  strokeWidth={6}  style={{ animation: "cpulse 2.5s ease-in-out infinite" }} pointerEvents="none" />}

      <g stroke="rgba(0,0,0,0.06)" strokeWidth={1} pointerEvents="none">
        <line x1={235} y1={18}  x2={235} y2={195} />
        <line x1={452} y1={235} x2={275} y2={235} />
        <line x1={235} y1={452} x2={235} y2={275} />
        <line x1={18}  y1={235} x2={195} y2={235} />
      </g>

      <g pointerEvents="none" fontFamily="'DM Mono',monospace">
        <path id="cpw" d="M 48,235 A 187,187 0 0,1 422,235" fill="none" />
        <text fontSize={7} fill="#a09480" letterSpacing={2.5}>
          <textPath href="#cpw" startOffset="12%">DEPLOYMENT CONTEXT · GOVERNANCE · TRUST STORE</textPath>
        </text>
        <path id="cps" d="M 78,235 A 157,157 0 0,1 392,235" fill="none" />
        <text fontSize={7} fill="#9a8870" letterSpacing={2}>
          <textPath href="#cps" startOffset="8%">CHAT SPOKE · IMPORT · PARSE · SIGN</textPath>
        </text>
        <path id="cpc" d="M 116,235 A 119,119 0 0,1 354,235" fill="none" />
        <text fontSize={7} fill="#786858" letterSpacing={2}>
          <textPath href="#cpc" startOffset="8%">AXM-CORE · DISTILL · EXTRACT CLAIMS</textPath>
        </text>
        <path id="cpg" d="M 160,222 A 78,78 0 0,1 310,222" fill="none" />
        <text fontSize={6.5} fill="#c8b890" letterSpacing={1.5}>
          <textPath href="#cpg" startOffset="5%">AXM-GENESIS · SPEC · VERIFY</textPath>
        </text>
        <text x={235} y={231} textAnchor="middle" fontFamily="'Georgia',serif" fontSize={9.5} fill="rgba(255,255,255,0.88)" letterSpacing={1}>
          {shardCount > 0 ? `${shardCount} SHARD${shardCount > 1 ? "S" : ""}` : "NO SHARDS"}
        </text>
        <text x={235} y={245} textAnchor="middle" fontSize={7} fill="rgba(160,190,240,0.65)" letterSpacing={1}>
          {shardCount > 0 ? "local · verified" : "import to begin"}
        </text>
      </g>

      {!mode && (
        <g pointerEvents="none" fontFamily="'DM Mono',monospace" fontSize={8} fill="rgba(0,0,0,0.4)" letterSpacing={0.3}>
          <text x={235} y={175} textAnchor="middle">IMPORT</text>
          <text x={235} y={137} textAnchor="middle">DISTILL</text>
          <text x={235} y={263} textAnchor="middle">QUERY</text>
        </g>
      )}
    </svg>
  );
}

// ─── Shared primitives ────────────────────────────────────────────
const IS = { background: "#120e0a", border: "1px solid #2a2420", borderRadius: 4, padding: "6px 10px", color: "#e8dcc8", fontSize: 11, fontFamily: "inherit", outline: "none", width: "100%", boxSizing: "border-box" };

function Badge({ color, bg, children }) {
  return (
    <span style={{ display: "inline-block", padding: "2px 8px", borderRadius: 4, fontSize: 10, fontWeight: 600, fontFamily: "'DM Mono',monospace", color, background: bg, letterSpacing: 0.5 }}>
      {children}
    </span>
  );
}

function Btn({ children, onClick, primary, disabled, small }) {
  return (
    <button onClick={onClick} disabled={disabled} style={{
      padding: small ? "5px 12px" : "9px 20px",
      background: primary ? "linear-gradient(135deg,#b8960a,#c8a84b)" : "transparent",
      color: primary ? "#0a0806" : "#6a5e50",
      border: primary ? "1px solid #c8a84b" : "1px solid #2a2420",
      borderRadius: 5, fontSize: small ? 10 : 11, fontFamily: "inherit",
      fontWeight: 600, cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.4 : 1, transition: "all 0.15s", letterSpacing: 0.8,
    }}>
      {children}
    </button>
  );
}

// ─── MODE: IMPORT ─────────────────────────────────────────────────
function ImportMode({ onImported }) {
  const [status, setStatus] = useState(null);
  const [log, setLog] = useState([]);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);
  const logRef = useRef(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const addLog = (msg, type = "info") => setLog(prev => [...prev, { msg, type, id: Date.now() + Math.random() }]);

  const handleImport = useCallback(async () => {
    const files = fileRef.current?.files;
    if (!files?.length) return;
    setStatus("importing"); setLog([]);

    if (!_backendUrl) {
      addLog("AXM Chat Importer v0.1.0  ·  demo mode");
      addLog(`Suite: axm-blake3-mldsa44`);
      await delay(200);
      for (let f of files) {
        addLog(`Reading: ${f.name}`);
        await delay(150);
        addLog(`  Detected format: ${f.name.endsWith(".zip") ? "ChatGPT export archive" : "Claude conversations.json"}`);
        await delay(100);
        const n = Math.floor(Math.random() * 8) + 3;
        addLog(`  Found ${n} conversations`);
        for (let i = 0; i < Math.min(n, 3); i++) {
          await delay(80);
          addLog(`  Compiling shard ${i+1}/${n}…`);
          addLog(`    Claims extracted: ${Math.floor(Math.random() * 20) + 5}`, "pass");
        }
        if (n > 3) addLog(`  … and ${n - 3} more`);
        const hash = await sha256hex(f.name + Date.now());
        addLog(`  Merkle root: ${hash.slice(0,32)}…`, "pass");
        addLog(`  Signing with ML-DSA-44 (simulated)…`);
        await delay(200);
        addLog(`✓ ${f.name} → ${n} shards compiled`, "pass");
      }
      await delay(200);
      addLog(""); addLog("PASS: Import complete", "pass");
      setResult({ imported: Array.from(files).reduce((a,f) => a + Math.floor(Math.random()*8)+3, 0), skipped: 0, errors: 0 });
      setStatus("done");
      if (onImported) onImported();
      return;
    }

    const formData = new FormData();
    for (let f of files) formData.append("files", f);
    try {
      const res = await fetch(`${_backendUrl}/import`, { method: "POST", body: formData });
      const data = await res.json();
      setResult(data); setStatus("done");
      (data.log || []).forEach(l => addLog(l));
      if (onImported) onImported();
    } catch (e) {
      setStatus("error"); addLog(`Error: ${e.message}`, "error");
    }
  }, [onImported]);

  const lc = { info: "#9a8870", pass: "#5a9060", error: "#e05c5c" };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ padding: "18px 24px", borderBottom: "1px solid #2a2420", flexShrink: 0 }}>
        <div style={{ fontFamily: "'Spectral',serif", fontSize: 18, color: "#e8dcc8", marginBottom: 6 }}>Import Conversations</div>
        <div style={{ fontSize: 11, color: "#6a5e50", lineHeight: 1.7 }}>
          Drop your Claude or ChatGPT export. ZIP or JSON.<br />
          One cryptographically signed shard per conversation.
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        <div style={{
          border: "2px dashed #2a2420", borderRadius: 8, padding: "36px 24px", textAlign: "center",
          cursor: "pointer", transition: "border-color 0.2s", marginBottom: 16,
          background: "#0e0a08",
        }} onClick={() => fileRef.current?.click()}>
          <input ref={fileRef} type="file" multiple accept=".json,.zip" style={{ display: "none" }}
            onChange={() => setStatus(null)} />
          <div style={{ fontSize: 24, marginBottom: 10, color: "#3a3020" }}>↓</div>
          <div style={{ fontFamily: "'DM Mono',monospace", fontSize: 11, color: "#6a5e50", marginBottom: 6 }}>
            {fileRef.current?.files?.length
              ? `${fileRef.current.files.length} file(s) selected`
              : "click to select export files"}
          </div>
          <div style={{ fontFamily: "'DM Mono',monospace", fontSize: 10, color: "#3a3020" }}>
            conversations.json · chatgpt_export.zip · claude_export.zip
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 16 }}>
          <Btn primary onClick={handleImport} disabled={status === "importing"}>
            {status === "importing" ? "Importing…" : "Import"}
          </Btn>
          {result && (
            <span style={{ fontFamily: "'DM Mono',monospace", fontSize: 11, color: "#5a9060" }}>
              {result.imported} imported · {result.skipped || 0} skipped · {result.errors || 0} errors
            </span>
          )}
        </div>

        {log.length > 0 && (
          <div ref={logRef} style={{ padding: 14, background: "#0a0806", border: "1px solid #1a1410", borderRadius: 6, maxHeight: 260, overflow: "auto", fontFamily: "'DM Mono',monospace", fontSize: 10.5, lineHeight: 1.7 }}>
            {log.map(l => <div key={l.id} style={{ color: lc[l.type] || lc.info }}>{l.msg}</div>)}
          </div>
        )}

        {!_backendUrl && (
          <div style={{ marginTop: 20, padding: 14, background: "#1a1410", borderRadius: 6, border: "1px solid #2a2420" }}>
            <div style={{ fontSize: 9, letterSpacing: 2, color: "#6a5e50", marginBottom: 8 }}>DEMO MODE</div>
            <div style={{ fontSize: 10, color: "#4a3e30", lineHeight: 1.7 }}>
              Running in-browser without a local server. Import simulates compilation.<br />
              To use real exports: <span style={{ color: "#6a5e50" }}>pip install -e ./axm-genesis ./axm-chat</span> then <span style={{ color: "#6a5e50" }}>python server/axm_server.py</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── MODE: DISTILL ────────────────────────────────────────────────
function DistillMode({ shards, onDistilled }) {
  const [selected, setSelected] = useState(null);
  const [dryRun, setDryRun] = useState(true);
  const [status, setStatus] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [log, setLog] = useState([]);
  const [error, setError] = useState(null);
  const logRef = useRef(null);

  useEffect(() => { if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight; }, [log]);

  const addLog = (msg, type = "info") => setLog(prev => [...prev, { msg, type, id: Date.now() + Math.random() }]);

  const handleDistill = useCallback(async () => {
    if (!selected) return;
    setStatus("running"); setDecisions([]); setError(null); setLog([]);

    if (!_backendUrl) {
      addLog("Distill Engine v0.1.0  ·  demo mode");
      addLog(`Source: ${selected}`);
      await delay(200);
      addLog("Loading shard…");
      await delay(150);
      addLog("Chunking conversation into segments…");
      await delay(200);
      addLog("Extracting decision candidates via local LLM (simulated)…");
      for (let i = 0; i < DEMO_DECISIONS.length; i++) {
        await delay(300);
        const d = DEMO_DECISIONS[i];
        addLog(`  [${d.predicate.toUpperCase()}] ${d.subject} → ${d.object.slice(0,40)}…`, "pass");
      }
      await delay(200);
      if (!dryRun) {
        addLog("Compiling decision shard…");
        await delay(300);
        addLog("Signing with ML-DSA-44 (simulated)…", "pass");
        addLog("PASS: Decision shard written", "pass");
        if (onDistilled) onDistilled();
      } else {
        addLog("Dry run complete — not compiled", "info");
      }
      setDecisions(DEMO_DECISIONS);
      setStatus(dryRun ? "dry_run" : "ok");
      return;
    }

    try {
      const data = await fetch(`${_backendUrl}/distill`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ shard: selected, model: "mistral", dry_run: dryRun }),
      }).then(r => r.json());
      setDecisions(data.decisions || []);
      setStatus(data.status);
      if (data.error) setError(data.error);
      if (data.status === "ok" && onDistilled) onDistilled();
    } catch (e) {
      setStatus("error"); setError(e.message);
    }
  }, [selected, dryRun, onDistilled]);

  const predColor = p => ({ rejected: "#e05c5c", abandoned: "#e05c5c", revised: "#c8a84b", pivoted: "#c8a84b" })[p] || "#5a9060";
  const predBg   = p => ({ rejected: "#e05c5c18", abandoned: "#e05c5c18", revised: "#c8a84b18", pivoted: "#c8a84b18" })[p] || "#5a906018";
  const lc = { info: "#9a8870", pass: "#5a9060", error: "#e05c5c" };

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ padding: "18px 24px", borderBottom: "1px solid #2a2420", flexShrink: 0 }}>
        <div style={{ fontFamily: "'Spectral',serif", fontSize: 18, color: "#e8dcc8", marginBottom: 6 }}>Distill Decisions</div>
        <div style={{ fontSize: 11, color: "#6a5e50", lineHeight: 1.7 }}>
          Extract structured decision claims from a conversation shard.<br />
          Produces a compact, signed decision shard that supersedes the original.
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        <div style={{ display: "flex", gap: 12, marginBottom: 16, flexWrap: "wrap" }}>
          <div style={{ flex: "1 1 260px" }}>
            <div style={{ fontSize: 9, letterSpacing: 2, color: "#6a5e50", marginBottom: 6 }}>CONVERSATION SHARD</div>
            <select value={selected || ""} onChange={e => setSelected(e.target.value || null)} style={{ ...IS }}>
              <option value="">Select a shard…</option>
              {shards.filter(s => !s.is_decision).map((s, i) => (
                <option key={i} value={s.name}>{s.title || s.name} ({s.claims} claims)</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: 12, alignItems: "center", marginBottom: 20 }}>
          <Btn primary onClick={handleDistill} disabled={!selected || status === "running"}>
            {status === "running" ? "Extracting…" : dryRun ? "Dry Run" : "Distill & Compile"}
          </Btn>
          <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "#6a5e50", cursor: "pointer" }}>
            <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} style={{ accentColor: "#c8a84b" }} />
            Preview only
          </label>
        </div>

        {log.length > 0 && (
          <div ref={logRef} style={{ padding: 14, background: "#0a0806", border: "1px solid #1a1410", borderRadius: 6, maxHeight: 180, overflow: "auto", fontFamily: "'DM Mono',monospace", fontSize: 10.5, lineHeight: 1.7, marginBottom: 16 }}>
            {log.map(l => <div key={l.id} style={{ color: lc[l.type] || lc.info }}>{l.msg}</div>)}
          </div>
        )}

        {error && (
          <div style={{ padding: "10px 14px", background: "#e05c5c11", border: "1px solid #e05c5c33", borderRadius: 6, fontSize: 11, color: "#e05c5c", marginBottom: 16 }}>
            {error}
          </div>
        )}

        {decisions.length > 0 && (
          <>
            <div style={{ fontSize: 9, letterSpacing: 2, color: "#6a5e50", marginBottom: 10 }}>
              {decisions.length} DECISION{decisions.length !== 1 ? "S" : ""} EXTRACTED
              {status === "dry_run" && "  ·  PREVIEW"}
              {status === "ok" && "  ·  COMPILED"}
            </div>
            {decisions.map((d, i) => (
              <div key={i} style={{ padding: 14, background: "#0e0a08", border: "1px solid #1a1410", borderRadius: 6, marginBottom: 8 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
                  <Badge color={predColor(d.predicate)} bg={predBg(d.predicate)}>{d.predicate.toUpperCase()}</Badge>
                  <span style={{ fontFamily: "'Spectral',serif", fontSize: 13, color: "#e8dcc8" }}>{d.subject}</span>
                  <span style={{ fontSize: 11, color: "#4a3e30" }}>→</span>
                  <span style={{ fontSize: 11, color: "#9a8870" }}>{d.object}</span>
                </div>
                {d.reasoning && (
                  <div style={{ fontSize: 11, color: "#6a5e50", lineHeight: 1.7, marginBottom: 4 }}>{d.reasoning}</div>
                )}
                {d.alternatives && (
                  <div style={{ fontSize: 10, color: "#3a3020", fontFamily: "'DM Mono',monospace" }}>
                    rejected alternatives: {d.alternatives}
                  </div>
                )}
                <div style={{ marginTop: 6, display: "flex", gap: 12 }}>
                  <span style={{ fontSize: 9, color: "#3a3020" }}>{d.decided_at?.slice(0,10)}</span>
                  <span style={{ fontSize: 9, color: "#3a3020" }}>speaker: {d.speaker}</span>
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </div>
  );
}

// ─── MODE: QUERY ──────────────────────────────────────────────────
function QueryMode() {
  const [question, setQuestion] = useState("");
  const [results, setResults] = useState(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);

  const suggestions = [
    "what decisions have we made",
    "what did we reject",
    "timeline of all decisions",
    "what changed since february",
    "what superseded what",
    "show all conversations",
  ];

  const runQuery = useCallback(async q => {
    if (!q.trim()) return;
    setLoading(true); setResults(null);

    await delay(!_backendUrl ? 400 : 0);

    if (!_backendUrl) {
      const key = Object.keys(DEMO_QUERY_RESULTS).find(k => q.toLowerCase().includes(k)) || "default";
      setResults(DEMO_QUERY_RESULTS[key]);
      setHistory(prev => [q, ...prev.filter(h => h !== q).slice(0, 9)]);
      setLoading(false);
      return;
    }

    try {
      const data = await fetch(`${_backendUrl}/query`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ question: q }),
      }).then(r => r.json());
      setResults(data);
      setHistory(prev => [q, ...prev.filter(h => h !== q).slice(0, 9)]);
    } catch (e) {
      setResults({ error: e.message, columns: [], rows: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      <div style={{ padding: "18px 24px", borderBottom: "1px solid #2a2420", flexShrink: 0 }}>
        <div style={{ fontFamily: "'Spectral',serif", fontSize: 18, color: "#e8dcc8", marginBottom: 6 }}>Query Knowledge</div>
        <div style={{ fontSize: 11, color: "#6a5e50", lineHeight: 1.7 }}>
          Ask in plain English. Queries run locally against mounted shards.<br />
          No SQL required. No data leaves your machine.
        </div>
      </div>

      <div style={{ flex: 1, overflow: "auto", padding: 24 }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 12 }}>
          <input
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === "Enter" && runQuery(question)}
            placeholder="what did we decide about…"
            style={{ ...IS, flex: 1, padding: "9px 14px", fontSize: 12 }}
          />
          <Btn primary onClick={() => runQuery(question)} disabled={loading || !question.trim()}>
            {loading ? "…" : "Ask"}
          </Btn>
        </div>

        <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginBottom: 20 }}>
          {suggestions.map((s, i) => (
            <button key={i} onClick={() => { setQuestion(s); runQuery(s); }} style={{
              padding: "4px 10px", background: "#0e0a08", color: "#4a3e30",
              border: "1px solid #1a1410", borderRadius: 4, fontSize: 10,
              fontFamily: "inherit", cursor: "pointer", transition: "all 0.15s",
            }}>
              {s}
            </button>
          ))}
        </div>

        {results?.sql && (
          <div style={{ padding: "10px 14px", background: "#08080e", borderRadius: 5, border: "1px solid #1a1a2e", fontFamily: "'DM Mono',monospace", fontSize: 10, color: "#3a4a6a", lineHeight: 1.6, marginBottom: 14, whiteSpace: "pre-wrap" }}>
            {results.sql}
          </div>
        )}

        {results?.error && (
          <div style={{ padding: "10px 14px", background: "#e05c5c11", border: "1px solid #e05c5c33", borderRadius: 6, fontSize: 11, color: "#e05c5c" }}>
            {results.error}
          </div>
        )}

        {results?.rows?.length > 0 && (
          <div style={{ overflow: "auto", border: "1px solid #1a1410", borderRadius: 6 }}>
            <table style={{ width: "100%", borderCollapse: "collapse", fontFamily: "'DM Mono',monospace", fontSize: 11 }}>
              <thead>
                <tr style={{ borderBottom: "1px solid #2a2420" }}>
                  {results.columns.map((col, i) => (
                    <th key={i} style={{ padding: "8px 14px", textAlign: "left", fontSize: 9, color: "#6a5e50", letterSpacing: 1.5, fontWeight: 400 }}>
                      {col.toUpperCase()}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {results.rows.map((row, ri) => (
                  <tr key={ri} style={{ borderBottom: "1px solid #1a141011" }}>
                    {row.map((cell, ci) => (
                      <td key={ci} style={{ padding: "7px 14px", color: "#9a8870", maxWidth: 240, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {String(cell ?? "")}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            <div style={{ padding: "6px 14px", borderTop: "1px solid #1a1410", fontSize: 9, color: "#3a3020", textAlign: "right" }}>
              {results.rows.length} row{results.rows.length !== 1 ? "s" : ""}
              {!_backendUrl && "  ·  demo data"}
            </div>
          </div>
        )}

        {results && results.rows?.length === 0 && !results.error && (
          <div style={{ padding: "32px", textAlign: "center", fontSize: 11, color: "#3a3020" }}>
            No results. Try a different question.
          </div>
        )}

        {history.length > 0 && (
          <div style={{ marginTop: 20 }}>
            <div style={{ fontSize: 9, letterSpacing: 2, color: "#3a3020", marginBottom: 8 }}>HISTORY</div>
            {history.map((h, i) => (
              <button key={i} onClick={() => { setQuestion(h); runQuery(h); }} style={{
                display: "block", width: "100%", textAlign: "left", padding: "5px 10px",
                background: "transparent", border: "none", color: "#4a3e30",
                fontSize: 10, fontFamily: "inherit", cursor: "pointer", letterSpacing: 0.3,
              }}>
                ↺ {h}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Root ──────────────────────────────────────────────────────────
export default function AXMChatGlassOnion() {
  const [mode, setMode] = useState(null);
  const [shards, setShards] = useState(DEMO_SHARDS);
  const [serverOk, setServerOk] = useState(false);
  const [demoMode, setDemoMode] = useState(true);

  // Auto-detect server on mount and periodically
  useEffect(() => {
    const check = async () => {
      try {
        const res = await fetch(`${SERVER_URL}/health`, { signal: AbortSignal.timeout(3000) });
        if (res.ok) {
          _backendUrl = SERVER_URL;
          setServerOk(true);
          setDemoMode(false);
          // Load real shards
          try {
            const data = await fetch(`${SERVER_URL}/shards`).then(r => r.json());
            if (data.shards?.length > 0) setShards(data.shards);
          } catch {}
        } else {
          _backendUrl = null;
          setServerOk(false);
          setDemoMode(true);
        }
      } catch {
        _backendUrl = null;
        setServerOk(false);
        setDemoMode(true);
      }
    };
    check();
    const interval = setInterval(check, 10000);
    return () => clearInterval(interval);
  }, []);

  const loadShards = useCallback(async () => {
    if (!_backendUrl) return;
    try {
      const data = await fetch(`${_backendUrl}/shards`).then(r => r.json());
      setShards(data.shards || []);
    } catch {}
  }, []);

  const modeColor = { import: "#c8a84b", distill: "#5a9060", query: "#4a8aff" }[mode];

  return (
    <>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=DM+Mono:wght@300;400;500&family=Spectral:ital,wght@0,300;0,400;1,300&display=swap');
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0a0806; }
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #2a2420; border-radius: 2px; }
        select option { background: #1a1410; color: #e8dcc8; }
        @keyframes cpulse { 0%,100%{opacity:0.5;transform:scale(1);}50%{opacity:1;transform:scale(1.05);} }
        @keyframes cfadeUp { from{opacity:0;transform:translateY(8px);}to{opacity:1;transform:translateY(0);} }
        .cpanel { animation: cfadeUp 0.25s ease both; }
      `}</style>

      <div style={{
        width: "100vw", height: "100vh", display: "flex", flexDirection: "column",
        background: "#0a0806", color: "#e8dcc8", fontFamily: "'DM Mono',monospace", overflow: "hidden",
      }}>
        {/* Header */}
        <div style={{ padding: "12px 28px", borderBottom: "1px solid #1a1410", display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14 }}>
            <span style={{ fontFamily: "'Spectral',serif", fontSize: 13, letterSpacing: "0.1em", color: "#8a7a60" }}>AXM</span>
            <span style={{ fontSize: 9, letterSpacing: "0.18em", color: "#3a3020" }}>CHAT SPOKE</span>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
            {mode && (
              <div style={{ display: "flex", alignItems: "center", gap: 7, animation: "cfadeUp 0.2s ease" }}>
                <div style={{ width: 5, height: 5, borderRadius: "50%", background: modeColor }} />
                <span style={{ fontSize: 9, color: modeColor, letterSpacing: 1, textTransform: "uppercase" }}>{mode} mode</span>
              </div>
            )}
            {mode && (
              <button onClick={() => setMode(null)} style={{ padding: "3px 10px", background: "transparent", border: "1px solid #2a2420", borderRadius: 3, color: "#6a5e50", cursor: "pointer", fontSize: 9, fontFamily: "inherit", letterSpacing: 1 }}>
                ← ONION
              </button>
            )}
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <div style={{ width: 5, height: 5, borderRadius: "50%", background: serverOk ? "#5a9060" : "#e05c5c" }} />
              <span style={{ fontSize: 9, color: serverOk ? "#5a9060" : "#e05c5c" }}>
                {!_backendUrl ? "demo" : serverOk ? "connected" : "offline"}
              </span>
            </div>
          </div>
        </div>

        {/* Main */}
        <div style={{ flex: 1, display: "flex", overflow: "hidden" }}>
          {/* Onion column */}
          <div style={{
            width: mode ? 270 : "100%", minWidth: mode ? 270 : undefined,
            borderRight: mode ? "1px solid #1a1410" : "none",
            display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center",
            padding: mode ? 20 : 32, transition: "width 0.35s cubic-bezier(0.4,0,0.2,1)",
            flexShrink: 0, overflow: "hidden",
          }}>
            <GlassOnion mode={mode} onModeChange={setMode} shardCount={shards.length} />

            {!mode && (
              <div style={{ marginTop: 24, width: "100%", maxWidth: 280, animation: "cfadeUp 0.3s ease" }}>
                {[
                  { m: "import",  ring: "Spoke ring",  key: "1", color: "#c8a84b", desc: "ingest conversation exports" },
                  { m: "distill", ring: "Core ring",   key: "2", color: "#5a9060", desc: "extract decision claims" },
                  { m: "query",   ring: "Kernel",      key: "3", color: "#4a8aff", desc: "ask your knowledge base" },
                ].map(item => (
                  <button key={item.m} onClick={() => setMode(item.m)} style={{
                    display: "flex", alignItems: "center", gap: 10, width: "100%", marginBottom: 6,
                    padding: "9px 14px", background: "#120e0a", border: "1px solid #2a2420",
                    borderRadius: 5, cursor: "pointer", transition: "all 0.15s", textAlign: "left",
                  }}>
                    <div style={{ width: 5, height: 5, borderRadius: "50%", background: item.color, flexShrink: 0 }} />
                    <div style={{ flex: 1 }}>
                      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline" }}>
                        <span style={{ fontSize: 10, color: item.color, textTransform: "capitalize" }}>{item.m} Mode</span>
                        <span style={{ fontSize: 9, color: "#2a2420" }}>⌘{item.key}</span>
                      </div>
                      <div style={{ fontSize: 9, color: "#4a3e30", marginTop: 1 }}>{item.ring} · {item.desc}</div>
                    </div>
                  </button>
                ))}
                <div style={{ marginTop: 8, padding: 11, background: "#0e0a08", borderRadius: 5, border: "1px solid #1a1410", fontSize: 9, color: "#3a3020", lineHeight: 1.7 }}>
                  Your AI conversations as sovereign knowledge shards.{"\n"}
                  Cryptographically signed. Locally owned. Forever queryable.
                </div>
              </div>
            )}

            {mode && (
              <div style={{ marginTop: 14, width: "100%", animation: "cfadeUp 0.2s ease" }}>
                <div style={{ fontSize: 8, color: modeColor, letterSpacing: 1.5, marginBottom: 6, textTransform: "uppercase" }}>{mode} mode</div>
                {[["import","Spoke"],["distill","Core"],["query","Kernel"]].map(([m, ring]) => (
                  <button key={m} onClick={() => setMode(m)} style={{
                    display: "block", width: "100%", marginBottom: 3, padding: "5px 10px",
                    background: mode === m ? "#1a1410" : "transparent",
                    border: `1px solid ${mode === m ? "#2a2420" : "transparent"}`,
                    borderRadius: 3,
                    color: mode === m ? modeColor : "#3a3020",
                    cursor: "pointer", fontSize: 9, fontFamily: "inherit", textAlign: "left",
                  }}>
                    {mode === m ? "▸ " : ""}{m.charAt(0).toUpperCase() + m.slice(1)} — {ring}
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Mode panel */}
          {mode && (
            <div className="cpanel" style={{
              flex: 1, overflow: "hidden", display: "flex", flexDirection: "column",
              background: mode === "query" ? "#080812" : mode === "distill" ? "#08100a" : "#0a0806",
            }}>
              {mode === "import"  && <ImportMode  onImported={() => { if (!_backendUrl) setShards(DEMO_SHARDS); else loadShards(); }} />}
              {mode === "distill" && <DistillMode shards={shards} onDistilled={() => { if (_backendUrl) loadShards(); }} />}
              {mode === "query"   && <QueryMode />}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ padding: "7px 28px", borderTop: "1px solid #1a1410", display: "flex", justifyContent: "space-between", alignItems: "center", flexShrink: 0 }}>
          <span style={{ fontSize: 9, color: "#2a2420" }}>
            axm-genesis v1.2.0 · axm-chat v0.1.0{!_backendUrl ? " · demo mode" : ""}
          </span>
          <span style={{ fontFamily: "'Spectral',serif", fontSize: 10, color: "#2a2420", fontStyle: "italic" }}>
            your conversations · your claims · your keys
          </span>
        </div>
      </div>
    </>
  );
}

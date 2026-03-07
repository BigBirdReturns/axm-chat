import { useState, useCallback, useEffect, useRef } from "react";

// ═══════════════════════════════════════════════════════════════════
// AXM Chat — Local Knowledge Interface
//
// This is the user-facing surface for the chat spoke.
// It talks to a local server (axm_server.py on port 8410)
// that wraps axm_chat.py, distill.py, and Spectra.
//
// Four modes: Import → Shards → Distill → Query
// ═══════════════════════════════════════════════════════════════════

const API = "http://localhost:8410";

const C = {
  bg: "#111113",
  surface: "#1a1a1f",
  surfaceUp: "#222228",
  border: "#2a2a32",
  borderFocus: "#4a6cf7",
  text: "#e8e8ec",
  textDim: "#8888a0",
  textMuted: "#55556a",
  accent: "#4a6cf7",
  accentDim: "rgba(74, 108, 247, 0.1)",
  green: "#34d399",
  greenDim: "rgba(52, 211, 153, 0.1)",
  amber: "#fbbf24",
  amberDim: "rgba(251, 191, 36, 0.1)",
  red: "#f87171",
  redDim: "rgba(248, 113, 113, 0.1)",
  cyan: "#22d3ee",
};

const font = {
  ui: "'IBM Plex Sans', -apple-system, sans-serif",
  mono: "'IBM Plex Mono', 'Menlo', monospace",
};

// ─── API helpers ─────────────────────────────────────────────

async function api(path, opts = {}) {
  try {
    const res = await fetch(`${API}${path}`, {
      ...opts,
      headers: { "Content-Type": "application/json", ...opts.headers },
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(text || `HTTP ${res.status}`);
    }
    return await res.json();
  } catch (e) {
    if (e.message.includes("Failed to fetch") || e.message.includes("NetworkError")) {
      throw new Error("Server not running. Start: python axm_server.py");
    }
    throw e;
  }
}

// ─── Components ──────────────────────────────────────────────

const Nav = ({ active, onChange, serverOk, shardCount }) => {
  const tabs = [
    { id: "import", label: "Import", icon: "↓" },
    { id: "shards", label: `Shards${shardCount > 0 ? ` (${shardCount})` : ""}`, icon: "◈" },
    { id: "distill", label: "Distill", icon: "◇" },
    { id: "query", label: "Query", icon: "?" },
  ];

  return (
    <nav style={{
      display: "flex",
      alignItems: "center",
      padding: "0 24px",
      height: "52px",
      borderBottom: `1px solid ${C.border}`,
      background: C.surface,
      gap: "4px",
    }}>
      <span style={{
        fontFamily: font.mono,
        fontSize: "13px",
        fontWeight: 600,
        color: C.accent,
        marginRight: "20px",
        letterSpacing: "0.04em",
      }}>
        AXM
      </span>
      {tabs.map(t => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          style={{
            padding: "8px 14px",
            background: active === t.id ? C.accentDim : "transparent",
            color: active === t.id ? C.accent : C.textDim,
            border: "none",
            borderRadius: "6px",
            fontSize: "13px",
            fontFamily: font.ui,
            fontWeight: active === t.id ? 600 : 400,
            cursor: "pointer",
            transition: "all 0.15s",
          }}
        >
          <span style={{ marginRight: "6px", fontSize: "11px" }}>{t.icon}</span>
          {t.label}
        </button>
      ))}
      <div style={{ flex: 1 }} />
      <div style={{
        display: "flex",
        alignItems: "center",
        gap: "6px",
        fontFamily: font.mono,
        fontSize: "11px",
      }}>
        <span style={{
          width: "6px",
          height: "6px",
          borderRadius: "50%",
          background: serverOk ? C.green : C.red,
        }} />
        <span style={{ color: serverOk ? C.green : C.red }}>
          {serverOk ? "connected" : "offline"}
        </span>
      </div>
    </nav>
  );
};

const Card = ({ children, style = {} }) => (
  <div style={{
    background: C.surface,
    border: `1px solid ${C.border}`,
    borderRadius: "8px",
    padding: "20px",
    ...style,
  }}>
    {children}
  </div>
);

const Badge = ({ color, bg, children }) => (
  <span style={{
    display: "inline-block",
    padding: "2px 8px",
    borderRadius: "4px",
    fontSize: "11px",
    fontWeight: 600,
    fontFamily: font.mono,
    color,
    background: bg,
  }}>
    {children}
  </span>
);

const Btn = ({ children, onClick, primary, disabled, small, style = {} }) => (
  <button
    onClick={onClick}
    disabled={disabled}
    style={{
      padding: small ? "6px 12px" : "10px 20px",
      background: primary ? C.accent : "transparent",
      color: primary ? "#fff" : C.textDim,
      border: primary ? "none" : `1px solid ${C.border}`,
      borderRadius: "6px",
      fontSize: small ? "12px" : "13px",
      fontFamily: font.ui,
      fontWeight: 600,
      cursor: disabled ? "not-allowed" : "pointer",
      opacity: disabled ? 0.4 : 1,
      transition: "all 0.15s",
      ...style,
    }}
  >
    {children}
  </button>
);

// ─── Import View ─────────────────────────────────────────────

const ImportView = ({ onImported }) => {
  const [status, setStatus] = useState(null); // null | "importing" | "done" | "error"
  const [log, setLog] = useState([]);
  const [result, setResult] = useState(null);
  const fileRef = useRef(null);

  const handleImport = useCallback(async () => {
    const files = fileRef.current?.files;
    if (!files?.length) return;

    setStatus("importing");
    setLog([]);

    const formData = new FormData();
    for (let f of files) {
      formData.append("files", f);
    }

    try {
      const res = await fetch(`${API}/import`, {
        method: "POST",
        body: formData,
      });
      const data = await res.json();
      setResult(data);
      setStatus("done");
      setLog(data.log || []);
      if (onImported) onImported();
    } catch (e) {
      setStatus("error");
      setLog([`Error: ${e.message}`]);
    }
  }, [onImported]);

  return (
    <div>
      <h2 style={{ fontFamily: font.ui, fontSize: "20px", fontWeight: 600, color: C.text, margin: "0 0 8px" }}>
        Import Conversations
      </h2>
      <p style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, margin: "0 0 24px" }}>
        Drop your Claude or ChatGPT export files. ZIP or JSON. One shard per conversation, signed automatically.
      </p>

      <Card>
        <div style={{
          border: `2px dashed ${C.border}`,
          borderRadius: "8px",
          padding: "40px",
          textAlign: "center",
          cursor: "pointer",
          transition: "border-color 0.2s",
        }}
          onClick={() => fileRef.current?.click()}
        >
          <input
            ref={fileRef}
            type="file"
            multiple
            accept=".json,.zip"
            style={{ display: "none" }}
            onChange={() => {
              const n = fileRef.current?.files?.length || 0;
              if (n > 0) setStatus(null);
            }}
          />
          <div style={{ fontFamily: font.ui, fontSize: "15px", color: C.textDim, marginBottom: "8px" }}>
            {fileRef.current?.files?.length
              ? `${fileRef.current.files.length} file(s) selected`
              : "Click to select export files"}
          </div>
          <div style={{ fontFamily: font.mono, fontSize: "12px", color: C.textMuted }}>
            conversations.json · chatgpt_export.zip · claude_export.zip
          </div>
        </div>

        <div style={{ marginTop: "16px", display: "flex", gap: "12px", alignItems: "center" }}>
          <Btn primary onClick={handleImport} disabled={status === "importing"}>
            {status === "importing" ? "Importing..." : "Import"}
          </Btn>
          {result && (
            <span style={{ fontFamily: font.mono, fontSize: "12px", color: C.green }}>
              {result.imported} imported · {result.skipped || 0} skipped · {result.errors || 0} errors
            </span>
          )}
        </div>
      </Card>

      {log.length > 0 && (
        <Card style={{ marginTop: "12px", maxHeight: "300px", overflow: "auto" }}>
          <div style={{ fontFamily: font.mono, fontSize: "12px", color: C.textDim, lineHeight: 1.6 }}>
            {log.map((line, i) => (
              <div key={i} style={{
                color: line.includes("✓") || line.includes("imported") ? C.green
                     : line.includes("error") || line.includes("✗") ? C.red
                     : line.includes("skip") ? C.textMuted
                     : C.textDim,
              }}>
                {line}
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
};

// ─── Shards View ─────────────────────────────────────────────

const ShardsView = ({ shards, loading, onRefresh }) => (
  <div>
    <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "16px" }}>
      <h2 style={{ fontFamily: font.ui, fontSize: "20px", fontWeight: 600, color: C.text, margin: 0 }}>
        Knowledge Shards
      </h2>
      <Btn small onClick={onRefresh}>{loading ? "Loading..." : "Refresh"}</Btn>
    </div>

    {shards.length === 0 ? (
      <Card>
        <p style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, textAlign: "center", padding: "40px 0" }}>
          No shards yet. Import some conversations first.
        </p>
      </Card>
    ) : (
      <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
        {shards.map((s, i) => (
          <Card key={i} style={{ padding: "14px 20px" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <div>
                <span style={{ fontFamily: font.ui, fontSize: "14px", fontWeight: 600, color: C.text }}>
                  {s.title || s.name}
                </span>
                {s.is_decision && (
                  <Badge color={C.cyan} bg="rgba(34, 211, 238, 0.1)" style={{ marginLeft: "8px" }}>
                    DECISION
                  </Badge>
                )}
              </div>
              <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
                <span style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted }}>
                  {s.claims} claims
                </span>
                <span style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted }}>
                  {s.created?.slice(0, 10)}
                </span>
                <Badge
                  color={s.verified ? C.green : C.amber}
                  bg={s.verified ? C.greenDim : C.amberDim}
                >
                  {s.verified ? "VERIFIED" : "UNVERIFIED"}
                </Badge>
              </div>
            </div>
            {s.merkle && (
              <div style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted, marginTop: "4px" }}>
                {s.merkle.slice(0, 24)}…
              </div>
            )}
          </Card>
        ))}
      </div>
    )}
  </div>
);

// ─── Distill View ────────────────────────────────────────────

const DistillView = ({ shards, onDistilled }) => {
  const [selected, setSelected] = useState(null);
  const [model, setModel] = useState("mistral");
  const [dryRun, setDryRun] = useState(true);
  const [status, setStatus] = useState(null);
  const [decisions, setDecisions] = useState([]);
  const [error, setError] = useState(null);

  const conversationShards = shards.filter(s => !s.is_decision);

  const handleDistill = useCallback(async () => {
    if (!selected) return;
    setStatus("running");
    setDecisions([]);
    setError(null);

    try {
      const data = await api("/distill", {
        method: "POST",
        body: JSON.stringify({
          shard: selected,
          model,
          dry_run: dryRun,
        }),
      });

      setDecisions(data.decisions || []);
      setStatus(data.status);
      if (data.error) setError(data.error);
      if (data.status === "ok" && onDistilled) onDistilled();
    } catch (e) {
      setStatus("error");
      setError(e.message);
    }
  }, [selected, model, dryRun, onDistilled]);

  return (
    <div>
      <h2 style={{ fontFamily: font.ui, fontSize: "20px", fontWeight: 600, color: C.text, margin: "0 0 8px" }}>
        Distill Decisions
      </h2>
      <p style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, margin: "0 0 24px" }}>
        Extract decisions from conversation shards via local LLM. Produces a compact decision shard that supersedes the original.
      </p>

      <Card>
        <div style={{ display: "flex", gap: "16px", flexWrap: "wrap", marginBottom: "16px" }}>
          {/* Shard selector */}
          <div style={{ flex: "1 1 300px" }}>
            <label style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted, display: "block", marginBottom: "6px" }}>
              CONVERSATION SHARD
            </label>
            <select
              value={selected || ""}
              onChange={e => setSelected(e.target.value || null)}
              style={{
                width: "100%",
                padding: "8px 12px",
                background: C.surfaceUp,
                color: C.text,
                border: `1px solid ${C.border}`,
                borderRadius: "6px",
                fontFamily: font.ui,
                fontSize: "13px",
              }}
            >
              <option value="">Select a shard...</option>
              {conversationShards.map((s, i) => (
                <option key={i} value={s.name}>{s.title || s.name} ({s.claims} claims)</option>
              ))}
            </select>
          </div>

          {/* Model */}
          <div style={{ flex: "0 0 160px" }}>
            <label style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted, display: "block", marginBottom: "6px" }}>
              MODEL
            </label>
            <select
              value={model}
              onChange={e => setModel(e.target.value)}
              style={{
                width: "100%",
                padding: "8px 12px",
                background: C.surfaceUp,
                color: C.text,
                border: `1px solid ${C.border}`,
                borderRadius: "6px",
                fontFamily: font.ui,
                fontSize: "13px",
              }}
            >
              {["mistral", "llama3", "llama3.1", "qwen2.5", "gemma2", "phi3"].map(m => (
                <option key={m} value={m}>{m}</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: "12px", alignItems: "center" }}>
          <Btn primary onClick={handleDistill} disabled={!selected || status === "running"}>
            {status === "running" ? "Extracting..." : dryRun ? "Dry Run" : "Distill & Compile"}
          </Btn>
          <label style={{
            display: "flex",
            alignItems: "center",
            gap: "6px",
            fontFamily: font.ui,
            fontSize: "13px",
            color: C.textDim,
            cursor: "pointer",
          }}>
            <input
              type="checkbox"
              checked={dryRun}
              onChange={e => setDryRun(e.target.checked)}
              style={{ accentColor: C.accent }}
            />
            Dry run (preview only)
          </label>
        </div>
      </Card>

      {error && (
        <Card style={{ marginTop: "12px", borderColor: C.red }}>
          <span style={{ fontFamily: font.mono, fontSize: "13px", color: C.red }}>{error}</span>
        </Card>
      )}

      {decisions.length > 0 && (
        <div style={{ marginTop: "16px" }}>
          <div style={{ fontFamily: font.mono, fontSize: "12px", color: C.textMuted, marginBottom: "8px" }}>
            {decisions.length} decision{decisions.length !== 1 ? "s" : ""} extracted
            {status === "dry_run" && " (preview — not compiled)"}
            {status === "ok" && " → compiled to decision shard"}
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
            {decisions.map((d, i) => (
              <Card key={i} style={{ padding: "12px 16px" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
                  <div>
                    <Badge
                      color={
                        d.predicate === "rejected" || d.predicate === "abandoned" ? C.red
                        : d.predicate === "revised" || d.predicate === "pivoted" ? C.amber
                        : C.green
                      }
                      bg={
                        d.predicate === "rejected" || d.predicate === "abandoned" ? C.redDim
                        : d.predicate === "revised" || d.predicate === "pivoted" ? C.amberDim
                        : C.greenDim
                      }
                    >
                      {d.predicate}
                    </Badge>
                    <span style={{ fontFamily: font.ui, fontSize: "14px", color: C.text, marginLeft: "10px", fontWeight: 600 }}>
                      {d.subject}
                    </span>
                    <span style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, marginLeft: "8px" }}>
                      → {d.object}
                    </span>
                  </div>
                  <span style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted, flexShrink: 0 }}>
                    {d.decided_at?.slice(0, 10) || "no date"} · {d.speaker}
                  </span>
                </div>
                {d.reasoning && (
                  <div style={{ fontFamily: font.ui, fontSize: "13px", color: C.textDim, marginTop: "6px" }}>
                    {d.reasoning}
                  </div>
                )}
                {d.alternatives && (
                  <div style={{ fontFamily: font.mono, fontSize: "11px", color: C.textMuted, marginTop: "4px" }}>
                    rejected: {d.alternatives}
                  </div>
                )}
              </Card>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

// ─── Query View ──────────────────────────────────────────────

const QueryView = () => {
  const [question, setQuestion] = useState("");
  const [results, setResults] = useState(null);
  const [sql, setSql] = useState(null);
  const [loading, setLoading] = useState(false);
  const [history, setHistory] = useState([]);

  const suggestions = [
    "what decisions have we made?",
    "what decisions conflict?",
    "timeline of all decisions",
    "what changed since february?",
    "what's stale or not reviewed?",
    "what superseded what?",
    "show all conversations",
  ];

  const runQuery = useCallback(async (q) => {
    if (!q.trim()) return;
    setLoading(true);
    setResults(null);
    setSql(null);

    try {
      const data = await api("/query", {
        method: "POST",
        body: JSON.stringify({ question: q }),
      });
      setResults(data);
      setSql(data.sql);
      setHistory(prev => [q, ...prev.filter(h => h !== q).slice(0, 9)]);
    } catch (e) {
      setResults({ error: e.message, columns: [], rows: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  const handleSubmit = useCallback((e) => {
    e.preventDefault();
    runQuery(question);
  }, [question, runQuery]);

  return (
    <div>
      <h2 style={{ fontFamily: font.ui, fontSize: "20px", fontWeight: 600, color: C.text, margin: "0 0 8px" }}>
        Query Knowledge
      </h2>
      <p style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, margin: "0 0 24px" }}>
        Ask in plain English. No SQL required. Queries run locally against mounted shards.
      </p>

      <Card>
        <div style={{ display: "flex", gap: "8px" }}>
          <input
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => e.key === "Enter" && runQuery(question)}
            placeholder="what did we decide about..."
            style={{
              flex: 1,
              padding: "10px 14px",
              background: C.surfaceUp,
              color: C.text,
              border: `1px solid ${C.border}`,
              borderRadius: "6px",
              fontFamily: font.ui,
              fontSize: "14px",
              outline: "none",
            }}
          />
          <Btn primary onClick={() => runQuery(question)} disabled={loading || !question.trim()}>
            {loading ? "..." : "Ask"}
          </Btn>
        </div>

        <div style={{ display: "flex", gap: "6px", flexWrap: "wrap", marginTop: "12px" }}>
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => { setQuestion(s); runQuery(s); }}
              style={{
                padding: "4px 10px",
                background: C.surfaceUp,
                color: C.textMuted,
                border: `1px solid ${C.border}`,
                borderRadius: "4px",
                fontSize: "12px",
                fontFamily: font.ui,
                cursor: "pointer",
              }}
            >
              {s}
            </button>
          ))}
        </div>
      </Card>

      {sql && (
        <div style={{
          marginTop: "12px",
          padding: "10px 14px",
          background: "#1e1e24",
          borderRadius: "6px",
          fontFamily: font.mono,
          fontSize: "12px",
          color: C.textMuted,
          whiteSpace: "pre-wrap",
          lineHeight: 1.5,
        }}>
          {sql.trim()}
        </div>
      )}

      {results?.error && (
        <Card style={{ marginTop: "12px", borderColor: C.red }}>
          <span style={{ fontFamily: font.mono, fontSize: "13px", color: C.red }}>{results.error}</span>
        </Card>
      )}

      {results?.rows?.length > 0 && (
        <div style={{ marginTop: "12px", overflow: "auto" }}>
          <table style={{
            width: "100%",
            borderCollapse: "collapse",
            fontFamily: font.ui,
            fontSize: "13px",
          }}>
            <thead>
              <tr>
                {results.columns.map((col, i) => (
                  <th key={i} style={{
                    textAlign: "left",
                    padding: "10px 12px",
                    borderBottom: `2px solid ${C.border}`,
                    color: C.textDim,
                    fontWeight: 600,
                    fontSize: "12px",
                    fontFamily: font.mono,
                    letterSpacing: "0.03em",
                  }}>
                    {col}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {results.rows.map((row, ri) => (
                <tr key={ri}>
                  {row.map((cell, ci) => (
                    <td key={ci} style={{
                      padding: "8px 12px",
                      borderBottom: `1px solid ${C.border}`,
                      color: C.text,
                      maxWidth: "300px",
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                    }}>
                      {String(cell ?? "")}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{
            fontFamily: font.mono,
            fontSize: "11px",
            color: C.textMuted,
            marginTop: "8px",
            textAlign: "right",
          }}>
            {results.rows.length} row{results.rows.length !== 1 ? "s" : ""}
          </div>
        </div>
      )}

      {results && results.rows?.length === 0 && !results.error && (
        <Card style={{ marginTop: "12px" }}>
          <p style={{ fontFamily: font.ui, fontSize: "14px", color: C.textDim, textAlign: "center", padding: "20px" }}>
            No results. Try a different question.
          </p>
        </Card>
      )}
    </div>
  );
};

// ─── Main App ────────────────────────────────────────────────

export default function AXMChat() {
  const [view, setView] = useState("import");
  const [serverOk, setServerOk] = useState(false);
  const [shards, setShards] = useState([]);
  const [loading, setLoading] = useState(false);

  const checkServer = useCallback(async () => {
    try {
      await api("/health");
      setServerOk(true);
    } catch {
      setServerOk(false);
    }
  }, []);

  const loadShards = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api("/shards");
      setShards(data.shards || []);
    } catch {
      // server down
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkServer();
    loadShards();
    const interval = setInterval(checkServer, 10000);
    return () => clearInterval(interval);
  }, [checkServer, loadShards]);

  return (
    <div style={{
      background: C.bg,
      minHeight: "100vh",
      fontFamily: font.ui,
      color: C.text,
    }}>
      <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet" />

      <Nav
        active={view}
        onChange={setView}
        serverOk={serverOk}
        shardCount={shards.length}
      />

      <main style={{
        maxWidth: "900px",
        margin: "0 auto",
        padding: "32px 24px",
      }}>
        {!serverOk && (
          <Card style={{ marginBottom: "20px", borderColor: C.amber }}>
            <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
              <span style={{ color: C.amber, fontSize: "18px" }}>⚠</span>
              <div>
                <div style={{ fontFamily: font.ui, fontSize: "14px", fontWeight: 600, color: C.amber }}>
                  Server not connected
                </div>
                <div style={{ fontFamily: font.mono, fontSize: "12px", color: C.textDim, marginTop: "4px" }}>
                  Start the local server: <span style={{ color: C.text }}>python axm_server.py</span>
                </div>
              </div>
            </div>
          </Card>
        )}

        {view === "import" && (
          <ImportView onImported={() => { loadShards(); }} />
        )}
        {view === "shards" && (
          <ShardsView shards={shards} loading={loading} onRefresh={loadShards} />
        )}
        {view === "distill" && (
          <DistillView shards={shards} onDistilled={() => { loadShards(); }} />
        )}
        {view === "query" && (
          <QueryView />
        )}
      </main>
    </div>
  );
}

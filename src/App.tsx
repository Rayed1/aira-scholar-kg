import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import ForceGraph3D from "react-force-graph-3d";
import "./App.css";

// ── Graph types ─────────────────────────────────────────────────────────────

type NodeInfo = {
  id: string;
  label: string;
  type: "Paper" | "Author" | "Topic" | "ReferencedPaper";
  details: string;
  year?: number;
  citations?: number;
  doi?: string | null;
  url?: string | null;
  venue?: string;
  authors?: string[];
};

type EdgeInfo = {
  id: string;
  source: string;
  target: string;
  label: string;
};

type GraphResponse = {
  nodes: NodeInfo[];
  edges: EdgeInfo[];
};

type GraphSummary = {
  papers: number;
  authors: number;
  topics: number;
  citedPapers: number;
  edges: number;
};

type GraphNode = NodeInfo & {
  x?: number;
  y?: number;
};

type GraphLink = EdgeInfo & {
  source: string | GraphNode;
  target: string | GraphNode;
};

type ForceGraphData = {
  nodes: GraphNode[];
  links: GraphLink[];
};

type FilterType =
  | "All"
  | "Core"
  | "Paper"
  | "Author"
  | "Topic"
  | "ReferencedPaper";

// ── Author insight types ────────────────────────────────────────────────────

type AuthorSummaryStats = {
  h_index?: number;
  i10_index?: number;
  "2yr_mean_citedness"?: number;
  oa_percent?: number;
};

type AuthorInstitution = {
  name: string;
  country: string | null;
};

type AuthorTopic = {
  name: string;
  score: number;
};

type AuthorYearCount = {
  year: number;
  works_count: number;
  cited_by_count: number;
};

type AuthorWork = {
  title: string;
  year: number | null;
  publication_date: string | null;
  cited_by_count: number;
  doi: string | null;
  url: string | null;
  venue: string | null;
};

type AuthorInsight = {
  id: string;
  display_name: string;
  orcid: string | null;
  openalex_url: string | null;
  works_count: number;
  cited_by_count: number;
  summary_stats: AuthorSummaryStats | null;
  last_known_institutions: AuthorInstitution[];
  topics: AuthorTopic[];
  counts_by_year: AuthorYearCount[];
  recent_works: AuthorWork[];
};

// ── Paper insight types ─────────────────────────────────────────────────────

type PaperInsightAuthor = {
  name: string;
  id: string | null;
};

type PaperInsightTopic = {
  name: string;
  score: number;
};

type PaperInsightRef = {
  title: string;
  year: number | null;
  cited_by_count: number;
  doi: string | null;
  url: string | null;
  venue: string | null;
};

type FullTextResult = {
  work_id: string;
  source_url: string | null;
  text: string;
  text_length: number;
  status: "ok" | "no_pdf" | "download_error" | "extraction_error" | "missing_dependency" | "error";
};

type PaperInsight = {
  id: string;
  title: string;
  publication_year: number | null;
  publication_date: string | null;
  type: string | null;
  language: string | null;
  cited_by_count: number;
  doi: string | null;
  openalex_url: string | null;
  venue: string | null;
  is_oa: boolean;
  oa_status: string | null;
  oa_url: string | null;
  pdf_url: string | null;
  has_full_text_available: boolean;
  authors: PaperInsightAuthor[];
  topics: PaperInsightTopic[];
  abstract: string | null;
  referenced_works_count: number;
  referenced_works: PaperInsightRef[];
};

// ── Chat types ──────────────────────────────────────────────────────────────

type ChatMessage = {
  role: "user" | "assistant" | "system";
  content: string;
};

// ── Graph helpers ───────────────────────────────────────────────────────────

const getNodeColor = (type: NodeInfo["type"]) => {
  if (type === "Paper") return "#5b6cff";
  if (type === "Author") return "#10b981";
  if (type === "Topic") return "#f59e0b";
  if (type === "ReferencedPaper") return "#71717a";
  return "#ffffff";
};

const getNodeSize = (type: NodeInfo["type"]) => {
  if (type === "Paper") return 6;
  if (type === "ReferencedPaper") return 4.8;
  if (type === "Topic") return 5.2;
  return 5.2;
};

const getShortLabel = (label: string) => {
  return label.length > 28 ? `${label.slice(0, 28)}...` : label;
};

const getLinkNodeId = (node: string | GraphNode) => {
  return typeof node === "object" ? node.id : String(node);
};

// ── App ─────────────────────────────────────────────────────────────────────

function App() {
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);
  const authorFetchGen = useRef(0);
  const paperFetchGen = useRef(0);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // ── Graph state ────────────────────────────────────────────
  const [selectedNode, setSelectedNode] = useState<NodeInfo | null>(null);
  const [hoverNodeId, setHoverNodeId] = useState<string | null>(null);
  const [searchTerm, setSearchTerm] = useState("artificial intelligence");
  const [isLoading, setIsLoading] = useState(false);
  const [fromYear, setFromYear] = useState("2020");
  const [toYear, setToYear] = useState("2026");
  const [resultLimit, setResultLimit] = useState("50");
  const [graphView, setGraphView] = useState<"2D" | "3D">("2D");
  const [activeFilter, setActiveFilter] = useState<FilterType>("All");
  const [statusMessage, setStatusMessage] = useState(
    "Real OpenAlex Oulu metadata connected"
  );
  const [nodeSearchTerm, setNodeSearchTerm] = useState("");
  const [isFocusMode, setIsFocusMode] = useState(false);

  const [graphData, setGraphData] = useState<ForceGraphData>({
    nodes: [],
    links: [],
  });
  const [highlightNodes, setHighlightNodes] = useState<Set<string>>(new Set());
  const [highlightLinks, setHighlightLinks] = useState<Set<string>>(new Set());
  const [graphSummary, setGraphSummary] = useState<GraphSummary>({
    papers: 0,
    authors: 0,
    topics: 0,
    citedPapers: 0,
    edges: 0,
  });

  // ── Author insight state ───────────────────────────────────
  const [authorInsight, setAuthorInsight] = useState<AuthorInsight | null>(null);
  const [authorInsightLoading, setAuthorInsightLoading] = useState(false);
  const [authorInsightError, setAuthorInsightError] = useState<string | null>(null);

  // ── Paper insight state ────────────────────────────────────
  const [paperInsight, setPaperInsight] = useState<PaperInsight | null>(null);
  const [paperInsightLoading, setPaperInsightLoading] = useState(false);
  const [paperInsightError, setPaperInsightError] = useState<string | null>(null);
  // Full text is keyed by workId so switching papers never loses loaded text.
  const [fullTextByWorkId, setFullTextByWorkId] = useState<Record<string, string>>({});
  const [fullTextLoading, setFullTextLoading] = useState(false);
  const [fullTextStatus, setFullTextStatus] = useState<string | null>(null);

  // ── Chat state ─────────────────────────────────────────────
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [chatInput, setChatInput] = useState("");
  const [chatLoading, setChatLoading] = useState(false);
  const [isChatCollapsed, setIsChatCollapsed] = useState(false);

  // ── Derived graph data ─────────────────────────────────────

  const visibleGraphData = useMemo(() => {
    if (activeFilter === "All") return graphData;

    const visibleNodes = graphData.nodes.filter((node) => {
      if (activeFilter === "Core") return node.type !== "ReferencedPaper";
      return node.type === activeFilter;
    });
    const visibleNodeIds = new Set(visibleNodes.map((n) => n.id));
    const visibleLinks = graphData.links.filter((link) => {
      const sourceId = getLinkNodeId(link.source);
      const targetId = getLinkNodeId(link.target);
      if (activeFilter === "Core" && link.label === "CITES") return false;
      return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
    });
    return { nodes: visibleNodes, links: visibleLinks };
  }, [activeFilter, graphData]);

  const displayGraphData = useMemo((): ForceGraphData => {
    if (!isFocusMode || highlightNodes.size === 0) return visibleGraphData;
    return {
      nodes: visibleGraphData.nodes.filter((n) => highlightNodes.has(n.id)),
      links: visibleGraphData.links.filter((l) => highlightLinks.has(l.id)),
    };
  }, [isFocusMode, visibleGraphData, highlightNodes, highlightLinks]);

  const nodeSearchMatchCount = useMemo(() => {
    const q = nodeSearchTerm.trim().toLowerCase();
    if (!q) return 0;
    return visibleGraphData.nodes.filter((n) =>
      n.label.toLowerCase().includes(q)
    ).length;
  }, [nodeSearchTerm, visibleGraphData]);

  // ── Data loading ───────────────────────────────────────────

  const loadGraph = async (query: string) => {
    setIsLoading(true);
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter("All");
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    setIsFocusMode(false);
    setNodeSearchTerm("");
    setChatMessages([]);
    setChatInput("");
    setFullTextByWorkId({});
    setStatusMessage("Loading University of Oulu publication graph...");

    try {
      const params = new URLSearchParams({
        limit: resultLimit,
        search: query.trim(),
        from_year: fromYear,
        to_year: toYear,
      });
      const response = await fetch(
        `http://127.0.0.1:8000/graph/openalex/oulu?${params.toString()}`
      );
      if (!response.ok) throw new Error("Failed to fetch graph data");
      const backendGraph: GraphResponse = await response.json();

      if (backendGraph.nodes.length === 0) {
        setGraphData({ nodes: [], links: [] });
        setGraphSummary({ papers: 0, authors: 0, topics: 0, citedPapers: 0, edges: 0 });
        setStatusMessage("No results found for this topic and year range");
        return;
      }

      setGraphData({ nodes: backendGraph.nodes, links: backendGraph.edges });
      setGraphSummary({
        papers: backendGraph.nodes.filter((n) => n.type === "Paper").length,
        authors: backendGraph.nodes.filter((n) => n.type === "Author").length,
        topics: backendGraph.nodes.filter((n) => n.type === "Topic").length,
        citedPapers: backendGraph.nodes.filter((n) => n.type === "ReferencedPaper").length,
        edges: backendGraph.edges.length,
      });
      setStatusMessage(
        `Loaded ${backendGraph.nodes.length} nodes and ${backendGraph.edges.length} edges`
      );
    } catch (error) {
      console.error(error);
      setGraphData({ nodes: [], links: [] });
      setGraphSummary({ papers: 0, authors: 0, topics: 0, citedPapers: 0, edges: 0 });
      setStatusMessage("Failed to load graph data");
    } finally {
      setIsLoading(false);
    }
  };

  // ── Graph interaction ──────────────────────────────────────

  const filterGraph = (type: FilterType) => {
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter(type);
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    setIsFocusMode(false);
    window.setTimeout(() => graphRef.current?.zoomToFit(600, 10), 300);
  };

  // ── Author insight helpers ─────────────────────────────────

  const fetchAuthorInsight = (nodeId: string) => {
    const gen = ++authorFetchGen.current;
    setAuthorInsightLoading(true);
    setAuthorInsightError(null);
    setAuthorInsight(null);

    fetch(`http://127.0.0.1:8000/author/openalex/${nodeId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<AuthorInsight>;
      })
      .then((data) => {
        if (authorFetchGen.current === gen) {
          setAuthorInsight(data);
          setAuthorInsightLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (authorFetchGen.current === gen) {
          console.error("Author insight fetch failed:", err);
          setAuthorInsightError("No extended author metadata available.");
          setAuthorInsightLoading(false);
        }
      });
  };

  const clearAuthorInsight = () => {
    authorFetchGen.current += 1;
    setAuthorInsight(null);
    setAuthorInsightError(null);
    setAuthorInsightLoading(false);
  };

  // ── Paper insight helpers ──────────────────────────────────

  const fetchPaperInsight = (nodeId: string) => {
    const gen = ++paperFetchGen.current;
    setPaperInsightLoading(true);
    setPaperInsightError(null);
    setPaperInsight(null);

    fetch(`http://127.0.0.1:8000/paper/openalex/${nodeId}`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<PaperInsight>;
      })
      .then((data) => {
        if (paperFetchGen.current === gen) {
          setPaperInsight(data);
          setPaperInsightLoading(false);
        }
      })
      .catch((err: unknown) => {
        if (paperFetchGen.current === gen) {
          console.error("Paper insight fetch failed:", err);
          setPaperInsightError("No extended paper metadata available.");
          setPaperInsightLoading(false);
        }
      });
  };

  const clearPaperInsight = () => {
    paperFetchGen.current += 1;
    setPaperInsight(null);
    setPaperInsightError(null);
    setPaperInsightLoading(false);
    // fullTextByWorkId is intentionally NOT cleared — text persists per workId
    setFullTextStatus(null);
  };

  const loadFullText = (workId: string) => {
    setFullTextLoading(true);
    setFullTextStatus(null);
    fetch(`http://127.0.0.1:8000/paper/openalex/${workId}/fulltext`)
      .then((res) => {
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        return res.json() as Promise<FullTextResult>;
      })
      .then((data) => {
        if (data.status === "ok") {
          setFullTextByWorkId((prev) => ({ ...prev, [workId]: data.text }));
          setFullTextStatus(`Full text loaded · ${data.text_length.toLocaleString()} chars`);
        } else {
          setFullTextStatus(data.text);
        }
      })
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : "Could not connect to backend.";
        setFullTextStatus(msg);
      })
      .finally(() => setFullTextLoading(false));
  };

  // ── Node click ─────────────────────────────────────────────

  const handleNodeClick = (node: GraphNode) => {
    const connectedNodeIds = new Set<string>([node.id]);
    const connectedLinkIds = new Set<string>();

    visibleGraphData.links.forEach((link) => {
      const sourceId = getLinkNodeId(link.source);
      const targetId = getLinkNodeId(link.target);
      if (sourceId === node.id || targetId === node.id) {
        connectedNodeIds.add(sourceId);
        connectedNodeIds.add(targetId);
        connectedLinkIds.add(link.id);
      }
    });

    if (selectedNode && selectedNode.id !== node.id && chatMessages.length > 0) {
      setChatMessages((prev) => [
        ...prev,
        { role: "system", content: `── Selected node changed: ${node.label} (${node.type}) ──` },
      ]);
    }

    setSelectedNode(node);
    setHighlightNodes(connectedNodeIds);
    setHighlightLinks(connectedLinkIds);

    if (node.type === "Author") {
      fetchAuthorInsight(node.id);
      clearPaperInsight();
    } else if (node.type === "Paper" || node.type === "ReferencedPaper") {
      fetchPaperInsight(node.id);
      clearAuthorInsight();
    } else {
      clearAuthorInsight();
      clearPaperInsight();
    }

    if (graphView === "2D" && node.x !== undefined && node.y !== undefined) {
      graphRef.current?.centerAt(node.x, node.y, 500);
      graphRef.current?.zoom(2.3, 500);
    }
  };

  const resetHighlight = () => {
    setSelectedNode(null);
    setHoverNodeId(null);
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    setIsFocusMode(false);
    clearAuthorInsight();
    clearPaperInsight();
    if (graphView === "2D") graphRef.current?.zoomToFit(600, 10);
  };

  // ── Node search ────────────────────────────────────────────

  const findNode = () => {
    const q = nodeSearchTerm.trim().toLowerCase();
    if (!q) return;
    const match = visibleGraphData.nodes.find((n) =>
      n.label.toLowerCase().includes(q)
    );
    if (match) handleNodeClick(match);
  };

  // ── Focus view ─────────────────────────────────────────────

  const activateFocus = () => {
    if (!selectedNode || highlightNodes.size === 0) return;
    setIsFocusMode(true);
    window.setTimeout(() => graphRef.current?.zoomToFit(500, 24), 150);
  };

  const clearFocus = () => {
    setIsFocusMode(false);
    window.setTimeout(() => graphRef.current?.zoomToFit(600, 10), 150);
  };

  // ── Export ─────────────────────────────────────────────────

  const exportPng = () => {
    const canvas = document.querySelector(".graph-view canvas");
    if (!(canvas instanceof HTMLCanvasElement)) return;
    const link = document.createElement("a");
    link.href = canvas.toDataURL("image/png");
    link.download = "aira-scholar-kg.png";
    link.click();
  };

  const exportJson = () => {
    const graphJson = JSON.stringify(graphData, null, 2);
    const blob = new Blob([graphJson], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "aira-scholar-kg.json";
    link.click();
    URL.revokeObjectURL(url);
  };

  // ── Chat ───────────────────────────────────────────────────

  const suggestedQuestions = useMemo((): string[] => {
    if (!selectedNode) {
      return [
        "Summarize the current graph.",
        "What types of nodes are shown?",
        "How should I explore this graph?",
      ];
    }
    if (selectedNode.type === "Paper" || selectedNode.type === "ReferencedPaper") {
      return [
        "What is this paper about?",
        "What are the main topics?",
        "Summarize this paper metadata.",
        "Which references are shown?",
      ];
    }
    if (selectedNode.type === "Author") {
      return [
        "What are this author's research interests?",
        "Show recent publications.",
        "Summarize this author profile.",
        "What metadata is available for this author?",
      ];
    }
    return ["Summarize this node.", "What is connected to this node?"];
  }, [selectedNode]);

  const sendChat = async (question: string) => {
    const q = question.trim();
    if (!q || chatLoading) return;
    setChatMessages((prev) => [...prev, { role: "user", content: q }]);
    setChatInput("");
    setChatLoading(true);
    try {
      // chatMessages is state before the user message is appended (React async).
      // Filter out system divider messages (UI-only) and keep only the last 1 prior turn.
      const currentPaperFullText = fullTextByWorkId[selectedNode?.id ?? ""] ?? null;
      const body = {
        question: q,
        history: chatMessages
          .filter((m) => m.role === "user" || m.role === "assistant")
          .slice(-2)
          .map((m) => ({ role: m.role, content: m.content })),
        full_text: currentPaperFullText,
        selected_node: selectedNode,
        graph_summary: graphSummary,
        paper_insight: paperInsight,
        author_insight: authorInsight,
        visible_nodes: visibleGraphData.nodes.slice(0, 50).map((n) => ({
          id: n.id,
          label: n.label,
          type: n.type,
          year: n.year,
          citations: n.citations,
          venue: n.venue,
        })),
        visible_links: visibleGraphData.links.slice(0, 50).map((l) => ({
          id: l.id,
          source: getLinkNodeId(l.source),
          target: getLinkNodeId(l.target),
          label: l.label,
        })),
      };
      const res = await fetch("http://127.0.0.1:8000/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        // Try to surface the backend's error detail (FastAPI returns {"detail": "..."})
        let errMsg = `Backend error (HTTP ${res.status}). Is the server running?`;
        try {
          const errBody = (await res.json()) as { detail?: string };
          if (errBody.detail) errMsg = errBody.detail;
        } catch {
          // response body was not JSON — keep the generic HTTP status message
        }
        setChatMessages((prev) => [...prev, { role: "assistant", content: errMsg }]);
        return;
      }
      const data = (await res.json()) as { answer: string; sources_used: string[] };
      setChatMessages((prev) => [...prev, { role: "assistant", content: data.answer }]);
    } catch (err) {
      console.error("Chat error:", err);
      const msg =
        err instanceof Error
          ? `Network error: ${err.message}`
          : "Could not connect to the backend. Is it running on port 8000?";
      setChatMessages((prev) => [...prev, { role: "assistant", content: msg }]);
    } finally {
      setChatLoading(false);
    }
  };

  // ── Effects ────────────────────────────────────────────────

  useEffect(() => {
    const id = window.setTimeout(() => void loadGraph(searchTerm), 0);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply forces when the displayed data or view mode changes
  useEffect(() => {
    if (graphView !== "2D") return;
    const fg = graphRef.current;
    if (!fg || displayGraphData.nodes.length === 0) return;

    const linkForce = fg.d3Force("link") as
      | { distance?: (v: number) => void; strength?: (v: number) => void }
      | undefined;
    const chargeForce = fg.d3Force("charge") as
      | { strength?: (v: number) => void }
      | undefined;

    linkForce?.distance?.(55);
    linkForce?.strength?.(0.16);
    chargeForce?.strength?.(-45);
    fg.d3ReheatSimulation();
    window.setTimeout(() => fg.zoomToFit(700, 10), 900);
  }, [displayGraphData, graphView]);

  // Scroll chat to bottom whenever messages change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [chatMessages]);

  // ── Render ─────────────────────────────────────────────────

  // Whether any of the standard per-node metadata fields have values
  const hasNodeMeta =
    selectedNode != null &&
    (selectedNode.year != null ||
      selectedNode.citations != null ||
      selectedNode.venue != null ||
      (selectedNode.authors && selectedNode.authors.length > 0));

  return (
    <main className="app-shell">
      {/* ── Sidebar ── */}
      <aside className="sidebar">
        <div className="sidebar-brand">
          <div className="brand-icon">KG</div>
          <div>
            <h2>AIRA Scholar</h2>
            <p>Knowledge Graph Explorer</p>
          </div>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">Node Types</h3>
          <div className="legend-list">
            <div className="legend-row"><span className="dot dot-paper" /><span>Paper</span></div>
            <div className="legend-row"><span className="dot dot-author" /><span>Author</span></div>
            <div className="legend-row"><span className="dot dot-topic" /><span>Topic</span></div>
            <div className="legend-row"><span className="dot dot-cited" /><span>Cited Paper</span></div>
          </div>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">Status</h3>
          <p className="status-msg">{statusMessage}</p>
          <p className="status-source">OpenAlex · University of Oulu</p>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">Graph Summary</h3>
          <div className="summary-grid">
            <div className="summary-item">
              <span className="summary-value">{graphSummary.papers}</span>
              <span className="summary-label">Papers</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{graphSummary.authors}</span>
              <span className="summary-label">Authors</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{graphSummary.topics}</span>
              <span className="summary-label">Topics</span>
            </div>
            <div className="summary-item">
              <span className="summary-value">{graphSummary.citedPapers}</span>
              <span className="summary-label">Cited</span>
            </div>
            <div className="summary-item summary-wide">
              <span className="summary-value">{graphSummary.edges}</span>
              <span className="summary-label">Edges</span>
            </div>
          </div>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">View Filter</h3>
          <div className="filter-list">
            <button className={`filter-btn${activeFilter === "All" ? " active" : ""}`} onClick={() => filterGraph("All")}>All Nodes</button>
            <button className={`filter-btn${activeFilter === "Core" ? " active" : ""}`} onClick={() => filterGraph("Core")}>Core Graph</button>
            <button className={`filter-btn${activeFilter === "Paper" ? " active" : ""}`} onClick={() => filterGraph("Paper")}>Papers Only</button>
            <button className={`filter-btn${activeFilter === "Author" ? " active" : ""}`} onClick={() => filterGraph("Author")}>Authors Only</button>
            <button className={`filter-btn${activeFilter === "Topic" ? " active" : ""}`} onClick={() => filterGraph("Topic")}>Topics Only</button>
            <button className={`filter-btn${activeFilter === "ReferencedPaper" ? " active" : ""}`} onClick={() => filterGraph("ReferencedPaper")}>Cited Papers</button>
          </div>
        </div>

        <div className="sidebar-section">
          <h3 className="section-label">Export</h3>
          <div className="filter-list">
            <button className="export-btn" onClick={exportPng}>Export PNG</button>
            <button className="export-btn" onClick={exportJson}>Export JSON</button>
          </div>
        </div>
      </aside>

      {/* ── Main content ── */}
      <section className="content">
        <header className="topbar">
          <div className="topbar-title">
            <h1>Interactive Academic Knowledge Graph</h1>
            <p>Search University of Oulu publications · visualize papers, authors, topics, and citations</p>
          </div>

          <div className="search-row">
            <input
              className="search-input"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") void loadGraph(searchTerm); }}
              placeholder="Search publications by topic..."
            />
            <input className="year-input" value={fromYear} onChange={(e) => setFromYear(e.target.value)} placeholder="From" />
            <span className="year-sep">–</span>
            <input className="year-input" value={toYear} onChange={(e) => setToYear(e.target.value)} placeholder="To" />
            <input className="limit-input" value={resultLimit} onChange={(e) => setResultLimit(e.target.value)} placeholder="Limit" />
            <button className="search-btn" onClick={() => void loadGraph(searchTerm)} disabled={isLoading}>
              {isLoading ? "Loading…" : "Search"}
            </button>
            <div className="view-toggle">
              <button className={`toggle-btn${graphView === "2D" ? " active" : ""}`} onClick={() => setGraphView("2D")}>2D</button>
              <button className={`toggle-btn${graphView === "3D" ? " active" : ""}`} onClick={() => setGraphView("3D")}>3D</button>
            </div>
          </div>
        </header>

        <div className="workspace">
          {/* ── Graph canvas ── */}
          <div className="graph-view">
            <div className="graph-overlay-tl">
              <span>
                {isFocusMode
                  ? `Focus · ${displayGraphData.nodes.length} nodes`
                  : "OpenAlex metadata · University of Oulu"}
              </span>
            </div>

            <div className="graph-search">
              <input
                className="graph-search-input"
                value={nodeSearchTerm}
                onChange={(e) => setNodeSearchTerm(e.target.value)}
                onKeyDown={(e) => { if (e.key === "Enter") findNode(); }}
                placeholder="Find node…"
              />
              {nodeSearchTerm.trim() !== "" && (
                <span className="graph-search-count">
                  {nodeSearchMatchCount} match{nodeSearchMatchCount !== 1 ? "es" : ""}
                </span>
              )}
              <button className="graph-ctrl-btn" onClick={findNode} disabled={nodeSearchTerm.trim() === ""} title="Find first matching node">
                Find
              </button>
            </div>

            <div className="graph-overlay-br">
              <span>Drag nodes · Scroll to zoom · Click node for details</span>
            </div>

            <div className="graph-controls">
              <button className="graph-ctrl-btn" title="Fit graph to view" onClick={() => graphRef.current?.zoomToFit(600, 10)}>Fit View</button>
              <button className="graph-ctrl-btn" title="Clear selection and reset highlight" onClick={resetHighlight}>Reset</button>
              <span className="ctrl-divider" />
              {isFocusMode ? (
                <button className="graph-ctrl-btn ctrl-active" title="Exit focus view" onClick={clearFocus}>Exit Focus</button>
              ) : (
                <button className="graph-ctrl-btn" title="Focus on selected node and its neighbours" onClick={activateFocus} disabled={!selectedNode}>Focus View</button>
              )}
              <span className="ctrl-divider" />
              <button className="graph-ctrl-btn" title="Export canvas as PNG" onClick={exportPng}>PNG ↓</button>
              <button className="graph-ctrl-btn" title="Export graph data as JSON" onClick={exportJson}>JSON ↓</button>
            </div>

            {graphView === "2D" ? (
              <ForceGraph2D
                ref={graphRef}
                graphData={displayGraphData}
                nodeId="id"
                nodeLabel={(node) => (node as GraphNode).label}
                nodeVal={(node) => getNodeSize((node as GraphNode).type)}
                nodeColor={(node) => {
                  const gn = node as GraphNode;
                  if (highlightNodes.size === 0) return getNodeColor(gn.type);
                  return highlightNodes.has(gn.id) ? getNodeColor(gn.type) : "rgba(80, 80, 90, 0.2)";
                }}
                linkLabel={(link) => (link as GraphLink).label}
                linkColor={(link) => {
                  const gl = link as GraphLink;
                  if (highlightLinks.size === 0) return "rgba(156, 163, 175, 0.5)";
                  return highlightLinks.has(gl.id) ? "#ffffff" : "rgba(80, 80, 90, 0.12)";
                }}
                linkWidth={(link) => highlightLinks.has((link as GraphLink).id) ? 2.5 : 1.1}
                linkDirectionalArrowLength={(link) => highlightLinks.has((link as GraphLink).id) ? 5 : 4}
                linkDirectionalArrowRelPos={0.95}
                linkDirectionalArrowColor={(link) => {
                  const gl = link as GraphLink;
                  if (highlightLinks.size === 0) return "rgba(156, 163, 175, 0.5)";
                  return highlightLinks.has(gl.id) ? "#ffffff" : "rgba(80, 80, 90, 0.12)";
                }}
                linkCurvature={0.04}
                backgroundColor="#09090b"
                d3VelocityDecay={0.28}
                cooldownTicks={260}
                onEngineStop={() => graphRef.current?.zoomToFit(600, 10)}
                onNodeClick={(node) => handleNodeClick(node as GraphNode)}
                onNodeHover={(node) => setHoverNodeId(node ? (node as GraphNode).id : null)}
                onBackgroundClick={resetHighlight}
                nodeCanvasObject={(node, ctx, globalScale) => {
                  const gn = node as GraphNode;
                  const x = gn.x ?? 0;
                  const y = gn.y ?? 0;
                  const isSelected = selectedNode?.id === gn.id;
                  const isHovered = hoverNodeId === gn.id;
                  const isFaded = highlightNodes.size > 0 && !highlightNodes.has(gn.id);
                  const nodeSize = getNodeSize(gn.type) * 2.8;

                  ctx.save();
                  ctx.globalAlpha = isFaded ? 0.18 : 1;
                  ctx.fillStyle = getNodeColor(gn.type);
                  ctx.strokeStyle = isSelected || isHovered ? "#ffffff" : "#d4d4d8";
                  ctx.lineWidth = isSelected || isHovered ? 2.6 : 1.3;
                  ctx.beginPath();
                  ctx.arc(x, y, nodeSize, 0, 2 * Math.PI, false);
                  ctx.fill();
                  ctx.stroke();

                  if ((isSelected || isHovered) && !isFaded) {
                    const label = getShortLabel(gn.label);
                    const fontSize = Math.max(10, 14 / globalScale);
                    ctx.font = `600 ${fontSize}px Sans-Serif`;
                    ctx.textAlign = "center";
                    ctx.textBaseline = "middle";
                    ctx.fillStyle = "#ffffff";
                    ctx.fillText(label, x, y - nodeSize - 8 / globalScale);
                  }
                  ctx.restore();
                }}
                nodePointerAreaPaint={(node, color, ctx) => {
                  const gn = node as GraphNode;
                  const x = gn.x ?? 0;
                  const y = gn.y ?? 0;
                  ctx.fillStyle = color;
                  ctx.beginPath();
                  ctx.arc(x, y, gn.type === "Paper" ? 24 : 20, 0, 2 * Math.PI, false);
                  ctx.fill();
                }}
              />
            ) : (
              <ForceGraph3D
                graphData={displayGraphData}
                nodeId="id"
                nodeLabel={(node) => (node as GraphNode).label}
                nodeVal={(node) => getNodeSize((node as GraphNode).type)}
                nodeColor={(node) => {
                  const gn = node as GraphNode;
                  if (highlightNodes.size === 0) return getNodeColor(gn.type);
                  return highlightNodes.has(gn.id) ? getNodeColor(gn.type) : "#1f2937";
                }}
                linkLabel={(link) => (link as GraphLink).label}
                linkColor={(link) => {
                  const gl = link as GraphLink;
                  if (highlightLinks.size === 0) return "#4b5563";
                  return highlightLinks.has(gl.id) ? "#ffffff" : "#1f2937";
                }}
                linkWidth={(link) => highlightLinks.has((link as GraphLink).id) ? 2 : 0.7}
                linkDirectionalArrowLength={3.5}
                linkDirectionalArrowRelPos={0.95}
                backgroundColor="#09090b"
                showNavInfo={true}
                onNodeClick={(node) => handleNodeClick(node as GraphNode)}
                onNodeHover={(node) => setHoverNodeId(node ? (node as GraphNode).id : null)}
                onBackgroundClick={resetHighlight}
              />
            )}
          </div>

          {/* ── Details panel ── */}
          <aside className="details-panel">
            <h2 className="details-title">Node Details</h2>

            {selectedNode ? (
              <div className="node-card">
                <span className={`type-badge badge-${selectedNode.type.toLowerCase()}`}>
                  {selectedNode.type}
                </span>

                <h3 className="node-label">{selectedNode.label}</h3>

                {/* Standard metadata rows — hidden when all fields are absent */}
                {hasNodeMeta && (
                  <div className="meta-rows">
                    {selectedNode.year && (
                      <div className="meta-row">
                        <span className="meta-key">Year</span>
                        <span className="meta-val">{selectedNode.year}</span>
                      </div>
                    )}
                    {selectedNode.citations != null && (
                      <div className="meta-row">
                        <span className="meta-key">Citations</span>
                        <span className="meta-val">{selectedNode.citations}</span>
                      </div>
                    )}
                    {selectedNode.venue && (
                      <div className="meta-row">
                        <span className="meta-key">Venue</span>
                        <span className="meta-val">{selectedNode.venue}</span>
                      </div>
                    )}
                    {selectedNode.authors && selectedNode.authors.length > 0 && (
                      <div className="meta-row meta-row-block">
                        <span className="meta-key">Authors</span>
                        <span className="meta-val">{selectedNode.authors.join(", ")}</span>
                      </div>
                    )}
                  </div>
                )}

                {/* External links — hidden when neither doi nor url exist */}
                {(selectedNode.doi || selectedNode.url) && (
                  <div className="action-links">
                    {selectedNode.doi && (
                      <a className="action-link" href={selectedNode.doi} target="_blank" rel="noreferrer">Open DOI ↗</a>
                    )}
                    {selectedNode.url && (
                      <a className="action-link" href={selectedNode.url} target="_blank" rel="noreferrer">View on OpenAlex ↗</a>
                    )}
                  </div>
                )}

                {selectedNode.details && (
                  <p className="node-details-extra">{selectedNode.details}</p>
                )}

                {/* ── Paper Insight section ── */}
                {(selectedNode.type === "Paper" || selectedNode.type === "ReferencedPaper") && (
                  <div className="paper-insights">
                    <h4 className="ai-section-title">Paper Insights</h4>

                    {paperInsightLoading && (
                      <p className="ai-status">Loading paper data…</p>
                    )}

                    {paperInsightError && !paperInsightLoading && (
                      <p className="ai-status ai-status-error">{paperInsightError}</p>
                    )}

                    {paperInsight && !paperInsightLoading && (
                      <>
                        {/* Links row */}
                        <div className="action-links">
                          {paperInsight.openalex_url && (
                            <a className="action-link" href={paperInsight.openalex_url} target="_blank" rel="noreferrer">OpenAlex ↗</a>
                          )}
                          {paperInsight.doi && (
                            <a className="action-link" href={paperInsight.doi} target="_blank" rel="noreferrer">DOI ↗</a>
                          )}
                          {paperInsight.is_oa && paperInsight.oa_url && (
                            <a className="action-link" href={paperInsight.oa_url} target="_blank" rel="noreferrer">Open Access PDF ↗</a>
                          )}
                        </div>

                        {/* OA badge + stats */}
                        <div className="ai-stats">
                          <div className="ai-stat">
                            <span className="ai-stat-value">{paperInsight.cited_by_count.toLocaleString()}</span>
                            <span className="ai-stat-label">Citations</span>
                          </div>
                          {paperInsight.publication_year && (
                            <div className="ai-stat">
                              <span className="ai-stat-value">{paperInsight.publication_year}</span>
                              <span className="ai-stat-label">Year</span>
                            </div>
                          )}
                          {paperInsight.referenced_works_count > 0 && (
                            <div className="ai-stat">
                              <span className="ai-stat-value">{paperInsight.referenced_works_count}</span>
                              <span className="ai-stat-label">References</span>
                            </div>
                          )}
                        </div>

                        {/* OA status badge */}
                        {paperInsight.oa_status && (
                          <div>
                            <span className={`pi-oa-badge${paperInsight.is_oa ? " pi-oa-open" : " pi-oa-closed"}`}>
                              {paperInsight.is_oa ? "Open Access" : "Closed Access"}
                              {paperInsight.oa_status !== "closed" && ` · ${paperInsight.oa_status}`}
                            </span>
                          </div>
                        )}

                        {/* Full-text loader */}
                        {paperInsight.has_full_text_available && (() => {
                          const currentFT = fullTextByWorkId[paperInsight.id] ?? null;
                          return (
                            <div className="pi-fulltext-section">
                              {!currentFT ? (
                                <button
                                  className="pi-fulltext-btn"
                                  disabled={fullTextLoading}
                                  onClick={() => loadFullText(paperInsight.id)}
                                >
                                  {fullTextLoading ? "Loading full text…" : "Load Full Text"}
                                </button>
                              ) : (
                                <span className="pi-fulltext-loaded">✓ Full text loaded</span>
                              )}
                              {fullTextStatus && (
                                <p className={`pi-fulltext-status${currentFT ? " pi-fulltext-ok" : " pi-fulltext-err"}`}>
                                  {fullTextStatus}
                                </p>
                              )}
                            </div>
                          );
                        })()}

                        {/* Venue */}
                        {paperInsight.venue && (
                          <div className="ai-block">
                            <span className="ai-block-label">Venue</span>
                            <span className="ai-block-val">{paperInsight.venue}</span>
                          </div>
                        )}

                        {/* Authors */}
                        {paperInsight.authors.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">Authors</span>
                            <span className="ai-block-val">
                              {paperInsight.authors.map((a) => a.name).join(", ")}
                            </span>
                          </div>
                        )}

                        {/* Topics */}
                        {paperInsight.topics.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">Research Topics</span>
                            <div className="ai-topics">
                              {paperInsight.topics.map((t, i) => (
                                <span key={i} className="ai-topic-badge">{t.name}</span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Abstract */}
                        {paperInsight.abstract && (
                          <div className="ai-block">
                            <span className="ai-block-label">Abstract</span>
                            <p className="pi-abstract">{paperInsight.abstract}</p>
                          </div>
                        )}

                        {/* Referenced works */}
                        {paperInsight.referenced_works.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">
                              References preview ({paperInsight.referenced_works.length} of {paperInsight.referenced_works_count})
                            </span>
                            <ol className="ai-work-list">
                              {paperInsight.referenced_works.map((w, i) => (
                                <li key={i} className="ai-work-item">
                                  <p className="ai-work-title">{w.title}</p>
                                  <div className="ai-work-meta">
                                    {w.year && <span>{w.year}</span>}
                                    <span>{w.cited_by_count} cited</span>
                                    {w.venue && <span>{w.venue}</span>}
                                  </div>
                                  {(w.doi || w.url) && (
                                    <div className="ai-work-links">
                                      {w.doi && (
                                        <a className="ai-work-link" href={w.doi} target="_blank" rel="noreferrer">DOI ↗</a>
                                      )}
                                      {w.url && (
                                        <a className="ai-work-link" href={w.url} target="_blank" rel="noreferrer">OA ↗</a>
                                      )}
                                    </div>
                                  )}
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}

                {/* ── Author Insight section ── */}
                {selectedNode.type === "Author" && (
                  <div className="author-insights">
                    <h4 className="ai-section-title">Author Insights</h4>

                    {authorInsightLoading && (
                      <p className="ai-status">Loading author data…</p>
                    )}

                    {authorInsightError && !authorInsightLoading && (
                      <p className="ai-status ai-status-error">{authorInsightError}</p>
                    )}

                    {authorInsight && !authorInsightLoading && (
                      <>
                        {/* Profile links */}
                        <div className="action-links">
                          {authorInsight.openalex_url && (
                            <a className="action-link" href={authorInsight.openalex_url} target="_blank" rel="noreferrer">
                              OpenAlex Profile ↗
                            </a>
                          )}
                          {authorInsight.orcid && (
                            <a className="action-link" href={authorInsight.orcid} target="_blank" rel="noreferrer">
                              ORCID ↗
                            </a>
                          )}
                        </div>

                        {/* Stats row */}
                        <div className="ai-stats">
                          <div className="ai-stat">
                            <span className="ai-stat-value">{authorInsight.works_count.toLocaleString()}</span>
                            <span className="ai-stat-label">Works</span>
                          </div>
                          <div className="ai-stat">
                            <span className="ai-stat-value">{authorInsight.cited_by_count.toLocaleString()}</span>
                            <span className="ai-stat-label">Citations</span>
                          </div>
                          {authorInsight.summary_stats?.h_index != null && (
                            <div className="ai-stat">
                              <span className="ai-stat-value">{authorInsight.summary_stats.h_index}</span>
                              <span className="ai-stat-label">h-index</span>
                            </div>
                          )}
                        </div>

                        {/* Affiliation */}
                        {authorInsight.last_known_institutions.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">Affiliation</span>
                            <span className="ai-block-val">
                              {authorInsight.last_known_institutions[0].name}
                              {authorInsight.last_known_institutions[0].country &&
                                ` · ${authorInsight.last_known_institutions[0].country}`}
                            </span>
                          </div>
                        )}

                        {/* Research topics */}
                        {authorInsight.topics.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">Research Topics</span>
                            <div className="ai-topics">
                              {authorInsight.topics.map((t, i) => (
                                <span key={i} className="ai-topic-badge">{t.name}</span>
                              ))}
                            </div>
                          </div>
                        )}

                        {/* Recent publications */}
                        {authorInsight.recent_works.length > 0 && (
                          <div className="ai-block">
                            <span className="ai-block-label">Recent Publications</span>
                            <ol className="ai-work-list">
                              {authorInsight.recent_works.map((w, i) => (
                                <li key={i} className="ai-work-item">
                                  <p className="ai-work-title">{w.title}</p>
                                  <div className="ai-work-meta">
                                    {w.year && <span>{w.year}</span>}
                                    <span>{w.cited_by_count} cited</span>
                                    {w.venue && <span>{w.venue}</span>}
                                  </div>
                                  {(w.doi || w.url) && (
                                    <div className="ai-work-links">
                                      {w.doi && (
                                        <a className="ai-work-link" href={w.doi} target="_blank" rel="noreferrer">DOI ↗</a>
                                      )}
                                      {w.url && (
                                        <a className="ai-work-link" href={w.url} target="_blank" rel="noreferrer">OA ↗</a>
                                      )}
                                    </div>
                                  )}
                                </li>
                              ))}
                            </ol>
                          </div>
                        )}
                      </>
                    )}
                  </div>
                )}
              </div>
            ) : (
              <div className="empty-state">
                <div className="empty-visual">
                  <span className="ev-dot ev-blue" />
                  <span className="ev-dot ev-green" />
                  <span className="ev-dot ev-amber" />
                </div>
                <p className="empty-heading">Select a node</p>
                <ul className="empty-hints">
                  <li>Click a paper, author, or topic node</li>
                  <li>Use the filter panel to narrow the graph</li>
                  <li>Hover over any node to see its label</li>
                </ul>
              </div>
            )}
            {/* ── AIRA Assistant ── */}
            <div className="aira-chat">
              <div className="chat-header" onClick={() => setIsChatCollapsed((c) => !c)}>
                <span className="chat-header-title">AIRA Assistant</span>
                <button className="chat-toggle-btn" aria-label="Toggle chat">
                  {isChatCollapsed ? "▲" : "▼"}
                </button>
              </div>

              {!isChatCollapsed && (
                <>
                  <div className="chat-suggestions">
                    {suggestedQuestions.map((q, i) => (
                      <button
                        key={i}
                        className="chat-suggestion-btn"
                        onClick={() => void sendChat(q)}
                        disabled={chatLoading}
                      >
                        {q}
                      </button>
                    ))}
                  </div>

                  <div className="chat-messages">
                    {chatMessages.map((msg, i) => (
                      <div key={i} className={`chat-message chat-message-${msg.role}`}>
                        {msg.content}
                      </div>
                    ))}
                    {chatLoading && (
                      <div className="chat-message chat-message-assistant">
                        <span className="chat-thinking">Thinking…</span>
                      </div>
                    )}
                    <div ref={chatEndRef} />
                  </div>

                  <div className="chat-input-row">
                    <input
                      className="chat-input"
                      value={chatInput}
                      onChange={(e) => setChatInput(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !chatLoading) void sendChat(chatInput);
                      }}
                      placeholder="Ask about this node or the graph…"
                      disabled={chatLoading}
                    />
                    <button
                      className="chat-send-btn"
                      onClick={() => void sendChat(chatInput)}
                      disabled={chatLoading || chatInput.trim() === ""}
                    >
                      Send
                    </button>
                  </div>
                </>
              )}
            </div>
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;

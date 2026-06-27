import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
import ForceGraph3D from "react-force-graph-3d";
import "./App.css";

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

function App() {
  const graphRef = useRef<ForceGraphMethods | undefined>(undefined);

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

  // ── New feature state ──────────────────────────────────────
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

  // ── Derived graph data ──────────────────────────────────────

  // Filter by active type filter (unchanged logic)
  const visibleGraphData = useMemo(() => {
    if (activeFilter === "All") return graphData;

    const visibleNodes = graphData.nodes.filter((node) => {
      if (activeFilter === "Core") return node.type !== "ReferencedPaper";
      return node.type === activeFilter;
    });

    const visibleNodeIds = new Set(visibleNodes.map((node) => node.id));

    const visibleLinks = graphData.links.filter((link) => {
      const sourceId = getLinkNodeId(link.source);
      const targetId = getLinkNodeId(link.target);
      if (activeFilter === "Core" && link.label === "CITES") return false;
      return visibleNodeIds.has(sourceId) && visibleNodeIds.has(targetId);
    });

    return { nodes: visibleNodes, links: visibleLinks };
  }, [activeFilter, graphData]);

  // Focus mode: restrict to selected node + direct neighbourhood
  const displayGraphData = useMemo((): ForceGraphData => {
    if (!isFocusMode || highlightNodes.size === 0) return visibleGraphData;
    return {
      nodes: visibleGraphData.nodes.filter((n) => highlightNodes.has(n.id)),
      links: visibleGraphData.links.filter((l) => highlightLinks.has(l.id)),
    };
  }, [isFocusMode, visibleGraphData, highlightNodes, highlightLinks]);

  // Count how many visible nodes match the node-search query
  const nodeSearchMatchCount = useMemo(() => {
    const q = nodeSearchTerm.trim().toLowerCase();
    if (!q) return 0;
    return visibleGraphData.nodes.filter((n) =>
      n.label.toLowerCase().includes(q)
    ).length;
  }, [nodeSearchTerm, visibleGraphData]);

  // ── Data loading ────────────────────────────────────────────

  const loadGraph = async (query: string) => {
    setIsLoading(true);
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter("All");
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    setIsFocusMode(false);
    setNodeSearchTerm("");
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

  // ── Graph interaction ───────────────────────────────────────

  const filterGraph = (type: FilterType) => {
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter(type);
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    setIsFocusMode(false);
    window.setTimeout(() => graphRef.current?.zoomToFit(600, 10), 300);
  };

  const handleNodeClick = (node: GraphNode) => {
    const connectedNodeIds = new Set<string>([node.id]);
    const connectedLinkIds = new Set<string>();

    // Always compute neighbourhood from the full visible graph, not the
    // potentially-restricted focused view, so the neighbourhood set is correct.
    visibleGraphData.links.forEach((link) => {
      const sourceId = getLinkNodeId(link.source);
      const targetId = getLinkNodeId(link.target);
      if (sourceId === node.id || targetId === node.id) {
        connectedNodeIds.add(sourceId);
        connectedNodeIds.add(targetId);
        connectedLinkIds.add(link.id);
      }
    });

    setSelectedNode(node);
    setHighlightNodes(connectedNodeIds);
    setHighlightLinks(connectedLinkIds);

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
    if (graphView === "2D") graphRef.current?.zoomToFit(600, 10);
  };

  // ── Feature 1: Find node ────────────────────────────────────

  const findNode = () => {
    const q = nodeSearchTerm.trim().toLowerCase();
    if (!q) return;
    const match = visibleGraphData.nodes.find((n) =>
      n.label.toLowerCase().includes(q)
    );
    if (match) handleNodeClick(match);
  };

  // ── Feature 2: Focus view ───────────────────────────────────

  const activateFocus = () => {
    if (!selectedNode || highlightNodes.size === 0) return;
    setIsFocusMode(true);
    window.setTimeout(() => graphRef.current?.zoomToFit(500, 24), 150);
  };

  const clearFocus = () => {
    setIsFocusMode(false);
    window.setTimeout(() => graphRef.current?.zoomToFit(600, 10), 150);
  };

  // ── Export ──────────────────────────────────────────────────

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

  // ── Effects ─────────────────────────────────────────────────

  useEffect(() => {
    const timeoutId = window.setTimeout(() => void loadGraph(searchTerm), 0);
    return () => window.clearTimeout(timeoutId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Re-apply force parameters whenever the *displayed* graph changes (covers
  // both type-filter changes and focus-mode toggling).
  useEffect(() => {
    if (graphView !== "2D") return;
    const forceGraph = graphRef.current;
    if (!forceGraph || displayGraphData.nodes.length === 0) return;

    const linkForce = forceGraph.d3Force("link") as
      | { distance?: (v: number) => void; strength?: (v: number) => void }
      | undefined;
    const chargeForce = forceGraph.d3Force("charge") as
      | { strength?: (v: number) => void }
      | undefined;

    linkForce?.distance?.(55);
    linkForce?.strength?.(0.16);
    chargeForce?.strength?.(-45);
    forceGraph.d3ReheatSimulation();
    window.setTimeout(() => forceGraph.zoomToFit(700, 10), 900);
  }, [displayGraphData, graphView]);

  // ── Render ──────────────────────────────────────────────────

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
            <input
              className="year-input"
              value={fromYear}
              onChange={(e) => setFromYear(e.target.value)}
              placeholder="From"
            />
            <span className="year-sep">–</span>
            <input
              className="year-input"
              value={toYear}
              onChange={(e) => setToYear(e.target.value)}
              placeholder="To"
            />
            <input
              className="limit-input"
              value={resultLimit}
              onChange={(e) => setResultLimit(e.target.value)}
              placeholder="Limit"
            />
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

            {/* Top-left: source chip */}
            <div className="graph-overlay-tl">
              <span>
                {isFocusMode
                  ? `Focus · ${displayGraphData.nodes.length} nodes`
                  : "OpenAlex metadata · University of Oulu"}
              </span>
            </div>

            {/* Top-right: node search */}
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
              <button
                className="graph-ctrl-btn"
                onClick={findNode}
                disabled={nodeSearchTerm.trim() === ""}
                title="Find first matching node"
              >
                Find
              </button>
            </div>

            {/* Bottom-right: hint */}
            <div className="graph-overlay-br">
              <span>Drag nodes · Scroll to zoom · Click node for details</span>
            </div>

            {/* Bottom-left: graph controls */}
            <div className="graph-controls">
              <button
                className="graph-ctrl-btn"
                title="Fit graph to view"
                onClick={() => graphRef.current?.zoomToFit(600, 10)}
              >
                Fit View
              </button>
              <button
                className="graph-ctrl-btn"
                title="Clear selection and reset highlight"
                onClick={resetHighlight}
              >
                Reset
              </button>

              <span className="ctrl-divider" />

              {isFocusMode ? (
                <button
                  className="graph-ctrl-btn ctrl-active"
                  title="Exit focus view and restore full graph"
                  onClick={clearFocus}
                >
                  Exit Focus
                </button>
              ) : (
                <button
                  className="graph-ctrl-btn"
                  title="Focus on selected node and its direct neighbours"
                  onClick={activateFocus}
                  disabled={!selectedNode}
                >
                  Focus View
                </button>
              )}

              <span className="ctrl-divider" />

              <button
                className="graph-ctrl-btn"
                title="Export canvas as PNG"
                onClick={exportPng}
              >
                PNG ↓
              </button>
              <button
                className="graph-ctrl-btn"
                title="Export graph data as JSON"
                onClick={exportJson}
              >
                JSON ↓
              </button>
            </div>

            {graphView === "2D" ? (
              <ForceGraph2D
                ref={graphRef}
                graphData={displayGraphData}
                nodeId="id"
                nodeLabel={(node) => (node as GraphNode).label}
                nodeVal={(node) => getNodeSize((node as GraphNode).type)}
                nodeColor={(node) => {
                  const graphNode = node as GraphNode;
                  if (highlightNodes.size === 0) return getNodeColor(graphNode.type);
                  return highlightNodes.has(graphNode.id)
                    ? getNodeColor(graphNode.type)
                    : "rgba(80, 80, 90, 0.2)";
                }}
                linkLabel={(link) => (link as GraphLink).label}
                linkColor={(link) => {
                  const graphLink = link as GraphLink;
                  if (highlightLinks.size === 0) return "rgba(156, 163, 175, 0.5)";
                  return highlightLinks.has(graphLink.id) ? "#ffffff" : "rgba(80, 80, 90, 0.12)";
                }}
                linkWidth={(link) => highlightLinks.has((link as GraphLink).id) ? 2.5 : 1.1}
                linkDirectionalArrowLength={(link) => highlightLinks.has((link as GraphLink).id) ? 5 : 4}
                linkDirectionalArrowRelPos={0.95}
                linkDirectionalArrowColor={(link) => {
                  const graphLink = link as GraphLink;
                  if (highlightLinks.size === 0) return "rgba(156, 163, 175, 0.5)";
                  return highlightLinks.has(graphLink.id) ? "#ffffff" : "rgba(80, 80, 90, 0.12)";
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
                  const graphNode = node as GraphNode;
                  const x = graphNode.x ?? 0;
                  const y = graphNode.y ?? 0;
                  const isSelected = selectedNode?.id === graphNode.id;
                  const isHovered = hoverNodeId === graphNode.id;
                  const isFaded = highlightNodes.size > 0 && !highlightNodes.has(graphNode.id);
                  const nodeSize = getNodeSize(graphNode.type) * 2.8;

                  ctx.save();
                  ctx.globalAlpha = isFaded ? 0.18 : 1;
                  ctx.fillStyle = getNodeColor(graphNode.type);
                  ctx.strokeStyle = isSelected || isHovered ? "#ffffff" : "#d4d4d8";
                  ctx.lineWidth = isSelected || isHovered ? 2.6 : 1.3;
                  ctx.beginPath();
                  ctx.arc(x, y, nodeSize, 0, 2 * Math.PI, false);
                  ctx.fill();
                  ctx.stroke();

                  if ((isSelected || isHovered) && !isFaded) {
                    const label = getShortLabel(graphNode.label);
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
                  const graphNode = node as GraphNode;
                  const x = graphNode.x ?? 0;
                  const y = graphNode.y ?? 0;
                  const size = graphNode.type === "Paper" ? 24 : 20;
                  ctx.fillStyle = color;
                  ctx.beginPath();
                  ctx.arc(x, y, size, 0, 2 * Math.PI, false);
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
                  const graphNode = node as GraphNode;
                  if (highlightNodes.size === 0) return getNodeColor(graphNode.type);
                  return highlightNodes.has(graphNode.id) ? getNodeColor(graphNode.type) : "#1f2937";
                }}
                linkLabel={(link) => (link as GraphLink).label}
                linkColor={(link) => {
                  const graphLink = link as GraphLink;
                  if (highlightLinks.size === 0) return "#4b5563";
                  return highlightLinks.has(graphLink.id) ? "#ffffff" : "#1f2937";
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

                <div className="meta-rows">
                  {selectedNode.year && (
                    <div className="meta-row">
                      <span className="meta-key">Year</span>
                      <span className="meta-val">{selectedNode.year}</span>
                    </div>
                  )}
                  {selectedNode.citations !== undefined && (
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

                <div className="action-links">
                  {selectedNode.doi && (
                    <a className="action-link" href={selectedNode.doi} target="_blank" rel="noreferrer">
                      Open DOI ↗
                    </a>
                  )}
                  {selectedNode.url && (
                    <a className="action-link" href={selectedNode.url} target="_blank" rel="noreferrer">
                      View on OpenAlex ↗
                    </a>
                  )}
                </div>

                {selectedNode.details && (
                  <p className="node-details-extra">{selectedNode.details}</p>
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
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;

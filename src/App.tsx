import { useEffect, useMemo, useRef, useState } from "react";
import ForceGraph2D, { type ForceGraphMethods } from "react-force-graph-2d";
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
  const [resultLimit, setResultLimit] = useState("20");
  const [toYear, setToYear] = useState("2026");
  const [activeFilter, setActiveFilter] = useState<FilterType>("All");
  const [statusMessage, setStatusMessage] = useState(
    "Real OpenAlex Oulu metadata connected"
  );

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

    return {
      nodes: visibleNodes,
      links: visibleLinks,
    };
  }, [activeFilter, graphData]);

  const loadGraph = async (query: string) => {
    setIsLoading(true);
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter("All");
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
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

      if (!response.ok) {
        throw new Error("Failed to fetch graph data");
      }

      const backendGraph: GraphResponse = await response.json();

      if (backendGraph.nodes.length === 0) {
        setGraphData({ nodes: [], links: [] });
        setGraphSummary({
          papers: 0,
          authors: 0,
          topics: 0,
          citedPapers: 0,
          edges: 0,
        });
        setStatusMessage("No results found for this topic and year range");
        return;
      }

      setGraphData({
        nodes: backendGraph.nodes,
        links: backendGraph.edges,
      });

      setGraphSummary({
        papers: backendGraph.nodes.filter((node) => node.type === "Paper")
          .length,
        authors: backendGraph.nodes.filter((node) => node.type === "Author")
          .length,
        topics: backendGraph.nodes.filter((node) => node.type === "Topic")
          .length,
        citedPapers: backendGraph.nodes.filter(
          (node) => node.type === "ReferencedPaper"
        ).length,
        edges: backendGraph.edges.length,
      });

      setStatusMessage(
        `Loaded ${backendGraph.nodes.length} nodes and ${backendGraph.edges.length} edges`
      );
    } catch (error) {
      console.error(error);
      setGraphData({ nodes: [], links: [] });
      setGraphSummary({
        papers: 0,
        authors: 0,
        topics: 0,
        citedPapers: 0,
        edges: 0,
      });
      setStatusMessage("Failed to load graph data");
    } finally {
      setIsLoading(false);
    }
  };

  const filterGraph = (type: FilterType) => {
    setSelectedNode(null);
    setHoverNodeId(null);
    setActiveFilter(type);
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());

    window.setTimeout(() => {
      graphRef.current?.zoomToFit(600, 90);
    }, 300);
  };

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

    setSelectedNode(node);
    setHighlightNodes(connectedNodeIds);
    setHighlightLinks(connectedLinkIds);

    if (node.x !== undefined && node.y !== undefined) {
      graphRef.current?.centerAt(node.x, node.y, 500);
      graphRef.current?.zoom(2.3, 500);
    }
  };

  const resetHighlight = () => {
    setSelectedNode(null);
    setHoverNodeId(null);
    setHighlightNodes(new Set());
    setHighlightLinks(new Set());
    graphRef.current?.zoomToFit(600, 90);
  };

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

  useEffect(() => {
  const timeoutId = window.setTimeout(() => {
    void loadGraph(searchTerm);
  }, 0);

  return () => {
    window.clearTimeout(timeoutId);
  };

  // Run once on initial page load.
  // eslint-disable-next-line react-hooks/exhaustive-deps
}, []);

  useEffect(() => {
    const forceGraph = graphRef.current;

    if (!forceGraph || visibleGraphData.nodes.length === 0) return;

    const linkForce = forceGraph.d3Force("link") as
      | {
          distance?: (value: number) => void;
          strength?: (value: number) => void;
        }
      | undefined;

    const chargeForce = forceGraph.d3Force("charge") as
      | {
          strength?: (value: number) => void;
        }
      | undefined;

    linkForce?.distance?.(55);
    linkForce?.strength?.(0.16);
    chargeForce?.strength?.(-45);

    forceGraph.d3ReheatSimulation();

    window.setTimeout(() => {
      forceGraph.zoomToFit(700, 10);
    }, 900);
  }, [visibleGraphData]);

  return (
    <main className="app-shell">
      <aside className="sidebar">
        <h2>AIRA Scholar-KG</h2>
        <p>Knowledge Graph Extension</p>

        <div className="legend">
          <h3>Node Types</h3>
          <span className="legend-item paper">Paper</span>
          <span className="legend-item author">Author</span>
          <span className="legend-item topic">Topic</span>
          <span className="legend-item referenced">Cited Paper</span>
        </div>

        <div className="status-box">
          <h3>Prototype Status</h3>
          <p>{statusMessage}</p>
          <p>Source: OpenAlex + University of Oulu filter</p>
        </div>

        <div className="status-box">
          <h3>Graph Summary</h3>
          <p>Papers: {graphSummary.papers}</p>
          <p>Authors: {graphSummary.authors}</p>
          <p>Topics: {graphSummary.topics}</p>
          <p>Cited papers: {graphSummary.citedPapers}</p>
          <p>Edges: {graphSummary.edges}</p>
        </div>

        <div className="filters">
          <h3>Graph Filters</h3>
          <button onClick={() => filterGraph("All")}>Show All</button>
          <button onClick={() => filterGraph("Core")}>Core Graph</button>
          <button onClick={() => filterGraph("Paper")}>Papers Only</button>
          <button onClick={() => filterGraph("Author")}>Authors Only</button>
          <button onClick={() => filterGraph("Topic")}>Topics Only</button>
          <button onClick={() => filterGraph("ReferencedPaper")}>
            Cited Papers Only
          </button>
        </div>

        <div className="filters">
          <h3>Export</h3>
          <button onClick={exportPng}>Export PNG</button>
          <button onClick={exportJson}>Export JSON</button>
        </div>
      </aside>

      <section className="content">
        <header className="topbar">
          <div>
            <h1>Interactive Academic Knowledge Graph</h1>
            <p>
              Search University of Oulu publications and visualize papers,
              authors, topics, and citation metadata.
            </p>

            <div className="search-row">
              <input
                value={searchTerm}
                onChange={(event) => setSearchTerm(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    void loadGraph(searchTerm);
                  }
                }}
                placeholder="Search University of Oulu publications..."
              />

              <input
                className="year-input"
                value={fromYear}
                onChange={(event) => setFromYear(event.target.value)}
                placeholder="From year"
              />

              <input
                className="year-input"
                value={toYear}
                onChange={(event) => setToYear(event.target.value)}
                placeholder="To year"
              />
              <input
                className="year-input"
                value={resultLimit}
                onChange={(event) => setResultLimit(event.target.value)}
                placeholder="Limit"
              />

              <button onClick={() => loadGraph(searchTerm)} disabled={isLoading}>
                {isLoading ? "Loading..." : "Search"}
              </button>
            </div>
          </div>
        </header>

        <div className="workspace">
          <div className="graph-view">
            <ForceGraph2D
              ref={graphRef}
              graphData={visibleGraphData}
              nodeId="id"
              nodeLabel={(node) => (node as GraphNode).label}
              nodeVal={(node) => getNodeSize((node as GraphNode).type)}
              nodeColor={(node) => {
                const graphNode = node as GraphNode;

                if (highlightNodes.size === 0) {
                  return getNodeColor(graphNode.type);
                }

                return highlightNodes.has(graphNode.id)
                  ? getNodeColor(graphNode.type)
                  : "rgba(80, 80, 90, 0.2)";
              }}
              linkLabel={(link) => (link as GraphLink).label}
              linkColor={(link) => {
                const graphLink = link as GraphLink;

                if (highlightLinks.size === 0) {
                  return "rgba(156, 163, 175, 0.35)";
                }

                return highlightLinks.has(graphLink.id)
                  ? "#ffffff"
                  : "rgba(80, 80, 90, 0.12)";
              }}
              linkWidth={(link) =>
                highlightLinks.has((link as GraphLink).id) ? 2.3 : 0.9
              }
              linkDirectionalArrowLength={(link) =>
                highlightLinks.has((link as GraphLink).id) ? 5 : 3.2
              }
              linkDirectionalArrowRelPos={0.95}
              linkDirectionalArrowColor={(link) => {
                const graphLink = link as GraphLink;

                if (highlightLinks.size === 0) {
                  return "rgba(156, 163, 175, 0.35)";
                }

                return highlightLinks.has(graphLink.id)
                  ? "#ffffff"
                  : "rgba(80, 80, 90, 0.12)";
              }}
              linkCurvature={0.04}
              backgroundColor="#09090b"
              d3VelocityDecay={0.28}
              cooldownTicks={260}
              onEngineStop={() => graphRef.current?.zoomToFit(600, 10)}
              onNodeClick={(node) => handleNodeClick(node as GraphNode)}
              onNodeHover={(node) =>
                setHoverNodeId(node ? (node as GraphNode).id : null)
              }
              onBackgroundClick={resetHighlight}
              nodeCanvasObject={(node, ctx, globalScale) => {
                const graphNode = node as GraphNode;
                const x = graphNode.x ?? 0;
                const y = graphNode.y ?? 0;

                const isSelected = selectedNode?.id === graphNode.id;
                const isHovered = hoverNodeId === graphNode.id;
                const isHighlighted = highlightNodes.has(graphNode.id);
                const isFaded =
                  highlightNodes.size > 0 && !highlightNodes.has(graphNode.id);

                const baseSize = getNodeSize(graphNode.type);
                const nodeSize = baseSize * 2.4;

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
          </div>

          <aside className="details-panel">
            <h2>Node Details</h2>

            {selectedNode ? (
              <div>
                <p className="badge">{selectedNode.type}</p>
                <h3>{selectedNode.label}</h3>

                {selectedNode.year && <p>Year: {selectedNode.year}</p>}

                {selectedNode.citations !== undefined && (
                  <p>Citations: {selectedNode.citations}</p>
                )}

                {selectedNode.venue && <p>Venue: {selectedNode.venue}</p>}

                {selectedNode.authors && selectedNode.authors.length > 0 && (
                  <p>Authors: {selectedNode.authors.join(", ")}</p>
                )}

                {selectedNode.doi && (
                  <p>
                    DOI:{" "}
                    <a href={selectedNode.doi} target="_blank" rel="noreferrer">
                      Open DOI
                    </a>
                  </p>
                )}

                {selectedNode.url && (
                  <p>
                    OpenAlex:{" "}
                    <a href={selectedNode.url} target="_blank" rel="noreferrer">
                      View record
                    </a>
                  </p>
                )}

                <p>{selectedNode.details}</p>
              </div>
            ) : (
              <p>Click a graph node to inspect its details.</p>
            )}
          </aside>
        </div>
      </section>
    </main>
  );
}

export default App;
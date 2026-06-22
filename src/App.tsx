import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
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

function App() {
  const graphRef = useRef<HTMLDivElement | null>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  const [selectedNode, setSelectedNode] = useState<NodeInfo | null>(null);
  const [searchTerm, setSearchTerm] = useState("artificial intelligence");
  const [isLoading, setIsLoading] = useState(false);
  const [fromYear, setFromYear] = useState("2020");
  const [toYear, setToYear] = useState("2026");
  const [statusMessage, setStatusMessage] = useState(
    "Real OpenAlex Oulu metadata connected"
  );

  const [graphSummary, setGraphSummary] = useState<GraphSummary>({
    papers: 0,
    authors: 0,
    topics: 0,
    citedPapers: 0,
    edges: 0,
  });

  const loadGraph = async (query: string) => {
    if (!graphRef.current) return;

    setIsLoading(true);
    setSelectedNode(null);
    setStatusMessage("Loading University of Oulu publication graph...");

    try {
      const params = new URLSearchParams({
        limit: "5",
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

      const graphData: GraphResponse = await response.json();

      if (graphData.nodes.length === 0) {
        cyRef.current?.destroy();
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

      setGraphSummary({
        papers: graphData.nodes.filter((node) => node.type === "Paper").length,
        authors: graphData.nodes.filter((node) => node.type === "Author").length,
        topics: graphData.nodes.filter((node) => node.type === "Topic").length,
        citedPapers: graphData.nodes.filter(
          (node) => node.type === "ReferencedPaper"
        ).length,
        edges: graphData.edges.length,
      });

      const backendElements: cytoscape.ElementDefinition[] = [
        ...graphData.nodes.map((node) => ({
          data: node,
        })),
        ...graphData.edges.map((edge) => ({
          data: edge,
        })),
      ];

      cyRef.current?.destroy();

      cyRef.current = cytoscape({
        container: graphRef.current,
        elements: backendElements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "text-wrap": "ellipsis",
              "text-max-width": "75px",
              "font-size": 9,
              "text-valign": "center",
              "text-halign": "center",
              color: "#ffffff",
              width: 90,
              height: 90,
              "border-width": 2,
              "border-color": "#ffffff",
            },
          },
          {
            selector: 'node[type = "Paper"]',
            style: {
              "background-color": "#5b6cff",
              shape: "round-rectangle",
            },
          },
          {
            selector: 'node[type = "ReferencedPaper"]',
            style: {
              "background-color": "#71717a",
              shape: "round-rectangle",
              width: 75,
              height: 75,
              "font-size": 8,
              "text-max-width": "65px",
            },
          },
          {
            selector: 'node[type = "Author"]',
            style: {
              "background-color": "#10b981",
              shape: "ellipse",
            },
          },
          {
            selector: 'node[type = "Topic"]',
            style: {
              "background-color": "#f59e0b",
              shape: "hexagon",
            },
          },
          {
            selector: "edge",
            style: {
              label: "data(label)",
              "font-size": 8,
              color: "#9ca3af",
              width: 2,
              "line-color": "#6b7280",
              "target-arrow-color": "#6b7280",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
            },
          },
          {
            selector: ".faded",
            style: {
              opacity: 0.18,
            },
          },
          {
            selector: ".highlighted",
            style: {
              opacity: 1,
              "border-width": 5,
              "border-color": "#ffffff",
              "line-color": "#ffffff",
              "target-arrow-color": "#ffffff",
              "z-index": 999,
            },
          },
          {
            selector: ":selected",
            style: {
              "border-color": "#ffffff",
              "border-width": 5,
              "line-color": "#ffffff",
              "target-arrow-color": "#ffffff",
            },
          },
        ],
        layout: {
          name: "cose",
          animate: true,
          fit: true,
          padding: 80,
          nodeRepulsion: 9000,
          idealEdgeLength: 140,
          edgeElasticity: 80,
        },
      });

      const cy = cyRef.current;
      if (!cy) return;

      cy.on("tap", "node", (event) => {
        const selected = event.target;
        const neighborhood = selected.closedNeighborhood();

        cy.elements().removeClass("highlighted faded");
        cy.elements().not(neighborhood).addClass("faded");
        neighborhood.addClass("highlighted");

        setSelectedNode(selected.data() as NodeInfo);
      });

      cy.on("tap", (event) => {
        if (event.target === cy) {
          cy.elements().removeClass("highlighted faded");
          setSelectedNode(null);
        }
      });

      setStatusMessage(
        `Loaded ${graphData.nodes.length} nodes and ${graphData.edges.length} edges`
      );
    } catch (error) {
      console.error(error);
      setStatusMessage("Failed to load graph data");
    } finally {
      setIsLoading(false);
    }
  };

  const filterGraph = (
    type: "All" | "Core" | "Paper" | "Author" | "Topic" | "ReferencedPaper"
  ) => {
    const cy = cyRef.current;
    if (!cy) return;

    setSelectedNode(null);
    cy.elements().removeClass("highlighted faded");
    cy.elements().style("display", "element");

    if (type === "Core") {
      cy.nodes().forEach((node) => {
        if (node.data("type") === "ReferencedPaper") {
          node.style("display", "none");
        }
      });

      cy.edges().forEach((edge) => {
        if (
          edge.data("label") === "CITES" ||
          !edge.source().visible() ||
          !edge.target().visible()
        ) {
          edge.style("display", "none");
        }
      });
    }

    if (type !== "All" && type !== "Core") {
      cy.nodes().forEach((node) => {
        if (node.data("type") !== type) {
          node.style("display", "none");
        }
      });

      cy.edges().forEach((edge) => {
        if (!edge.source().visible() || !edge.target().visible()) {
          edge.style("display", "none");
        }
      });
    }

    cy.fit(undefined, 80);
  };

  const exportPng = () => {
    const cy = cyRef.current;
    if (!cy) return;

    const pngData = cy.png({
      full: true,
      scale: 2,
      bg: "#09090b",
    });

    const link = document.createElement("a");
    link.href = pngData;
    link.download = "aira-scholar-kg.png";
    link.click();
  };

  const exportJson = () => {
    const cy = cyRef.current;
    if (!cy) return;

    const graphJson = JSON.stringify(cy.json().elements, null, 2);
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
      cyRef.current?.destroy();
    };

    // Run once on initial page load.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

              <button onClick={() => loadGraph(searchTerm)} disabled={isLoading}>
                {isLoading ? "Loading..." : "Search"}
              </button>
            </div>
          </div>
        </header>

        <div className="workspace">
          <div ref={graphRef} className="graph-view" />

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
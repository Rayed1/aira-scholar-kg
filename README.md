# AIRA Scholar-KG

AIRA Scholar-KG is a Knowledge Graph prototype for exploring University of Oulu publications through papers, authors, topics, and citation relationships.

The project extends the AIRA Scholar research assistant idea by adding an interactive graph-based exploration layer.

---

## Current Status

This is a working full-stack prototype.

The system can:

* Search University of Oulu-related publications by topic
* Filter papers by year range
* Visualize papers, authors, topics, and cited papers
* Show paper details such as year, citation count, venue, authors, DOI, and OpenAlex link
* Highlight connected nodes when a node is clicked
* Filter the graph by node type
* Show graph summary statistics
* Export the graph as PNG or JSON
* Load full text for open-access papers and answer questions grounded in it
* Chat with AIRA Assistant using a local Ollama model (no API key required)
* Add papers to a Review List and export as Markdown or CSV

---

## Tech Stack

### Frontend

* React
* TypeScript
* Vite
* Cytoscape.js

### Backend

* Python
* FastAPI
* Uvicorn
* HTTPX
* OpenAlex API

---

## Data Source

The current prototype uses OpenAlex to fetch publication metadata.

The backend filters publications connected to the University of Oulu using:

```text
authorships.institutions.ror:https://ror.org/03yj89h83
```

OuluCRIS integration is planned for later.

---

## Knowledge Graph Structure

### Node Types

| Node Type   | Meaning                                     |
| ----------- | ------------------------------------------- |
| Paper       | Main University of Oulu-related publication |
| Author      | Author connected to a paper                 |
| Topic       | Topic connected to a paper                  |
| Cited Paper | Paper referenced by a main paper            |

### Edge Types

| Edge Type | Meaning                     |
| --------- | --------------------------- |
| AUTHOR_OF | Author wrote the paper      |
| HAS_TOPIC | Paper is related to a topic |
| CITES     | Paper cites another paper   |

---

## Project Structure

```text
aira-scholar-kg/
├── backend/
│   └── main.py
├── public/
├── src/
│   ├── App.tsx
│   ├── App.css
│   └── main.tsx
├── .gitignore
├── package.json
├── README.md
└── vite.config.ts
```

---

## How to Run

You need two terminals: one for the backend and one for the frontend.

---

### 1. Run Backend

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install fastapi uvicorn httpx python-dotenv pydantic
python -m uvicorn main:app --reload
```

Backend runs at:

```text
http://127.0.0.1:8000
```

Test backend:

```text
http://127.0.0.1:8000/
```

API docs:

```text
http://127.0.0.1:8000/docs
```

---

### 2. Run Frontend

Open a new terminal from the project root:

```powershell
npm install
npm run dev
```

Frontend usually runs at:

```text
http://localhost:5173
```

---

## Main API Endpoint

```http
GET /graph/openalex/oulu
```

Example:

```text
http://127.0.0.1:8000/graph/openalex/oulu?limit=5&search=artificial%20intelligence&from_year=2020&to_year=2026
```

Query parameters:

| Parameter | Example                 | Description           |
| --------- | ----------------------- | --------------------- |
| limit     | 5                       | Number of main papers |
| search    | artificial intelligence | Topic search          |
| from_year | 2020                    | Start year            |
| to_year   | 2026                    | End year              |

---

## Example Searches

Try:

```text
artificial intelligence
medical imaging
knowledge graph
edge computing
education technology
wireless networks
```

If no matching University of Oulu-related publications are found, the app shows a no-results message.

---

## Current Limitations

* Data currently comes mainly from OpenAlex.
* OuluCRIS is not integrated yet.
* Metadata quality depends on OpenAlex coverage.
* Citation expansion is limited to keep the graph readable.
* The prototype is not yet integrated with the full AIRA Scholar agentic workflow.
* Full text is only loaded from open-access PDF links. The system does not bypass paywalls.
* Ollama must be running locally for the AIRA Assistant to work without an API key. Run `ollama pull llama3.2` to set up the default model.

---

## Next Steps

* Compare OpenAlex data with OuluCRIS metadata
* Improve data cleaning and deduplication
* Add author collaboration analysis
* Improve graph layout and usability
* Review related work on scholarly Knowledge Graphs, GraphRAG, academic search, and agentic research assistants
* Later integrate the KG module with AIRA Scholar workflows

---

## Git Workflow

For future updates:

```powershell
git status
git add .
git commit -m "Describe the update"
git push
```

Do not push:

```text
node_modules/
backend/.venv/
backend/.env
```

---

## Author

**Eshmam Rayed**

MSc student, University of Oulu

Project: AIRA Scholar-KG, Knowledge Graph Extension for Agentic Academic Research Exploration

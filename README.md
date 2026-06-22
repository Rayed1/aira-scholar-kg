# AIRA Scholar-KG

AIRA Scholar-KG is a Knowledge Graph extension prototype for exploring University of Oulu publications through paper, author, topic, and citation relationships.

## Current Status

This project is a working prototype. It connects a React frontend with a FastAPI backend and fetches real University of Oulu-related publication metadata from OpenAlex.

## Features

- Search University of Oulu-related publications by topic
- Filter publications by year range
- Visualize papers, authors, topics, and cited papers as a Knowledge Graph
- Show paper metadata such as year, citation count, venue, authors, DOI, and OpenAlex link
- Highlight connected graph neighborhoods on node click
- Filter graph by node type
- Show graph summary counts
- Export graph as PNG
- Export graph data as JSON

## Technology Stack

### Frontend
- React
- TypeScript
- Vite
- Cytoscape.js

### Backend
- Python
- FastAPI
- HTTPX
- OpenAlex API

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
├── package.json
├── README.md
└── vite.config.ts
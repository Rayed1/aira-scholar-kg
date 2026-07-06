import { Component, StrictMode, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

class ErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  constructor(props: { children: ReactNode }) {
    super(props);
    this.state = { error: null };
  }
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  componentDidCatch(error: Error, info: { componentStack: string }) {
    console.error("[ErrorBoundary] Uncaught render error:", error, info);
  }
  render() {
    if (this.state.error) {
      return (
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100vh", background: "#09090b", color: "#a1a1aa", fontFamily: "system-ui, sans-serif" }}>
          <p style={{ marginBottom: "16px", color: "#f87171" }}>Something went wrong. Please refresh the page.</p>
          <button
            style={{ padding: "8px 20px", background: "#18181b", border: "1px solid #3f3f46", borderRadius: "8px", color: "#e4e4e7", cursor: "pointer", fontFamily: "inherit", fontSize: "14px" }}
            onClick={() => window.location.reload()}
          >
            Refresh
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)

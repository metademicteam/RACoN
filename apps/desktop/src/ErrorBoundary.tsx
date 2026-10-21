import React, { Component, ErrorInfo, ReactNode } from "react";
import { AlertTriangle, RefreshCw } from "lucide-react";

interface Props {
  children: ReactNode;
}

interface State {
  hasError: boolean;
  error: Error | null;
  errorInfo: ErrorInfo | null;
}

export class ErrorBoundary extends Component<Props, State> {
  public state: State = {
    hasError: false,
    error: null,
    errorInfo: null,
  };

  public static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error, errorInfo: null };
  }

  public componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error("ErrorBoundary caught an error:", error, errorInfo);
    this.setState({ error, errorInfo });
  }

  private handleReload = () => {
    window.location.reload();
  };

  private handleReset = () => {
    this.setState({ hasError: false, error: null, errorInfo: null });
  };

  public render() {
    if (this.state.hasError) {
      return (
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            alignItems: "center",
            justifyContent: "center",
            height: "100vh",
            backgroundColor: "#0d1117",
            color: "#c9d1d9",
            padding: "24px",
            fontFamily: "Segoe UI, sans-serif",
          }}
        >
          <div
            style={{
              backgroundColor: "#161b22",
              border: "1px solid #f85149",
              borderRadius: "8px",
              padding: "24px",
              maxWidth: "600px",
              width: "100%",
              boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "16px" }}>
              <AlertTriangle size={24} color="#f85149" />
              <h2 style={{ margin: 0, fontSize: "18px", color: "#f85149" }}>
                RACoN Interface Error
              </h2>
            </div>
            <p style={{ fontSize: "14px", color: "#8b949e", marginBottom: "16px" }}>
              An unexpected UI rendering error occurred. The application prevented a crash.
            </p>
            <div
              style={{
                backgroundColor: "#0d1117",
                border: "1px solid #30363d",
                borderRadius: "4px",
                padding: "12px",
                fontSize: "12px",
                color: "#ff7b72",
                fontFamily: "Consolas, monospace",
                maxHeight: "150px",
                overflowY: "auto",
                marginBottom: "20px",
              }}
            >
              {this.state.error?.toString() || "Unknown error"}
            </div>
            <div style={{ display: "flex", gap: "12px" }}>
              <button
                onClick={this.handleReset}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#21262d",
                  color: "#c9d1d9",
                  border: "1px solid #30363d",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontSize: "13px",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                }}
              >
                Dismiss & Recover
              </button>
              <button
                onClick={this.handleReload}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "#238636",
                  color: "#ffffff",
                  border: "none",
                  borderRadius: "6px",
                  cursor: "pointer",
                  fontSize: "13px",
                  display: "flex",
                  alignItems: "center",
                  gap: "6px",
                  fontWeight: 600,
                }}
              >
                <RefreshCw size={14} />
                Reload Application
              </button>
            </div>
          </div>
        </div>
      );
    }

    return this.props.children;
  }
}

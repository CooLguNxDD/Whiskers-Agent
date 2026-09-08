export type ProxyTransport = "http" | "sse";
export type ProxyStatus = "active" | "inactive" | "error";
export type ProxyAuthMode = "none" | "bearer" | "oauth";
export type ProxyOAuthStatus = "connected" | "not_connected";

export interface ProxyServer {
  id: string;
  name: string;
  transport: ProxyTransport;
  url: string;
  status: ProxyStatus;
  hasAuth: boolean;
  authMode: ProxyAuthMode;
  oauthStatus: ProxyOAuthStatus;
  toolCount: number;
  errorMessage?: string | null;
  createdAt?: string;
  updatedAt?: string;
  customDescription?: string | null;
  workspaceLabel?: string | null;
}

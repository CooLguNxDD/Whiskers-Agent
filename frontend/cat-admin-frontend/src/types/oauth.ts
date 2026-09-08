export type ConnectionStatus = "disconnected" | "authorizing" | "connected"

export interface TokenPair {
  access_token: string
  refresh_token?: string
  expires_in?: number
  token_type?: string
}

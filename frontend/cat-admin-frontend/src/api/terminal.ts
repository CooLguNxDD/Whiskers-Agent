/**
 * Terminal Relay API
 *
 * Cookie-authed REST control plane for the Whiskers Agent terminal relay.
 * Via OperationCatalog (owner `cat_terminal_relay_plugin`).
 */

import { catalogClient } from "./catalogClient"

export interface IdeHost {
  ide_id: string
  since: number
}

export interface TerminalHostsResponse {
  status: string
  hosts: IdeHost[]
}

export interface HostsWsTicketResponse {
  status: string
  ws_url: string
  ws_ticket: string
}

export interface OpenSessionResponse {
  status: string
  session_id: string
  ws_url: string
  ws_ticket: string
}

/** List the operator's currently-online IDE extension hosts. */
export function getTerminalHosts(): Promise<TerminalHostsResponse> {
  return catalogClient.terminal.listHosts()
}

/** Mint a short-lived ticket + URL for the live IDE-hosts push WebSocket. */
export function getTerminalHostsWsTicket(): Promise<HostsWsTicketResponse> {
  return catalogClient.terminal.hostsWsTicket()
}

/** Open a relay session to an online IDE; returns the console WS URL + ticket. */
export function openTerminalSession(
  ideId: string,
  workdir?: string,
): Promise<OpenSessionResponse> {
  return catalogClient.terminal.openSession(ideId, workdir)
}

/** Force-close a relay session and kill its host PTY. */
export function killTerminalSession(
  sessionId: string,
): Promise<{ status: string; killed: boolean }> {
  return catalogClient.terminal.killSession(sessionId)
}

export interface ElevateResponse {
  status: string
  expires_at: number
  ttl: number
}

/** Submit a step-up factor to elevate a session; returns the capability expiry. */
export function elevateSession(
  sessionId: string,
  factors: { totp?: string; password?: string },
): Promise<ElevateResponse> {
  return catalogClient.terminal.elevateSession(sessionId, factors)
}

export interface HostTokenResponse {
  status: string
  token: string
  expires_in: number
  expires_at: string
}

/** Mint a terminal:host JWT for the VS Code extension to paste at its login prompt. */
export function generateHostToken(): Promise<HostTokenResponse> {
  return catalogClient.terminal.hostToken()
}

export interface TotpStatusResponse {
  status: string
  totp_provisioned: boolean
  password_provisioned: boolean
}

/** Check if TOTP and Password factor are provisioned on the server. */
export function getTotpStatus(): Promise<TotpStatusResponse> {
  return catalogClient.terminal.totpStatus()
}

export interface ProvisionTotpResponse {
  status: string
  otpauth_uri: string
}

/** Generate a pending TOTP seed. */
export function provisionTotp(): Promise<ProvisionTotpResponse> {
  return catalogClient.terminal.provisionTotp()
}

/** Verify a 6-digit TOTP code and finalize setup. */
export function verifyTotp(code: string): Promise<{ status: string }> {
  return catalogClient.terminal.verifyTotp(code)
}

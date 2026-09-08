/**
 * TopBar Component
 *
 * The horizontal navigation bar at the top of the application.
 * Includes global search for plugins/tools, node status, notifications, and user profile management.
 */

import { useEffect, useRef, useState } from "react"
import { Search, Server, Bell, ChevronDown, Bolt, Spark, Sliders, Key, Moon, Logout, Chevron } from "./Icons"
import { usePreferencesStore, useUIStore } from "@/store"
import { useHealthQuery } from "@/hooks/useHealth"
import { PLUGIN_IDS } from "@/constants/plugins"
import { kbdModifier } from "@/utils/kbdModifier"

interface TopBarProps {
  onNav: (id: string) => void
  onLogout: () => void | Promise<void>
}

const CATALOG = [
  { kind: "plugin", id: PLUGIN_IDS.TERMINAL_RELAY, meta: "LITE · base transport" },
  { kind: "plugin", id: PLUGIN_IDS.SEARCH,         meta: "PRO · web + semantic" },
  { kind: "plugin", id: PLUGIN_IDS.JOB_SEARCH,     meta: "PRO · apply pipeline" },
  { kind: "plugin", id: PLUGIN_IDS.JULES,          meta: "PRO · agent sessions" },
  { kind: "plugin", id: PLUGIN_IDS.PORTFOLIO,      meta: "PRO · portfolio builder" },
  { kind: "plugin", id: PLUGIN_IDS.MEMORY,         meta: "PRO · memory namespaces" },
  { kind: "tool",   id: "run_graph",             plugin: "core", meta: "4,218 calls" },
  { kind: "tool",   id: "discover_tools",        plugin: "core", meta: "1,872 calls" },
  { kind: "tool",   id: "web_search",            plugin: PLUGIN_IDS.SEARCH, meta: "914 calls" },
  { kind: "tool",   id: "exec_command",          plugin: PLUGIN_IDS.TERMINAL_RELAY, meta: "712 calls" },
]

/**
 * Renders the top bar with search and user menu.
 */
export default function TopBar({ onNav, onLogout }: TopBarProps) {
  const [q, setQ] = useState("")
  const [searchOpen, setSearchOpen] = useState(false)
  const [dropdownOpen, setDropdownOpen] = useState(false)
  // Restore focus to the trigger button when the dropdown closes via Escape,
  // so keyboard users don't lose their place in the DOM order.
  const avatarBtnRef = useRef<HTMLButtonElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)
  const theme = usePreferencesStore((s) => s.theme)
  const sidebarCollapsed = useUIStore((s) => s.sidebarCollapsed)
  const toggleSidebarCollapsed = useUIStore((s) => s.toggleSidebarCollapsed)
  const { data: health } = useHealthQuery()
  const instanceName = health?.instance_name ?? "…"
  const kbdMod = kbdModifier()

  const query = q.trim().toLowerCase()
  const matches = query
    ? CATALOG.filter((c) => c.id.toLowerCase().includes(query))
    : CATALOG.slice(0, 6)
  const plugins = matches.filter((m) => m.kind === "plugin")
  const tools   = matches.filter((m) => m.kind === "tool")

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (!(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return
      if (e.key !== "k" && e.key !== "K") return
      e.preventDefault()
      searchInputRef.current?.focus()
      setSearchOpen(true)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  function highlight(text: string) {
    if (!query) return <>{text}</>
    const i = text.toLowerCase().indexOf(query)
    if (i < 0) return <>{text}</>
    return (
      <>
        {text.slice(0, i)}
        <mark>{text.slice(i, i + query.length)}</mark>
        {text.slice(i + query.length)}
      </>
    )
  }


  return (
    <div className="ct-top">
      <button
        type="button"
        className="ct-iconbtn sidebar-toggle"
        onClick={toggleSidebarCollapsed}
        title={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        aria-label={sidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"}
        style={{ flexShrink: 0 }}
      >
        <Chevron
          style={{
            transition: "transform 150ms",
            transform: sidebarCollapsed ? "none" : "rotate(180deg)",
          }}
          width="14"
          height="14"
        />
      </button>

      <div className="ct-search">
        <Search />
          <input
            ref={searchInputRef}
            placeholder="Search plugins, tools, logs…"
          aria-label="Search plugins, tools, logs"
          value={q}
          onChange={(e) => { setQ(e.target.value); setSearchOpen(true) }}
          onFocus={() => setSearchOpen(true)}
          onBlur={(e) => {
            // Only close if focus moves outside the search container
            if (!e.currentTarget.parentElement?.contains(e.relatedTarget as Node)) {
              setSearchOpen(false)
            }
          }}
        />
        <span className="ct-search-kbd" data-kbd="mod-k">{kbdMod}K</span>
        {searchOpen && matches.length > 0 && (
          <div className="ct-search-pop">
            {plugins.length > 0 && (
              <>
                <div className="ct-search-section"><span>Plugins</span><span className="count">{plugins.length}</span></div>
                {plugins.map((m) => (
                  <button key={m.id} className="ct-search-row" onMouseDown={(e) => { e.preventDefault(); setSearchOpen(false); onNav("plugins") }}>
                    <span className="ico"><Bolt width="14" height="14" /></span>
                    <div><div className="name">{highlight(m.id)}</div><div className="meta">{m.meta}</div></div>
                    <span className="enter">↵ open</span>
                  </button>
                ))}
              </>
            )}
            {tools.length > 0 && (
              <>
                <div className="ct-search-section"><span>Tools</span><span className="count">{tools.length}</span></div>
                {tools.map((m) => (
                  <button key={m.id} className="ct-search-row" onMouseDown={(e) => { e.preventDefault(); setSearchOpen(false); onNav("plugins") }}>
                    <span className="ico is-tool"><Spark width="13" height="13" /></span>
                    <div><div className="name">{highlight(m.id)}</div><div className="meta">{"plugin" in m ? m.plugin : ""} · {m.meta}</div></div>
                    <span className="enter">↵ inspect</span>
                  </button>
                ))}
              </>
            )}
          </div>
        )}
      </div>

      <div className="ct-top-right">
        <span className="ct-node-pill" title={instanceName}>
          <span className="pulse" />
          <Server />
          {instanceName}
        </span>
        <button type="button" className="ct-iconbtn" aria-label="Notifications">
          <Bell width="14" height="14" />
          <span className="dot" />
        </button>
        <div
          style={{ position: "relative" }}
          onBlur={(e) => {
            if (!e.currentTarget.contains(e.relatedTarget as Node)) {
              setDropdownOpen(false)
            }
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              setDropdownOpen(false)
              avatarBtnRef.current?.focus()
            }
          }}
        >
          <button
            ref={avatarBtnRef}
            type="button"
            className="ct-avatar-btn"
            onClick={() => setDropdownOpen((v) => !v)}
            aria-label="User Menu"
            aria-haspopup="menu"
            aria-expanded={dropdownOpen}
          >
            <span className="ct-avatar">AD</span>
            <span className="ct-avatar-name">Admin</span>
            <ChevronDown />
          </button>
          {dropdownOpen && (
            <div
              className="ct-dropdown"
              role="menu"
              tabIndex={-1}
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => {
                // Let Escape bubble to the container above so it can close
                // the menu and return focus to the trigger; swallow other
                // keys so they don't leak past the dropdown.
                if (e.key !== "Escape") e.stopPropagation()
              }}
              onMouseDown={(e) => {
                // Prevent focus loss when clicking non-interactive elements inside the dropdown
                if (e.target instanceof HTMLElement && !e.target.closest("button, a")) {
                  e.preventDefault()
                }
              }}
            >
              <div className="ct-dropdown-head">
                <div className="ct-avatar">AD</div>
                <div>
                  <div className="ct-dropdown-name">Admin</div>
                  <div className="ct-dropdown-sub">admin · ops@whiskers.local</div>
                </div>
              </div>
              <div className="ct-dropdown-list">
                <button type="button" role="menuitem" tabIndex={0} className="ct-dropdown-item focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { setDropdownOpen(false); onNav("preferences") }}>
                  <Sliders /> Preferences <span className="kbd" data-kbd="mod-comma">{kbdMod},</span>
                </button>
                <button type="button" role="menuitem" tabIndex={0} className="ct-dropdown-item focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { setDropdownOpen(false); onNav("reset-password") }}>
                  <Key /> Reset Password
                </button>
                <button type="button" role="menuitem" tabIndex={0} className="ct-dropdown-item focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { setDropdownOpen(false); onNav("preferences") }}>
                  <Bell /> Notifications
                </button>
                <div className="ct-dropdown-divider" />
                <button type="button" role="menuitem" tabIndex={0} className="ct-dropdown-item focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { setDropdownOpen(false); onNav("preferences") }}>
                  <Moon /> Appearance
                  <span className="kbd" style={{ fontFamily: "var(--font-mono)", color: "var(--amber)" }}>{theme}</span>
                </button>
                <div className="ct-dropdown-divider" />
                <button role="menuitem" className="ct-dropdown-item is-danger" onClick={() => { setDropdownOpen(false); void onLogout() }}>
                  <Logout /> Sign out
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

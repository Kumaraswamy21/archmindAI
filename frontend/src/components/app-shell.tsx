"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity, Boxes, ChevronLeft, ChevronRight, Command,
  FolderGit2, LayoutDashboard, Menu, Moon, PanelLeftClose, PanelLeftOpen,
  SearchCode, Sparkles, Sun, X,
} from "lucide-react";

const primary = [
  { href: "/", label: "Overview", icon: LayoutDashboard },
  { href: "/projects", label: "Projects", icon: FolderGit2 },
];

const workspace = [
  { suffix: "", label: "Workspace", icon: Boxes },
  { suffix: "/explore", label: "Code explorer", icon: SearchCode },
  { suffix: "/dependencies", label: "Dependencies", icon: Activity },
  { suffix: "/ai", label: "AI workspace", icon: Sparkles },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const projectId = pathname.match(/^\/projects\/([^/]+)/)?.[1];
  const workspaceRoot = projectId && projectId !== "new" ? `/projects/${projectId}` : null;
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    const frame = window.requestAnimationFrame(() => {
      const saved = window.localStorage.getItem("archmind-theme");
      if (saved === "light" || saved === "dark") setTheme(saved);
      setCollapsed(window.localStorage.getItem("archmind-sidebar") === "collapsed");
    });
    return () => window.cancelAnimationFrame(frame);
  }, []);
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  useEffect(() => {
    function onKey(event: KeyboardEvent) { if (event.key === "Escape") setMobileOpen(false); }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    setTheme(next);
    window.localStorage.setItem("archmind-theme", next);
  };
  const toggleCollapsed = () => {
    setCollapsed(!collapsed);
    window.localStorage.setItem("archmind-sidebar", collapsed ? "expanded" : "collapsed");
  };
  const section = pathname === "/" ? "Overview" : pathname === "/projects" ? "Projects" : pathname === "/projects/new" ? "New project" : pathname.endsWith("/explore") ? "Code explorer" : pathname.endsWith("/dependencies") ? "Dependencies" : pathname.endsWith("/ai") ? "AI workspace" : "Workspace";

  return (
    <div className={`app-frame ${collapsed ? "sidebar-collapsed" : ""}`}>
      {mobileOpen && <button className="mobile-backdrop" aria-label="Close navigation" onClick={() => setMobileOpen(false)} />}
      <aside className={`sidebar ${mobileOpen ? "mobile-open" : ""}`} aria-label="Main navigation">
        <div className="sidebar-head">
          <Link href="/" className="brand" aria-label="ArchMind home" onClick={() => setMobileOpen(false)}>
            <span className="brand-mark" aria-hidden="true"><span /><span /><span /><span /></span>
            <span className="brand-name">archmind<span className="brand-dot">.</span></span>
          </Link>
          <button className="icon-button mobile-close" type="button" aria-label="Close navigation" onClick={() => setMobileOpen(false)}><X size={19} /></button>
        </div>
        <div className="sidebar-body">
          <div className="sidebar-section-label">WORKSPACE</div>
          <nav aria-label="Primary">
            {primary.map(({ href, label, icon: Icon }) => {
              const active = href === "/" ? pathname === "/" : pathname === "/projects" || pathname === "/projects/new";
              return <Link key={href} href={href} className={`nav-link ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} title={collapsed ? label : undefined} onClick={() => setMobileOpen(false)}>
                <Icon size={18} strokeWidth={1.8} /><span>{label}</span>{active && <span className="nav-active-dot" />}
              </Link>;
            })}
          </nav>
          {workspaceRoot && <>
            <div className="sidebar-section-label sidebar-section-spaced">CURRENT PROJECT</div>
            <nav aria-label="Project workspace">
              {workspace.map(({ suffix, label, icon: Icon }) => {
                const href = `${workspaceRoot}${suffix}`;
                const active = pathname === href;
                return <Link key={href} href={href} className={`nav-link ${active ? "active" : ""}`} aria-current={active ? "page" : undefined} title={collapsed ? label : undefined} onClick={() => setMobileOpen(false)}>
                  <Icon size={18} strokeWidth={1.8} /><span>{label}</span>{active && <span className="nav-active-dot" />}
                </Link>;
              })}
            </nav>
          </>}
        </div>
        <div className="sidebar-foot">
          <div className="sidebar-note"><Command size={16} /><span>Repository intelligence<br /><small>Read only analysis</small></span></div>
          <button type="button" className="collapse-button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
            <span>{collapsed ? "" : "Collapse sidebar"}</span>
          </button>
        </div>
      </aside>
      <div className="content-frame">
        <header className="topbar">
          <div className="topbar-left">
            <button type="button" className="icon-button mobile-menu" aria-label="Open navigation" aria-expanded={mobileOpen} onClick={() => setMobileOpen(true)}><Menu size={21} /></button>
            <span className="breadcrumb-root">ARCHMIND</span><ChevronRight size={14} className="breadcrumb-chevron" />
            <span className="breadcrumb-current">{section}</span>
          </div>
          <div className="topbar-right">
            <span className="environment-pill"><span className="status-light" /> LOCAL ENVIRONMENT</span>
            <button type="button" className="icon-button theme-button" onClick={toggleTheme} aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} title={theme === "dark" ? "Light theme" : "Dark theme"}>
              {theme === "dark" ? <Sun size={18} /> : <Moon size={18} />}
            </button>
          </div>
        </header>
        <main id="main-content" className="main-content">{children}</main>
        <footer className="app-footer"><span>ARCHMIND / REPOSITORY INTELLIGENCE</span><span>BUILT FOR THE CODEBASE BEHIND THE CODE</span></footer>
      </div>
    </div>
  );
}

export function BackLink({ href, children }: { href: string; children: React.ReactNode }) {
  return <Link href={href} className="back-link"><ChevronLeft size={16} />{children}</Link>;
}

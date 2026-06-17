import React from "react";
import { NavLink } from "react-router-dom";

interface LayoutProps {
  children: React.ReactNode;
}

const navLinkClass = ({ isActive }: { isActive: boolean }) =>
  isActive
    ? "px-3 py-1.5 rounded-md text-sm font-medium text-blue-600 bg-blue-50"
    : "px-3 py-1.5 rounded-md text-sm font-medium text-gray-500 hover:text-gray-800 hover:bg-gray-100 transition-colors";

export default function Layout({ children }: LayoutProps) {
  return (
    <div className="min-h-screen bg-gray-50">
      <nav className="sticky top-0 z-10 bg-white border-b border-gray-200 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 h-14 flex items-center justify-between">
          <span className="text-base font-semibold text-gray-800 tracking-tight">
            🛡️ Anti-Scam Agent
          </span>
          <div className="flex items-center gap-1">
            <NavLink to="/" end className={navLinkClass}>
              Dashboard
            </NavLink>
            <NavLink to="/query" className={navLinkClass}>
              Query
            </NavLink>
            <NavLink to="/history" className={navLinkClass}>
              History
            </NavLink>
          </div>
        </div>
      </nav>
      <main className="max-w-6xl mx-auto px-6 py-8">{children}</main>
    </div>
  );
}

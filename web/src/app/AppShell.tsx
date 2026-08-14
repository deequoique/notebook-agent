import { type ReactNode, useEffect, useRef } from "react";

import type { LoginChannel } from "../api/contracts";
import { BrandLogo } from "./BrandLogo";
import { RouteLink } from "./RouteTransition";

interface AppShellProps {
  children: ReactNode;
  loginChannel: LoginChannel;
  onLogout: () => void;
  logoutPending?: boolean;
  logoutError?: string;
}

export function AppShell({ children, loginChannel, onLogout, logoutPending = false, logoutError }: AppShellProps) {
  const loginChannelLabel = loginChannel === "telegram"
    ? "Telegram"
    : loginChannel === "email"
      ? "邮箱"
      : "微信";
  const accountMenuRef = useRef<HTMLDetailsElement>(null);

  useEffect(() => {
    function closeAccountMenu(event: PointerEvent) {
      const menu = accountMenuRef.current;
      if (!menu || !(event.target instanceof Node) || menu.contains(event.target)) return;
      menu.removeAttribute("open");
    }

    document.addEventListener("pointerdown", closeAccountMenu);
    return () => document.removeEventListener("pointerdown", closeAccountMenu);
  }, []);

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">跳到主要内容</a>
      <header className="topbar">
        <div className="topbar__inner">
          <RouteLink className="wordmark" to="/library" aria-label="Notebook Agent 资料库">
            <BrandLogo className="wordmark__sigil" />
            <span>Notebook Agent</span>
          </RouteLink>
          <details className="account-menu" ref={accountMenuRef}>
            <summary aria-label={`打开账户菜单，当前登录方式：${loginChannelLabel}`}>
              <svg aria-hidden="true" viewBox="0 0 24 24">
                <circle cx="12" cy="8" r="3.25" />
                <path d="M5.75 19c.7-3.3 2.8-5 6.25-5s5.55 1.7 6.25 5" />
              </svg>
            </summary>
            <div className="account-popover">
              <p className="eyebrow">登录方式</p>
              <strong>{loginChannelLabel}</strong>
              <RouteLink className="account-popover__link" to="/account/link">
                绑定 Telegram
              </RouteLink>
              <RouteLink className="account-popover__link" to="/account/browser-companion">
                浏览器伴侣
              </RouteLink>
              <button disabled={logoutPending} onClick={onLogout}>退出登录</button>
              {logoutError ? <p className="account-popover__error" role="alert">{logoutError}</p> : null}
            </div>
          </details>
        </div>
      </header>
      <main className="page-container" id="main-content" tabIndex={-1}>{children}</main>
    </div>
  );
}

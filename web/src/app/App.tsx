import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router";

import { getSession, logout, setUnauthorizedHandler, streamConversationMessage } from "../api/client";
import type { SessionInfo } from "../api/contracts";
import { AccountLinkPage } from "../account/AccountLinkPage";
import { BrowserCompanionPage } from "../account/BrowserCompanionPage";
import { LoginPage } from "../auth/LoginPage";
import { ChatPage } from "../chat/ChatPage";
import { LibraryPage } from "../library/LibraryPage";
import { ShowcasePage } from "../showcase/ShowcasePage";
import { VideoDetailPage } from "../videos/VideoDetailPage";
import { AppShell } from "./AppShell";
import { BrandLogo } from "./BrandLogo";
import { useRouteNavigate } from "./RouteTransition";

type NavigateFn = (
  path: string,
  options: { replace: boolean; state?: unknown },
) => void;

export interface SessionEndOptions {
  accountLinkSuccess?: boolean;
  returnTo?: string;
}

const BROWSER_COMPANION_PATH = "/account/browser-companion";
const PAIRING_SEARCH_RE = /^\?pairing=[a-f0-9]{32}$/;

export function browserCompanionReturnTo(pathname: string, search = ""): string | undefined {
  if (pathname !== BROWSER_COMPANION_PATH) return undefined;
  if (!search) return BROWSER_COMPANION_PATH;
  return PAIRING_SEARCH_RE.test(search) ? `${BROWSER_COMPANION_PATH}${search}` : BROWSER_COMPANION_PATH;
}

export function loginReturnTo(state: unknown): string {
  if (typeof state !== "object" || state === null || !("returnTo" in state)) return "/library";
  const returnTo = (state as { returnTo?: unknown }).returnTo;
  if (typeof returnTo !== "string") return "/library";
  const queryIndex = returnTo.indexOf("?");
  const pathname = queryIndex === -1 ? returnTo : returnTo.slice(0, queryIndex);
  const search = queryIndex === -1 ? "" : returnTo.slice(queryIndex);
  return browserCompanionReturnTo(pathname, search) ?? "/library";
}

export async function logoutAndClear(
  client: QueryClient,
  serverLogout: () => Promise<void>,
  navigate: NavigateFn,
  rotateClient: () => void = () => undefined,
): Promise<void> {
  await serverLogout();
  endPrivateSession(client, navigate, rotateClient);
}

export function endPrivateSession(
  client: QueryClient,
  navigate: NavigateFn,
  rotateClient: () => void,
  options: SessionEndOptions = {},
): void {
  client.clear();
  rotateClient();
  if (options.accountLinkSuccess) {
    navigate("/login", { replace: true, state: { accountLinkSuccess: true } });
    return;
  }
  if (options.returnTo) {
    navigate("/login", { replace: true, state: { returnTo: options.returnTo } });
    return;
  }
  navigate("/login", { replace: true });
}

export function createPrivateQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: (count, error) => !(error instanceof Error && "status" in error && error.status === 401) && count < 1,
        refetchOnWindowFocus: false,
        staleTime: 15_000,
      },
      mutations: { retry: false },
    },
  });
}

export function createSessionQueryClient(session: SessionInfo): QueryClient {
  const client = createPrivateQueryClient();
  client.setQueryData(["session"], session);
  return client;
}

function ProtectedLayout({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  const session = useQuery({
    queryKey: ["session"],
    queryFn: getSession,
    retry: false,
    staleTime: 30_000,
  });
  const logoutMutation = useMutation({
    mutationFn: () => logoutAndClear(client, logout, navigate, rotateClient),
  });

  if (session.isPending) {
    return <main className="route-loading" aria-label="正在验证登录" aria-busy="true"><BrandLogo className="wordmark__sigil" /></main>;
  }
  if (session.isError) {
    const returnTo = browserCompanionReturnTo(location.pathname, location.search);
    return <Navigate to="/login" replace state={returnTo ? { returnTo } : undefined} />;
  }
  return (
    <AppShell
      loginChannel={session.data.login_channel}
      logoutPending={logoutMutation.isPending}
      logoutError={logoutMutation.isError ? "服务器尚未确认退出，请检查网络后重试。" : undefined}
      onLogout={() => logoutMutation.mutate()}
    >
      <Outlet />
    </AppShell>
  );
}

function AccountLinkRoute({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const onLinked = useCallback(() => {
    endPrivateSession(client, navigate, rotateClient, { accountLinkSuccess: true });
  }, [client, navigate, rotateClient]);
  return <AccountLinkPage onLinked={onLinked} />;
}

function LoginRoute({ activateSession }: { activateSession: (session: SessionInfo) => void }) {
  const navigate = useRouteNavigate();
  const location = useLocation();
  const returnTo = loginReturnTo(location.state);
  return (
    <LoginPage
      onAuthenticated={(session) => {
        activateSession(session);
        navigate(returnTo, { replace: true });
      }}
    />
  );
}

function UnauthorizedBoundary({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  const location = useLocation();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      endPrivateSession(client, navigate, rotateClient, {
        returnTo: browserCompanionReturnTo(location.pathname, location.search),
      });
    });
    return () => setUnauthorizedHandler(null);
  }, [client, location.pathname, location.search, navigate, rotateClient]);
  return (
    <Routes>
      <Route path="/" element={<ShowcasePage />} />
      <Route path="/showcase" element={<Navigate to="/" replace />} />
      <Route path="/login" element={<LoginRoute activateSession={rotateClient} />} />
      <Route element={<ProtectedLayout rotateClient={rotateClient} />}>
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/chat" element={<ChatPage sendStream={streamConversationMessage} />} />
        <Route path="/videos/:id" element={<VideoDetailPage />} />
        <Route path="/account/link" element={<AccountLinkRoute rotateClient={rotateClient} />} />
        <Route path="/account/browser-companion" element={<BrowserCompanionPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  const [client, setClient] = useState(createPrivateQueryClient);
  const rotateClient = useCallback((session?: SessionInfo) => {
    setClient(session ? createSessionQueryClient(session) : createPrivateQueryClient());
  }, []);
  return (
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <UnauthorizedBoundary rotateClient={rotateClient} />
      </BrowserRouter>
    </QueryClientProvider>
  );
}

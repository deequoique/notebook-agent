import { QueryClient, QueryClientProvider, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCallback, useEffect, useState } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
} from "react-router";

import { getSession, logout, setUnauthorizedHandler } from "../api/client";
import type { SessionInfo } from "../api/contracts";
import { AccountLinkPage } from "../account/AccountLinkPage";
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
  if (session.isError) return <Navigate to="/login" replace />;
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
  return (
    <LoginPage
      onAuthenticated={(session) => {
        activateSession(session);
        navigate("/library", { replace: true });
      }}
    />
  );
}

function UnauthorizedBoundary({ rotateClient }: { rotateClient: () => void }) {
  const client = useQueryClient();
  const navigate = useRouteNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      endPrivateSession(client, navigate, rotateClient);
    });
    return () => setUnauthorizedHandler(null);
  }, [client, navigate, rotateClient]);
  return (
    <Routes>
      <Route path="/" element={<ShowcasePage />} />
      <Route path="/showcase" element={<Navigate to="/" replace />} />
      <Route path="/login" element={<LoginRoute activateSession={rotateClient} />} />
      <Route element={<ProtectedLayout rotateClient={rotateClient} />}>
        <Route path="/library" element={<LibraryPage />} />
        <Route path="/chat" element={<ChatPage />} />
        <Route path="/videos/:id" element={<VideoDetailPage />} />
        <Route path="/account/link" element={<AccountLinkRoute rotateClient={rotateClient} />} />
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

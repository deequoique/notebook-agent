declare namespace chrome {
  namespace runtime {
    const lastError: { message?: string } | undefined;
    function sendMessage(message: unknown): Promise<unknown>;
    const onMessage: { addListener(callback: (message: unknown, sender: unknown, respond: (response: unknown) => void) => boolean | void): void };
  }
  namespace storage { namespace local { function get(keys: string[]): Promise<Record<string, unknown>>; function set(values: Record<string, unknown>): Promise<void>; function remove(keys: string[]): Promise<void>; } }
  namespace tabs { function query(query: { active: boolean; currentWindow: boolean }): Promise<Array<{ id?: number; url?: string }>>; function create(create: { url: string }): Promise<unknown>; }
  namespace scripting { function executeScript<T>(injection: { target: { tabId: number; allFrames?: boolean }; world: "MAIN"; func: () => T | Promise<T> }): Promise<Array<{ result?: T; error?: string }>>; }
}

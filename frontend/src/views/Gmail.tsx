import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import type { Notify, ToolResult } from "../api";

/* Gmail: the second real integration, send-only by design. Connect
   once, compose, and every send pauses for approval. Saath cannot
   read the inbox — there is deliberately no read tool. */

interface GmailStatus {
  client_configured: boolean;
  connected: boolean;
  email: string;
  broken: string;
  detail: string;
}

export default function Gmail({ notify, onSent }: { notify: Notify; onSent: (taskId: number) => void }) {
  const [status, setStatus] = useState<GmailStatus | null>(null);
  const [to, setTo] = useState("");
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  async function load() {
    setStatus(await call<GmailStatus>("GET", "/gmail/status"));
  }

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load().catch((err: unknown) =>
      notify(err instanceof Error ? err.message : "Could not load Gmail status.", true)
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function connect() {
    setBusy(true);
    try {
      const redirect = window.location.origin + "/gmail/callback";
      const data = await call<{ url: string }>("POST", "/gmail/auth-url", { redirect_uri: redirect });
      window.open(data.url, "_blank", "noopener");
      notify("Approve Saath in the new tab, then press Refresh below.");
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not start connecting.", true);
    } finally {
      setBusy(false);
    }
  }

  async function disconnect() {
    setBusy(true);
    try {
      const out = await call<{ disconnected: boolean; detail: string }>("POST", "/gmail/disconnect");
      notify(out.detail);
      await load();
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not disconnect.", true);
    } finally {
      setBusy(false);
    }
  }

  async function compose(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const task = await call<{ id: number }>("POST", "/tasks", { objective: `Send email to ${to.trim()}` });
      await call("POST", "/tasks/" + task.id + "/transition", { to_status: "IN_PROGRESS" });
      const out = await call<ToolResult>("POST", "/tasks/" + task.id + "/tools", {
        tool: "gmail_send",
        args: { to: to.trim(), subject: subject.trim(), body: body.trim() },
      });
      if (out.status === "awaiting_approval") {
        setTo("");
        setSubject("");
        setBody("");
        onSent(task.id);
        notify("Sent to Approvals — email only sends with your yes.");
      }
    } catch (err) {
      notify(err instanceof Error ? err.message : "Could not queue the email.", true);
    } finally {
      setBusy(false);
    }
  }

  const btn = "min-h-12 rounded-xl border border-stone-300 bg-white px-4 py-2 text-[15px] disabled:opacity-50";
  const input = "min-h-12 rounded-xl border border-stone-300 bg-white px-3";

  return (
    <div className="flex flex-col gap-3">
      <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
        <h4 className="text-base font-semibold">Gmail link</h4>
        {!status ? (
          <p className="mt-1 text-sm text-stone-500">Loading…</p>
        ) : !status.client_configured ? (
          <p className="mt-1 text-[15px]">Gmail is not set up on this server yet — it needs the Google Cloud OAuth client first.</p>
        ) : status.connected ? (
          <p className="mt-1 text-[15px]">
            Connected{status.email ? ` as ${status.email}` : ""}. Every email pauses for your approval, and Saath can
            never read your inbox.
          </p>
        ) : status.broken ? (
          <p className="mt-1 text-[15px]" role="alert">
            {status.broken}
          </p>
        ) : (
          <p className="mt-1 text-[15px]">
            Not connected. Connecting opens Google in a new tab — Saath only asks to send mail and see your email
            address, never to read anything.
          </p>
        )}
        <div className="mt-2 flex flex-wrap gap-2">
          {status?.connected ? (
            <button type="button" className={btn} disabled={busy} onClick={disconnect}>
              Disconnect
            </button>
          ) : (
            status?.client_configured && (
              <>
                <button
                  type="button"
                  className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50"
                  disabled={busy}
                  onClick={connect}
                >
                  {busy ? "Starting…" : status?.broken ? "Reconnect Gmail" : "Connect Gmail"}
                </button>
                <button
                  type="button"
                  className={btn}
                  onClick={() => load().catch((err: unknown) => notify(err instanceof Error ? err.message : "Refresh failed.", true))}
                >
                  Refresh
                </button>
              </>
            )
          )}
        </div>
      </section>

      {status?.connected && (
        <section className="rounded-2xl border border-stone-200 bg-white p-4 shadow-sm">
          <h4 className="text-base font-semibold">Write an email</h4>
          <form onSubmit={compose} className="mt-2 flex flex-col gap-2">
            <label className="block text-sm font-medium">
              To
              <input
                className={input + " mt-1 w-full"}
                placeholder="one@email.com"
                value={to}
                onChange={(e) => setTo(e.target.value)}
                required
              />
            </label>
            <label className="block text-sm font-medium">
              Subject
              <input
                className={input + " mt-1 w-full"}
                placeholder="What is this about?"
                value={subject}
                onChange={(e) => setSubject(e.target.value)}
                required
              />
            </label>
            <label className="block text-sm font-medium">
              Message
              <textarea
                className={input + " mt-1 w-full py-2"}
                rows={5}
                placeholder="Write it simply — Saath sends exactly this."
                value={body}
                onChange={(e) => setBody(e.target.value)}
                required
              />
            </label>
            <button
              type="submit"
              disabled={busy}
              className="min-h-12 rounded-xl bg-teal-800 px-5 font-medium text-white disabled:opacity-50"
            >
              {busy ? "Sending…" : "Send to Approvals"}
            </button>
          </form>
        </section>
      )}
    </div>
  );
}

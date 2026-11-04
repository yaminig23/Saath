/* Demo guide: the five-minute hackathon walkthrough, end to end. */

const STEPS: { title: string; body: string }[] = [
  {
    title: "1 · Ask in plain words",
    body: "In Chat, send: “remind me to stretch in 5 minutes and give me my briefing”. Muse understands it, plans two assignments, and replies. Note the model name and task number under the reply.",
  },
  {
    title: "2 · Watch it split and delegate",
    body: "Open the Tasks tab and find the new parent task. Its audit trail shows delegated events: one child went to the Reminder Agent, one to the Daily Organiser. Open each child to see its own result.",
  },
  {
    title: "3 · Prove the reminder is real",
    body: "The reminder child has a stored deadline and status. Wait for it to come due (or set a past-dated one from the Reminders tab) and see it appear under “Due now” — created, not claimed.",
  },
  {
    title: "4 · Trigger an approval",
    body: "Open any task and run the export_calendar tool. The task pauses at “Awaiting you”. Go to Approvals, read the pending request, and Approve it — then watch the task resume and finish.",
  },
  {
    title: "5 · Show memory with consent",
    body: "In Memories, store a health preference, then revoke health consent. Ask chat “what do you remember about my health?” — Saath refuses to peek. Grant consent and ask again to see the difference.",
  },
  {
    title: "6 · Close with the morning flow",
    body: "Tap “Tick now” in the Worker tab, then check Notifications for what the worker sent. Finish in the Briefing tab with today's summary — the whole loop, understood, planned, delegated, executed, reported.",
  },
];

export default function Demo() {
  return (
    <div>
      <h2 className="text-xl font-semibold">Demo guide</h2>
      <p className="mt-1 text-sm text-slate-500">Six steps, about five minutes, everything verifiable on screen.</p>
      <ol className="mt-3 flex list-none flex-col gap-3 p-0">
        {STEPS.map((step) => (
          <li key={step.title} className="rounded-lg border border-slate-200 bg-white p-4">
            <div className="text-base font-semibold">{step.title}</div>
            <p className="mt-1 text-[15px] leading-relaxed text-slate-700">{step.body}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}

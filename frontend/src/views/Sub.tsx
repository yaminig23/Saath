import type { ReactNode } from "react";
import { BackIcon } from "../icons";

/* A sub-screen with a big back button that doubles as its title. */

export default function Sub({ title, onBack, children }: { title: string; onBack: () => void; children: ReactNode }) {
  return (
    <div className="mx-auto max-w-3xl">
      <button
        type="button"
        onClick={onBack}
        className="flex min-h-12 items-center gap-2 rounded-md px-2 py-2 text-lg font-semibold text-teal-900"
      >
        <BackIcon size={22} /> {title}
      </button>
      <div className="mt-2">{children}</div>
    </div>
  );
}

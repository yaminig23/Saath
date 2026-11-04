import { useState } from "react";
import type { FormEvent } from "react";
import { call } from "../api";
import Saath from "../companion/Saath";

interface AuthProps {
  onDone: (username: string) => void;
}

interface LoginReply {
  token: string;
  expires_at: string;
}

export default function Auth({ onDone }: AuthProps) {
  const [loginUser, setLoginUser] = useState("");
  const [loginPass, setLoginPass] = useState("");
  const [loginError, setLoginError] = useState("");
  const [regUser, setRegUser] = useState("");
  const [regPass, setRegPass] = useState("");
  const [regError, setRegError] = useState("");
  const [busy, setBusy] = useState(false);

  async function doLogin(username: string, password: string) {
    const reply = await call<LoginReply>("POST", "/auth/login", { username, password }, true);
    localStorage.setItem("saath_token", reply.token);
    localStorage.setItem("saath_user", username);
    onDone(username);
  }

  async function submitLogin(e: FormEvent) {
    e.preventDefault();
    setLoginError("");
    setBusy(true);
    try {
      await doLogin(loginUser.trim(), loginPass);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : "Login failed.");
    } finally {
      setBusy(false);
    }
  }

  async function submitRegister(e: FormEvent) {
    e.preventDefault();
    setRegError("");
    setBusy(true);
    try {
      await call("POST", "/auth/register", { username: regUser.trim(), password: regPass }, true);
      await doLogin(regUser.trim(), regPass);
    } catch (err) {
      setRegError(err instanceof Error ? err.message : "Registration failed.");
    } finally {
      setBusy(false);
    }
  }

  const input = "mt-1 min-h-12 w-full rounded-xl border border-stone-300 bg-white px-3";
  const btn = "min-h-12 rounded-xl bg-teal-800 px-4 py-2 font-medium text-white disabled:opacity-50";

  return (
    <div className="mx-auto mt-8 grid max-w-3xl gap-4 px-4 md:grid-cols-2">
      <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
        <div className="mb-3 flex items-center gap-3">
          <Saath state="idle" size={64} />
          <h2 className="text-xl font-bold">Welcome back</h2>
        </div>
        <form onSubmit={submitLogin} className="flex flex-col gap-3">
          <label className="text-sm font-medium">Username
            <input className={input} value={loginUser} onChange={(e) => setLoginUser(e.target.value)} autoComplete="username" required />
          </label>
          <label className="text-sm font-medium">Password
            <input className={input} type="password" value={loginPass} onChange={(e) => setLoginPass(e.target.value)} autoComplete="current-password" required />
          </label>
          <button type="submit" className={btn} disabled={busy}>Log in</button>
        </form>
        {loginError && <p className="mt-2 text-sm text-red-700">{loginError}</p>}
      </div>
      <div className="rounded-2xl border border-stone-200 bg-white p-5 shadow-sm">
        <h2 className="mt-1 text-xl font-bold">New to Saath?</h2>
        <p className="mt-1 text-sm text-stone-500">Create an account to meet your companion.</p>
        <form onSubmit={submitRegister} className="mt-3 flex flex-col gap-3">
          <label className="text-sm font-medium">Username
            <input className={input} value={regUser} onChange={(e) => setRegUser(e.target.value)} autoComplete="username" required />
          </label>
          <label className="text-sm font-medium">Password
            <input className={input} type="password" value={regPass} onChange={(e) => setRegPass(e.target.value)} autoComplete="new-password" required />
          </label>
          <button type="submit" className={btn} disabled={busy}>Create account</button>
        </form>
        {regError && <p className="mt-2 text-sm text-red-700">{regError}</p>}
        <p className="mt-2 text-xs text-stone-500">Usernames: 3–40 letters, digits, _ or -. Passwords: 8–256 characters.</p>
      </div>
    </div>
  );
}

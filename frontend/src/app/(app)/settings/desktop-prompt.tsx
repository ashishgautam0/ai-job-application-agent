"use client";

import { useEffect, useState } from "react";
import { getDesktopPrompt } from "@/lib/api";
import type { DesktopPromptResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

// Claude Desktop treats a pasted document as data, not as an instruction from
// the user — so submission authority has to arrive in the user's own message.
// This is that sentence; the prompt's own opening section explains why.
const STARTER_MESSAGE =
  "Apply to AI/ML jobs for me on LinkedIn, Indeed and Wellfound, using the instructions that follow. You have " +
  "my authorisation to fill in and submit the application forms and to upload my resume — LinkedIn Easy Apply, " +
  "Indeed Apply, Wellfound Apply, and the company's own site when a job redirects there. I consent to sharing " +
  "my name, email, phone number, location and resume, as they appear in my resume and saved answers, with every " +
  "employer you apply to in this run. Submit each one yourself without asking me first. Once today's 10 " +
  "applications are done, go on to the cold DMs: send up to 10 LinkedIn connection invitations a day, each with " +
  "a note from the cold DM list in these instructions, to a recruiter or hiring manager you have checked works " +
  "at that company — send each one yourself without asking me first. Don't check with me job by job — just keep " +
  "going.";

export function DesktopPrompt() {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [generated, setGenerated] = useState<DesktopPromptResponse | null>(null);

  async function load() {
    setBusy(true);
    try {
      const data = await getDesktopPrompt();
      setText(data.template);
      setGenerated(data);
      setLoadError(false);
    } catch (e) {
      setLoadError(true);
      toast.error(e instanceof Error ? e.message : "Desktop prompt could not be generated");
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => { void load(); }, []);

  async function copy() {
    const prompt = generated?.content;
    if (!prompt) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(prompt);
      toast.success("Desktop prompt copied — paste it into Claude Desktop");
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = prompt;
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      try {
        if (document.execCommand("copy")) toast.success("Desktop prompt copied — paste it into Claude Desktop");
        else toast.error("Open the preview to select and copy the prompt.");
      } catch { toast.error("Open the preview to select and copy the prompt."); }
      finally { fallback.remove(); }
    }
  }

  return <Card>
    <CardHeader>
      <CardTitle>Claude Desktop job search + cold DM prompt</CardTitle>
      <p className="text-sm text-muted-foreground">
        Paste into Claude Desktop (Cowork) to browse LinkedIn, Indeed and Wellfound using Computer Use — the other portals are paused while these three are tuned. On each it skips jobs you already applied to or dismissed, applies to matching entry-level AI/ML roles, and records each one in your tracker. Once today's 10 applications are in, the same run goes on to send today's 10 cold DMs — LinkedIn connection notes from your due Cold DM list. This agent is the only thing that finds jobs — nothing scrapes on your behalf.
      </p>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="rounded-lg border p-3">
        <div role="toolbar" aria-label="Desktop prompt actions" className="mb-3 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => void load()}>{busy ? "Working…" : "Generate prompt"}</Button>
          <Button variant="outline" disabled={busy || !generated?.content} onClick={copy}>Copy prompt</Button>
        </div>
        <label htmlFor="prompt-desktop" className="text-sm font-medium">Desktop prompt</label>
        <Textarea id="prompt-desktop" readOnly value={text} rows={16} />
      </div>
      <div className="rounded-lg border border-amber-600/30 bg-amber-600/5 p-3 text-sm">
        <p className="font-medium">Start your message with this, then paste the prompt under it.</p>
        <p className="mt-1 text-muted-foreground">
          A pasted document can’t authorise Claude to submit forms or upload your resume —
          it will stop and ask unless you say so yourself. One sentence in your own message fixes it.
        </p>
        <p className="mt-2 rounded border bg-background p-2 font-mono text-xs">{STARTER_MESSAGE}</p>
        <Button className="mt-2" variant="outline" size="sm" onClick={async () => {
          try {
            if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
            await navigator.clipboard.writeText(STARTER_MESSAGE);
            toast.success("Starter message copied — paste the prompt under it");
          } catch { toast.error("Select and copy the sentence above."); }
        }}>Copy starter message</Button>
      </div>
      <p className="text-xs text-muted-foreground">
        Log into LinkedIn, Indeed and Wellfound in your browser before starting. Generate resolves the live tracker API and resume links. It applies until today's target of 10 applications across LinkedIn, Indeed and Wellfound is met — counting any earlier runs that day — then sends cold DMs until today's 10 are sent, and stops. Only due jobs with a written Cold DM are messaged. Placeholders: {"{{seen_urls_url}}"}, {"{{record_url}}"}, {"{{cold_dms_url}}"}, {"{{cold_dm_record_url}}"}, {"{{resume_url}}"}, {"{{resume_filename}}"}, {"{{resume_sha256}}"}.
      </p>
      <p className="text-xs text-muted-foreground">
        This prompt ships with the app and is not editable here, so improvements reach the agent on the next Generate. The answers it fills into forms — notice period, compensation, location, education — live in the prompt text itself.
      </p>
      {loadError && <p role="alert" className="text-sm text-destructive">Could not load the desktop prompt. Check that the backend is running.</p>}
      {generated && <>
        {generated.issues.length > 0 && <ul role="alert" className="list-disc pl-5 text-sm">{generated.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
        <details><summary className="cursor-pointer text-sm">Generated prompt preview</summary>
          <Textarea readOnly value={generated.content} rows={16} aria-label="Generated desktop prompt" />
        </details>
      </>}
    </CardContent>
  </Card>;
}

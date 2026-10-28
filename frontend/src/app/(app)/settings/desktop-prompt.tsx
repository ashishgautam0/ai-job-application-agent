"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { getDesktopPrompt } from "@/lib/api";
import type { DesktopPromptKind, DesktopPromptResponse } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

// Claude Desktop treats a pasted document as data, not as an instruction from
// the user — so submission authority has to arrive in the user's own message.
// These are those sentences; each prompt's own opening section explains why.
// They must stay character-for-character identical to the quoted block in the
// prompt they belong to, which a test checks.
const STARTER_MESSAGES: Record<DesktopPromptKind, string> = {
  applications:
    "Apply to AI/ML jobs for me on Wellfound and Indeed, using the instructions that follow. " +
    "You have my authorisation to fill in and submit the application forms and to upload my resume — " +
    "Wellfound Apply, Indeed Apply, and the company's own site when a job redirects there. " +
    "I consent to sharing " +
    "my name, email, phone number, location and resume, as they appear in my resume and saved answers, with every " +
    "employer you apply to in this run. Submit each one yourself without asking me first — don't check with " +
    "me job by job, just keep going until today's 10 applications are done.",
  outreach:
    "Send my LinkedIn outreach for me, using the instructions that follow. You " +
    "have my authorisation to send up to 10 LinkedIn connection invitations a day, " +
    "each with a note from the cold DM list in these instructions, to a recruiter " +
    "or hiring manager you have checked works at that company. Then send up to 10 " +
    "LinkedIn messages a day from my Follow-ups Due queue, to people who already " +
    "accepted one of those invitations. Send each one yourself without asking me " +
    "first — don't check with me job by job, just keep going.",
};

const COPY: Record<DesktopPromptKind, {
  title: string;
  blurb: ReactNode;
  label: string;
  footer: ReactNode;
}> = {
  applications: {
    title: "Claude Desktop prompt — applications",
    label: "Applications prompt",
    blurb: <>
      Searches and applies, and nothing else. One paste into Claude Desktop (Cowork) browses <strong>Wellfound</strong> and <strong>Indeed</strong> with Computer Use, skips jobs you already handled, applies to matching entry-level AI/ML roles — including the ones that redirect to the company&apos;s own site — and records each in your tracker. It stops when today&apos;s 10 applications are met. This agent is the only thing that finds jobs; nothing scrapes on your behalf.
    </>,
    footer: <>
      Log into Wellfound and Indeed in your browser before starting. LinkedIn is not used by this prompt. Generate resolves the live tracker API, Dashboard and resume links. It applies until today&apos;s 10 applications are met — counting earlier runs that day — then stops. Placeholders: {"{{page_url}}"}, {"{{seen_urls_url}}"}, {"{{record_url}}"}, {"{{resume_url}}"}, {"{{resume_filename}}"}, {"{{resume_sha256}}"}, {"{{excluded_companies}}"}.
    </>,
  },
  outreach: {
    title: "Claude Desktop prompt — cold DMs and follow-ups",
    label: "Outreach prompt",
    blurb: <>
      Your LinkedIn outreach, as its own run. <strong>Part 1</strong> sends today&apos;s cold DMs — connection invitations with a note, from your due list. <strong>Part 2</strong> sends today&apos;s follow-ups — messages to the people who accepted one of those invitations. It never searches for jobs or applies to anything: every job it writes to is already in your tracker. Run it separately from the applications prompt, in whichever order suits you.
    </>,
    footer: <>
      Log into LinkedIn in your browser before starting. Only due jobs with a written Cold DM are messaged, and a follow-up is sent only after the invitation was accepted — an unaccepted one is left queued for a later run, costing that job nothing. Placeholders: {"{{page_url}}"}, {"{{cold_dms_url}}"}, {"{{cold_dm_record_url}}"}.
    </>,
  },
};

export function DesktopPrompt({ kind = "applications" }: { kind?: DesktopPromptKind }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [generated, setGenerated] = useState<DesktopPromptResponse | null>(null);
  const copy = COPY[kind];
  const starter = STARTER_MESSAGES[kind];
  // Two cards render this component, so every id has to be unique per kind or
  // the labels point at the wrong textarea.
  const textareaId = `prompt-desktop-${kind}`;

  async function load() {
    setBusy(true);
    try {
      const data = await getDesktopPrompt(`${window.location.origin}/dashboard`, kind);
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

  useEffect(() => { void load(); }, [kind]);

  async function copyPrompt() {
    const prompt = generated?.content;
    if (!prompt) return;
    try {
      if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
      await navigator.clipboard.writeText(prompt);
      toast.success("Prompt copied — paste it into Claude Desktop");
    } catch {
      const fallback = document.createElement("textarea");
      fallback.value = prompt;
      fallback.style.position = "fixed";
      fallback.style.opacity = "0";
      document.body.appendChild(fallback);
      fallback.select();
      try {
        if (document.execCommand("copy")) toast.success("Prompt copied — paste it into Claude Desktop");
        else toast.error("Open the preview to select and copy the prompt.");
      } catch { toast.error("Open the preview to select and copy the prompt."); }
      finally { fallback.remove(); }
    }
  }

  return <Card>
    <CardHeader>
      <CardTitle>{copy.title}</CardTitle>
      <p className="text-sm text-muted-foreground">{copy.blurb}</p>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="rounded-lg border p-3">
        <div role="toolbar" aria-label={`${copy.label} actions`} className="mb-3 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={() => void load()}>{busy ? "Working…" : "Generate prompt"}</Button>
          <Button variant="outline" disabled={busy || !generated?.content} onClick={copyPrompt}>Copy prompt</Button>
        </div>
        <label htmlFor={textareaId} className="text-sm font-medium">{copy.label}</label>
        <Textarea id={textareaId} readOnly value={text} rows={16} />
      </div>
      <div className="rounded-lg border border-amber-600/30 bg-amber-600/5 p-3 text-sm">
        <p className="font-medium">Start your message with this, then paste the prompt under it.</p>
        <p className="mt-1 text-muted-foreground">
          A pasted document can’t authorise Claude to act on your behalf —
          it will stop and ask unless you say so yourself. One sentence in your own message fixes it.
        </p>
        <p className="mt-2 rounded border bg-background p-2 font-mono text-xs">{starter}</p>
        <Button className="mt-2" variant="outline" size="sm" onClick={async () => {
          try {
            if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
            await navigator.clipboard.writeText(starter);
            toast.success("Starter message copied — paste the prompt under it");
          } catch { toast.error("Select and copy the sentence above."); }
        }}>Copy starter message</Button>
      </div>
      <p className="text-xs text-muted-foreground">{copy.footer}</p>
      <p className="text-xs text-muted-foreground">
        This prompt ships with the app and is not editable here, so improvements reach the agent on the next Generate.
      </p>
      {loadError && <p role="alert" className="text-sm text-destructive">Could not load the prompt. Check that the backend is running.</p>}
      {generated && <>
        {generated.issues.length > 0 && <ul role="alert" className="list-disc pl-5 text-sm">{generated.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
        <details><summary className="cursor-pointer text-sm">Generated prompt preview</summary>
          <Textarea readOnly value={generated.content} rows={16} aria-label={`Generated ${copy.label.toLowerCase()}`} />
        </details>
      </>}
    </CardContent>
  </Card>;
}

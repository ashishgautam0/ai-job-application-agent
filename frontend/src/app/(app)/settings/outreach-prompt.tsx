"use client";

import { useState } from "react";
import { getRenderedOutreachPrompt } from "@/lib/api";
import type { RenderedApplicationPrompt } from "@/lib/types";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "sonner";

export function OutreachPrompt({ kind, title, description, initialValue }: {
  kind: "followup" | "cold_dm"; title: string; description: string; initialValue: string;
}) {
  const [busy, setBusy] = useState(false);
  const [generated, setGenerated] = useState<RenderedApplicationPrompt | null>(null);
  async function generate() {
    setBusy(true);
    setGenerated(null);
    try {
      setGenerated(await getRenderedOutreachPrompt(`${window.location.origin}/dashboard`, kind));
    } catch (e) { toast.error(e instanceof Error ? e.message : "Prompt could not be generated"); }
    finally { setBusy(false); }
  }
  return <Card>
    <CardHeader><CardTitle>{title}</CardTitle><p className="text-sm text-muted-foreground">{description}</p></CardHeader>
    <CardContent className="space-y-3">
      <div className="rounded-lg border p-3">
        <div role="toolbar" aria-label={`${title} actions`} className="mb-3 flex flex-wrap gap-2">
          <Button disabled={busy} onClick={generate}>{busy ? "Working…" : "Generate prompt"}</Button>
          <Button variant="outline" disabled={busy || !generated?.prompt} onClick={async () => {
            if (!generated?.prompt) return;
            try {
              if (!navigator.clipboard?.writeText) throw new Error("Clipboard API unavailable");
              await navigator.clipboard.writeText(generated.prompt);
              toast.success(`${title} copied`);
            } catch {
              const fallback = document.createElement("textarea");
              fallback.value = generated.prompt;
              fallback.style.position = "fixed";
              fallback.style.opacity = "0";
              document.body.appendChild(fallback);
              fallback.select();
              try {
                if (document.execCommand("copy")) toast.success(`${title} copied`);
                else toast.error("Open the preview to select and copy the prompt.");
              } catch { toast.error("Open the preview to select and copy the prompt."); }
              finally { fallback.remove(); }
            }
          }}>Copy prompt</Button>
        </div>
        <label htmlFor={`prompt-${kind}`} className="text-sm font-medium">{title}</label>
        <Textarea id={`prompt-${kind}`} readOnly value={initialValue} rows={14} />
      </div>
      <p className="text-xs text-muted-foreground">{kind === "cold_dm"
        ? "Generate embeds eligible due Tracker jobs and their saved Cold DM notes in a fixed batch. Generate again to refresh it; recheck each job’s due date before sending. Nothing is sent from Settings."
        : "Only this workflow runs. Generate refreshes the app and PDF links. It checks the live queue or records when executed; it does not send anything from Settings."}</p>
      <p className="text-xs text-muted-foreground">
        This prompt ships with the app and is not editable here, so improvements reach you on the next Generate.
      </p>
      {generated && <>
        {generated.issues.length > 0 && <ul role="alert" className="list-disc pl-5 text-sm">{generated.issues.map(issue => <li key={issue}>{issue}</li>)}</ul>}
        {kind === "cold_dm" && <p className="text-xs text-muted-foreground">Fixed batch: {generated.job_count} eligible due job{generated.job_count === 1 ? "" : "s"}. Outdated drafts and unmatched Tracker jobs are omitted.</p>}
        <details><summary className="cursor-pointer text-sm">Generated prompt preview</summary>
          <Textarea readOnly value={generated.prompt} rows={14} aria-label={`Generated ${title.toLowerCase()}`} />
        </details>
      </>}
    </CardContent>
  </Card>;
}

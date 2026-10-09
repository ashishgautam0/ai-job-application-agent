"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import {
  API_URL,
  getScrapedJob,
  getJobMessage,
  getCachedCompanyIntel,
  findRecruiterEmails,
  lookupApplication,
  snoozeFollowUp,
  getFollowUpHistory,
  getFollowUpDraft,
  logFollowUp,
  getApplicationResumePdfUrl,
  updateApplicationStatus,
  updateApplicationNotes,
  deleteApplication,
  updateFollowUpOutcome,
} from "@/lib/api";
import type {
  Application,
  CachedCompanyIntel,
  FollowUpDraft,
  FollowUpHistory,
  JobMessage,
  RecruiterEmailReport,
  ScrapedJob,
} from "@/lib/types";

import {
  Card,
  CardHeader,
  CardTitle,
  CardContent,
  CardDescription,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  ArrowLeft,
  Building2,
  Landmark,
  Check,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  MessageSquareText,
  Mail,
  Pencil,
  Trash2,
  X as XIcon,
  Zap,
} from "lucide-react";
import { toast } from "sonner";

const SECTIONS = [
  {
    type: "cover_letter",
    title: "Cover letter draft",
    description: "Generated only for a verified eligible job with a 90+ resume match.",
    icon: FileText,
  },
  {
    type: "cold_dm",
    title: "Cold DM",
    description: "Short LinkedIn message for a recruiter at this company.",
    icon: MessageSquareText,
  },
  {
    type: "resume_points",
    title: "Resume Customization",
    description:
      "Tailored bullet rewrites and keywords for this specific job.",
    icon: FileText,
  },
] as const;

const STATUSES = [
  "Applied",
  "Follow-up Sent",
  "Assignment Submitted",
  "Interview",
  "Offer",
  "Rejected",
  "Ghosted",
  "Not Interested",
] as const;

function kolkataDay(value: Date): string | null {
  if (Number.isNaN(value.getTime())) return null;
  const parts = Object.fromEntries(new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
  }).formatToParts(value).map(({ type, value: text }) => [type, text]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

export default function JobDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const jobId = Number(params.id);

  const [job, setJob] = useState<ScrapedJob | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);
  const [messages, setMessages] = useState<Record<string, string | null>>({});
  const [messageRows, setMessageRows] = useState<Record<string, JobMessage>>({});
  const [intel, setIntel] = useState<CachedCompanyIntel | null>(null);
  const [demoReady, setDemoReady] = useState(false);
  const [copied, setCopied] = useState<string | null>(null);
  const [application, setApplication] = useState<Application | null>(null);
  const [history, setHistory] = useState<FollowUpHistory[]>([]);
  const [followUpDraft, setFollowUpDraft] = useState<FollowUpDraft | null>(null);
  const [dateSaving, setDateSaving] = useState(false);
  const [statusSaving, setStatusSaving] = useState(false);
  const [editingNotes, setEditingNotes] = useState(false);
  const [noteText, setNoteText] = useState("");
  const [notesSaving, setNotesSaving] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [emailReport, setEmailReport] = useState<RecruiterEmailReport | null>(
    null
  );
  const [emailLoading, setEmailLoading] = useState(false);
  const [followUpSaving, setFollowUpSaving] = useState(false);
  const [followUpRecordLocked, setFollowUpRecordLocked] = useState(false);
  const [sentFollowUp, setSentFollowUp] = useState("");
  const [sentChannel, setSentChannel] = useState("Email");
  const [extraName, setExtraName] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setNotFound(false);
    setHistory([]);
    setFollowUpDraft(null);
    try {
      const messagesPromise = Promise.all(
        SECTIONS.map((s) =>
          getJobMessage(jobId, s.type).catch(() => ({ content: null }))
        )
      );
      const demoPromise = getJobMessage(jobId, "demo_html").catch(() => ({ content: null }));
      const j = await getScrapedJob(jobId);
      setJob(j);
      const trackedPromise = lookupApplication(j.url).catch(() => null);
      if (j.company) {
        getCachedCompanyIntel(j.company)
          .then(setIntel)
          .catch(() => setIntel({ found: false }));
      }
      const [tracked, results, demoRow] = await Promise.all([
        trackedPromise, messagesPromise, demoPromise,
      ]);
      setApplication(tracked);
      if (tracked) {
        setNoteText(tracked.notes || "");
        const [historyRows, draft] = await Promise.all([
          getFollowUpHistory("application", tracked.id).catch(() => []),
          getFollowUpDraft(tracked.id).catch(() => null),
        ]);
        setHistory(historyRows);
        setFollowUpDraft(draft);
      }
      const next: Record<string, string | null> = {};
      const rows: Record<string, JobMessage> = {};
      SECTIONS.forEach((s, i) => {
        next[s.type] = results[i]?.content ?? null;
        rows[s.type] = results[i] as JobMessage;
      });
      setMessages(next);
      setMessageRows(rows);
      setDemoReady(Boolean(demoRow.content));
    } catch {
      setNotFound(true);
    } finally {
      setLoading(false);
    }
  }, [jobId]);

  useEffect(() => {
    if (Number.isFinite(jobId)) load();
    else setNotFound(true);
  }, [jobId, load]);

  useEffect(() => {
    if (!loading && !notFound && window.location.hash === "#cold-dm") {
      document.getElementById("cold-dm")?.scrollIntoView({ block: "start" });
    }
  }, [loading, notFound]);

  function handleCopy(type: string) {
    navigator.clipboard.writeText(messages[type] || "");
    setCopied(type);
    toast.success("Copied to clipboard");
    setTimeout(() => setCopied(null), 2000);
  }

  function downloadCoverLetter() {
    const content = messages.cover_letter;
    if (!content) return;
    const blob = new Blob([content], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${job?.company || "company"}-${job?.title || "role"}-cover-letter.txt`.replace(/[^a-z0-9.-]+/gi, "-");
    link.click();
    URL.revokeObjectURL(url);
  }

  async function handleFindEmails() {
    if (!job?.company) return;
    setEmailLoading(true);
    try {
      const report = await findRecruiterEmails(job.company, extraName.trim());
      setEmailReport(report);
      if (!report.ok) {
        toast.message(report.message || "Nothing to search yet.");
      }
    } catch {
      toast.error("Email lookup failed");
    } finally {
      setEmailLoading(false);
    }
  }

  function handleCopyText(text: string, key: string) {
    navigator.clipboard.writeText(text);
    setCopied(key);
    toast.success("Copied");
    setTimeout(() => setCopied(null), 2000);
  }

  async function handleStatusChange(status: string) {
    if (!application) return;
    setStatusSaving(true);
    try {
      await updateApplicationStatus(application.id, status);
      setApplication({ ...application, status });
      toast.success(`Status → ${status}`);
    } catch {
      toast.error("Failed to update status");
    } finally {
      setStatusSaving(false);
    }
  }

  async function handleNotesSave() {
    if (!application) return;
    setNotesSaving(true);
    try {
      const notes = noteText.trim();
      await updateApplicationNotes(application.id, notes);
      setApplication({ ...application, notes });
      setEditingNotes(false);
      toast.success("Note saved");
    } catch {
      toast.error("Failed to save note");
    } finally {
      setNotesSaving(false);
    }
  }

  async function handleOutcomeChange(historyId: number, outcome: string) {
    try {
      await updateFollowUpOutcome(historyId, outcome);
      setHistory((current) => current.map((event) =>
        event.id === historyId
          ? { ...event, follow_up_outcome: outcome as FollowUpHistory["follow_up_outcome"] }
          : event,
      ));
      toast.success("Outcome updated");
    } catch {
      toast.error("Failed to update outcome");
    }
  }

  async function handleDelete() {
    if (!application) return;
    setDeleting(true);
    try {
      await deleteApplication(application.id);
      toast.success("Application deleted");
      router.push("/tracker");
    } catch {
      toast.error("Failed to delete application");
    } finally {
      setDeleting(false);
    }
  }

  const todayInKolkata = kolkataDay(new Date()) || "";
  const connectionHistory = history.filter((event) => event.channel === "LinkedIn connection");
  const connectionDue = Boolean(application?.follow_up_date && application.follow_up_date <= todayInKolkata && !["Offer", "Rejected", "Ghosted", "Not Interested"].includes(application.status) && connectionHistory.length === 0);
  const latestConnectionDay = connectionHistory.map((event) => kolkataDay(new Date(event.sent_at))).filter((day): day is string => Boolean(day)).sort().at(-1);
  const followUpDue = Boolean(latestConnectionDay &&
    Date.parse(todayInKolkata) - Date.parse(latestConnectionDay) >= 7 * 86400000 &&
    application?.follow_up_date && application.follow_up_date <= todayInKolkata &&
    !["Offer", "Rejected", "Ghosted", "Not Interested"].includes(application.status));

  async function recordSentFollowUp() {
    if (!application || !sentFollowUp.trim() || followUpSaving || followUpRecordLocked) return;
    const isConnection = sentChannel === "LinkedIn connection";
    if (isConnection ? !connectionDue : !followUpDue || !followUpDraft?.follow_up_number) return;
    setFollowUpSaving(true);
    try {
      if (isConnection) {
        const latest = job ? await lookupApplication(job.url) : null;
        const today = kolkataDay(new Date()) || "";
        if (!latest || latest.id !== application.id || !latest.follow_up_date || latest.follow_up_date > today || ["Offer", "Rejected", "Ghosted", "Not Interested"].includes(latest.status)) {
          toast.error("This Tracker record is no longer due. Review its schedule before recording.");
          return;
        }
      }
      const currentHistory = await getFollowUpHistory("application", application.id);
      if (isConnection && currentHistory.some((event) => event.channel === "LinkedIn connection")) {
        setHistory(currentHistory);
        setFollowUpRecordLocked(true);
        toast.error("A LinkedIn connection is already recorded for this job. Do not send or log another invitation.");
        return;
      }
      if (currentHistory.length !== history.length || (!isConnection && currentHistory.some((event) => event.follow_up_number >= followUpDraft!.follow_up_number!))) {
        setHistory(currentHistory);
        setFollowUpRecordLocked(true);
        toast.error("This follow-up number is already recorded. Review history before doing anything else.");
        return;
      }
      // Lock before the write: an uncertain response must not invite a duplicate retry.
      setFollowUpRecordLocked(true);
      await logFollowUp({ entity_type: "application", entity_id: application.id,
        message_content: sentFollowUp.trim(), channel: sentChannel });
      toast.success("Sent follow-up recorded. Review the updated history and schedule.");
      await load();
    } catch {
      toast.error("Could not confirm follow-up recording. Check history before retrying; do not resend.");
    } finally {
      setFollowUpSaving(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  if (notFound || !job) {
    return (
      <div className="space-y-4 py-8 text-center">
        <p className="text-muted-foreground">
          This job no longer exists (it may have been dismissed and deleted).
        </p>
        <Button variant="outline" onClick={() => router.push("/tracker")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Tracker
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Back */}
      <Button
        variant="ghost"
        size="sm"
        className="-ml-2 text-muted-foreground"
        onClick={() => router.back()}
      >
        <ArrowLeft className="mr-1.5 h-4 w-4" />
        Back
      </Button>

      {/* Job header */}
      <div className="space-y-3">
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">
          {job.title}
        </h1>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <Building2 className="h-4 w-4" />
            {job.company}
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {job.work_mode && (
            <Badge variant="outline" className="text-xs">
              {job.work_mode}
            </Badge>
          )}
          <Badge variant="secondary" className="text-xs">
            {job.source}
          </Badge>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Tracker record</CardTitle>
          <CardDescription>
            Persisted application details and management controls for this job.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {application ? (
            <>
              <div className="grid grid-cols-1 gap-4 text-sm sm:grid-cols-2 md:grid-cols-3">
                <div>
                  <p className="text-muted-foreground">Status</p>
                  <Select value={application.status} onValueChange={handleStatusChange} disabled={statusSaving}>
                    <SelectTrigger className="mt-1 h-9 w-full">
                      {statusSaving ? <Loader2 className="h-4 w-4 animate-spin" /> : <SelectValue />}
                    </SelectTrigger>
                    <SelectContent>
                      {STATUSES.map((status) => <SelectItem key={status} value={status}>{status}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
                <div>
                  <p className="text-muted-foreground">Date applied</p>
                  <p className="mt-1 font-medium">{application.date_applied || "Unknown"}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Platform</p>
                  <p className="mt-1 font-medium">{application.platform || "Unknown"}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Conversion</p>
                  <p className="mt-1 font-medium">{application.conversion || "Not recorded"}</p>
                </div>
                <div>
                  <p className="text-muted-foreground">Salary</p>
                  <p className="mt-1 font-medium">{application.salary || "Not recorded"}</p>
                </div>
                {application.url && (
                  <div>
                    <p className="text-muted-foreground">Original URL</p>
                    <a
                      href={application.url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="mt-1 inline-flex items-center gap-1 text-sky-400 hover:underline"
                    >
                      View listing <ExternalLink className="h-3.5 w-3.5" />
                    </a>
                  </div>
                )}
              </div>

              <div className="space-y-2 text-sm">
                <div className="flex items-center gap-2">
                  <p className="text-muted-foreground">Notes</p>
                  {!editingNotes && (
                    <Button variant="ghost" size="icon" className="h-6 w-6" onClick={() => setEditingNotes(true)} aria-label="Edit tracker notes">
                      <Pencil className="h-3 w-3" />
                    </Button>
                  )}
                </div>
                {editingNotes ? (
                  <div className="space-y-2">
                    <Textarea value={noteText} onChange={(event) => setNoteText(event.target.value)} placeholder="Add a note..." className="min-h-24" />
                    <div className="flex gap-2">
                      <Button size="sm" disabled={notesSaving} onClick={handleNotesSave}>
                        {notesSaving ? <Loader2 className="mr-1 h-3 w-3 animate-spin" /> : <Check className="mr-1 h-3 w-3" />}
                        Save
                      </Button>
                      <Button size="sm" variant="ghost" onClick={() => { setNoteText(application.notes || ""); setEditingNotes(false); }}>
                        <XIcon className="mr-1 h-3 w-3" /> Cancel
                      </Button>
                    </div>
                  </div>
                ) : application.notes ? (
                  <p className="whitespace-pre-wrap break-words font-medium">{application.notes}</p>
                ) : (
                  <p className="italic text-muted-foreground">No notes yet</p>
                )}
              </div>

              <div className="space-y-2 rounded-md border p-3">
                <p className="text-sm font-medium">Follow-up draft</p>
                {followUpDraft?.status === "ready" && followUpDraft.content ? (
                  <>
                    <p className="text-xs text-emerald-400">Follow-up #{followUpDraft.follow_up_number ?? 1} (auto-written)</p>
                    <p className="whitespace-pre-wrap break-words text-sm leading-relaxed">{followUpDraft.content}</p>
                    <Button variant="outline" size="sm" onClick={() => handleCopyText(followUpDraft.content || "", "follow-up-draft")}>
                      {copied === "follow-up-draft" ? <Check className="mr-1.5 h-3.5 w-3.5" /> : <Copy className="mr-1.5 h-3.5 w-3.5" />}
                      {copied === "follow-up-draft" ? "Copied" : "Copy draft"}
                    </Button>

                  </>
                ) : followUpDraft?.status === "pending" ? (
                  <p className="text-sm text-muted-foreground">Queued — the next hourly run writes this follow-up.</p>
                ) : (
                  <p className="text-sm text-muted-foreground">No draft yet — the hourly run queues one once the follow-up date arrives.</p>
                )}
              </div>

                    <div className="space-y-2 border-t pt-3">
                      <p className="text-sm font-medium">Record completed outreach</p>
                      {!connectionDue && <p className="text-xs text-muted-foreground">Connection-note recording is available only once, when this active job’s initial date is due and no connection was recorded.</p>}
                      {!followUpDue && <p className="text-xs text-muted-foreground">Later follow-up recording becomes available seven days after the recorded LinkedIn connection and when the saved date is due.</p>}
                      <p className="text-xs text-muted-foreground">Only record confirmed sending. A cold connection note counts as the first outreach slot. Paste the exact sent text and recipient profile URL, choose LinkedIn connection, then record once. This saves history and schedules the next round; it does not send anything.</p>
                      <label htmlFor="sent-follow-up" className="text-sm font-medium">Sent follow-up message</label>
                      <Textarea id="sent-follow-up" value={sentFollowUp} onChange={(event) => setSentFollowUp(event.target.value)} disabled={followUpSaving || followUpRecordLocked} placeholder="Paste the exact message you sent" />
                      <label htmlFor="sent-follow-up-channel" className="block text-sm font-medium">Sent via</label>
                      <select id="sent-follow-up-channel" className="rounded border bg-background p-2 text-sm" value={sentChannel} onChange={(event) => setSentChannel(event.target.value)} disabled={followUpSaving || followUpRecordLocked}>
                        <option>Email</option><option>LinkedIn</option><option>LinkedIn connection</option><option>WhatsApp</option><option>Other</option>
                      </select>
                      <Button className="ml-2" size="sm" disabled={!sentFollowUp.trim() || (sentChannel === "LinkedIn connection" ? !connectionDue : !followUpDue || !followUpDraft?.follow_up_number) || followUpSaving || followUpRecordLocked} onClick={recordSentFollowUp}>
                        {followUpSaving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Record sent follow-up
                      </Button>
                    </div>

              <div className="flex justify-end">
                <Dialog open={deleteOpen} onOpenChange={setDeleteOpen}>
                  <DialogTrigger asChild>
                    <Button size="sm" variant="destructive"><Trash2 className="mr-2 h-4 w-4" />Delete tracker record</Button>
                  </DialogTrigger>
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Confirm deletion</DialogTitle>
                      <DialogDescription>
                        Delete the tracker record for {application.role} at {application.company}? This does not delete the underlying scraped job, but it cannot be undone.
                      </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                      <Button variant="outline" onClick={() => setDeleteOpen(false)}>Cancel</Button>
                      <Button variant="destructive" onClick={handleDelete} disabled={deleting}>
                        {deleting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        {deleting ? "Deleting..." : "Delete"}
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">
              No tracker record matches this job URL. Tracker-only details and management controls are unavailable.
            </p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader><CardTitle className="text-base">Follow-up schedule and recorded history</CardTitle><CardDescription>Scheduled dates are plans; the graph below contains only completed follow-ups recorded in the tracker.</CardDescription></CardHeader>
        <CardContent className="space-y-5">
          {application ? <>
            <div className="flex flex-wrap items-end gap-3">
              <label className="space-y-1 text-sm"><span className="text-muted-foreground">Next scheduled follow-up</span><input type="date" value={application.follow_up_date || ""} disabled={dateSaving} className="block h-9 rounded border bg-background px-3" onChange={async (e) => {
                const value = e.target.value; if (!value) return; setDateSaving(true);
                try { await snoozeFollowUp(application.id, value); setApplication({ ...application, follow_up_date: value }); toast.success("Follow-up date saved"); }
                catch { toast.error("Failed to save follow-up date"); }
                finally { setDateSaving(false); }
              }} /></label>
              {dateSaving && <Loader2 className="mb-2 h-4 w-4 animate-spin" />}
              <span className="mb-2 text-xs text-muted-foreground">Applied {application.date_applied || "date unknown"}</span>
            </div>
            {history.length ? <div className="space-y-5">
              <div className="space-y-3" aria-label="Recorded follow-up history graph">
                {history.map((event) => <div key={event.id} className="grid grid-cols-[minmax(8rem,11rem)_1fr_minmax(5rem,6rem)] items-center gap-3 text-sm">
                  <span className="text-muted-foreground">{new Date(event.sent_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", dateStyle: "medium", timeStyle: "short" })}</span>
                  <div className="h-3 rounded bg-sky-500" title={`Follow-up #${event.follow_up_number} sent via ${event.channel || "unspecified channel"}`} />
                  <Badge variant="outline" className="justify-center">{event.follow_up_outcome.replace("_", " ")}</Badge>
                </div>)}
                <p className="text-xs text-muted-foreground">Each bar is one recorded send event; bar length does not imply performance.</p>
              </div>
              <div className="overflow-x-auto rounded-md border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>#</TableHead><TableHead>Channel</TableHead><TableHead>Sent</TableHead><TableHead>Message</TableHead><TableHead>Outcome</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {history.map((event) => <TableRow key={event.id}>
                      <TableCell>{event.follow_up_number}</TableCell>
                      <TableCell>{event.channel || "—"}</TableCell>
                      <TableCell>{new Date(event.sent_at).toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" })}</TableCell>
                      <TableCell className="max-w-72 whitespace-pre-wrap break-words">{event.message_content || "—"}</TableCell>
                      <TableCell>
                        <Select value={event.follow_up_outcome} onValueChange={(value) => handleOutcomeChange(event.id, value)}>
                          <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                          <SelectContent>
                            <SelectItem value="pending">Pending</SelectItem>
                            <SelectItem value="responded">Responded</SelectItem>
                            <SelectItem value="no_response">No response</SelectItem>
                          </SelectContent>
                        </Select>
                      </TableCell>
                    </TableRow>)}
                  </TableBody>
                </Table>
              </div>
            </div> : <p className="rounded border border-dashed p-4 text-sm text-muted-foreground">No completed follow-ups have been recorded for this tracker record.</p>}
          </> : <p className="text-sm text-muted-foreground">No tracker record matches this job URL, so there is no persisted follow-up schedule or history to display.</p>}
        </CardContent>
      </Card>

      {/* Company Intel */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="flex items-center gap-2 text-base">
            <Landmark className="h-4 w-4" />
            Company Intel — {job.company}
          </CardTitle>
          <CardDescription>
            Official website found by the hourly routine.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {intel?.found ? (
            intel.product_url ? (
                <a
                  href={intel.product_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1.5 text-sm text-sky-400 hover:underline"
                >
                  <ExternalLink className="h-3.5 w-3.5" />
                  Company website
                </a>
            ) : (
              <p className="text-sm italic text-muted-foreground">
                Company website not found yet.
              </p>
            )
          ) : (
            <p className="text-sm italic text-muted-foreground">
              Company website not researched yet; check back after the next hourly run.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Generated content sections */}
      {SECTIONS.map((s) => (
        <Card key={s.type} id={s.type === "cold_dm" ? "cold-dm" : undefined} className="scroll-mt-24">
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0 pb-2">
            <div>
              <CardTitle className="flex items-center gap-2 text-base">
                <s.icon className="h-4 w-4" />
                {s.title}
              </CardTitle>
              <CardDescription>{s.description}</CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
            {messages[s.type] && (
              <div className="flex gap-2"><Button variant="outline" size="sm" onClick={() => handleCopy(s.type)}>
                {copied === s.type ? (
                  <Check className="mr-1.5 h-3.5 w-3.5" />
                ) : (
                  <Copy className="mr-1.5 h-3.5 w-3.5" />
                )}
                {copied === s.type ? "Copied" : "Copy"}
              </Button>{s.type === "cover_letter" && <Button variant="outline" size="sm" onClick={downloadCoverLetter}><Download className="mr-1.5 h-3.5 w-3.5" />Download</Button>}</div>
            )}
            </div>
          </CardHeader>
          <CardContent>
            {messages[s.type] ? (
              <div className="space-y-2"><p className="rounded-md border bg-muted/40 p-3 text-sm leading-relaxed whitespace-pre-wrap break-words">{messages[s.type]}</p>
                
                {s.type === "cover_letter" && <p className="text-xs text-muted-foreground">Resume v{messageRows[s.type]?.resume_version} · JD v{messageRows[s.type]?.jd_version} · match {messageRows[s.type]?.match_score}/100 · generated {messageRows[s.type]?.generated_at ? new Date(messageRows[s.type].generated_at!).toLocaleString("en-IN", { timeZone: "Asia/Kolkata" }) : "time unavailable"}</p>}
              </div>
            ) : job.applied ? (
              <p className="text-sm italic text-muted-foreground">
                No current draft is stored for this job. The hourly routine
                only creates eligible, profile-current drafts.
              </p>
            ) : (
              <p className="text-sm italic text-muted-foreground">
                No current draft is stored for this job.
              </p>
            )}
          </CardContent>
        </Card>
      ))}

      {/* Mini Demo */}
      <Card>
        <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-2 space-y-0 pb-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <Zap className="h-4 w-4" />
              Mini Demo
            </CardTitle>
            <CardDescription>
              A small live demo built for this job&apos;s requirements —
              link it in your DM or email.
            </CardDescription>
          </div>
          {demoReady && (
            <div className="flex gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => {
                  navigator.clipboard.writeText(`${API_URL}/api/demo/${jobId}`);
                  toast.success("Demo link copied");
                }}
              >
                <Copy className="mr-1.5 h-3.5 w-3.5" />
                Copy link
              </Button>
              <Button variant="outline" size="sm" asChild>
                <a
                  href={`${API_URL}/api/demo/${jobId}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <ExternalLink className="mr-1.5 h-3.5 w-3.5" />
                  Open live
                </a>
              </Button>
            </div>
          )}
        </CardHeader>
        <CardContent>
          {demoReady ? (
            <iframe
              src={`${API_URL}/api/demo/${jobId}`}
              title="Mini demo preview"
              className="h-96 w-full rounded-md border bg-black"
            />
          ) : job.applied ? (
            <p className="text-sm italic text-muted-foreground">
              Not built yet — this job is in your tracker, so the hourly
              routine will build its demo on an upcoming run.
            </p>
          ) : (
            <p className="text-sm italic text-muted-foreground">
              No demo is stored for this job.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Recipient links + email finder */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Send it to</CardTitle>
          <CardDescription>
            For a cold DM, find a relevant recruiter or hiring manager using these LinkedIn searches, verify their current company, then send a connection request with a short note. Email suggestions below are unverified and are not used for connection notes.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
            <a
              href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${job.company} recruiter`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sky-400 hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Recruiters at {job.company}
            </a>
            <a
              href={`https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(`${job.company} hiring manager`)}`}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 text-sky-400 hover:underline"
            >
              <ExternalLink className="h-3.5 w-3.5" />
              Hiring managers at {job.company}
            </a>
          </div>

          <div className="rounded-lg border bg-muted/30 p-3 space-y-3">
            <div className="flex items-center gap-2 text-sm font-medium">
              <Mail className="h-4 w-4" />
              Recruiter email finder
            </div>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                value={extraName}
                onChange={(e) => setExtraName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleFindEmails();
                }}
                placeholder="Recruiter name (optional — e.g. Priya Sharma)"
                className="flex-1 rounded-md border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-ring"
              />
              <Button
                onClick={handleFindEmails}
                disabled={emailLoading}
                className="shrink-0"
              >
                {emailLoading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Mail className="h-4 w-4" />
                )}
                Find emails
              </Button>
            </div>

            {emailReport?.ok === false && (
              <p className="text-xs italic text-muted-foreground">
                {emailReport.reason === "no_domain"
                  ? "No company website known yet — the hourly routine adds it with company intel, or type a name and it will still guess once a domain is known."
                  : "Add a recruiter name above to search."}
              </p>
            )}

            {emailReport?.ok && (
              <div className="space-y-3">
                <p className="text-xs text-muted-foreground">
                  Domain{" "}
                  <span className="font-mono">{emailReport.domain}</span>
                  {emailReport.mx_ok
                    ? " receives mail"
                    : " has no mail server"}
                  {emailReport.smtp_checked
                    ? emailReport.catch_all
                      ? " · accepts every address (can't confirm a single mailbox)"
                      : " · mailboxes verified live"
                    : " · guessed from the usual patterns (live verify unavailable here)"}
                  .
                </p>
                {(emailReport.contacts || []).map((c) => (
                  <div key={c.name} className="space-y-1.5">
                    <p className="text-sm font-medium">{c.name}</p>
                    <div className="space-y-1">
                      {c.candidates.map((cand) => (
                        <div
                          key={cand.email}
                          className="flex items-center justify-between gap-2 rounded-md border bg-background px-2.5 py-1.5"
                        >
                          <span className="truncate font-mono text-xs">
                            {cand.email}
                          </span>
                          <div className="flex shrink-0 items-center gap-1.5">
                            <Badge
                              variant={
                                cand.status === "valid"
                                  ? "default"
                                  : "outline"
                              }
                              className="text-[10px]"
                            >
                              {cand.status === "valid"
                                ? "verified"
                                : cand.status === "catch_all"
                                  ? "catch-all"
                                  : cand.status === "invalid"
                                    ? "invalid"
                                    : cand.status === "no_mx"
                                      ? "no mail"
                                      : "guess"}
                            </Badge>
                            <button
                              onClick={() =>
                                handleCopyText(cand.email, cand.email)
                              }
                              className="text-muted-foreground hover:text-foreground"
                              title="Copy"
                            >
                              {copied === cand.email ? (
                                <Check className="h-3.5 w-3.5" />
                              ) : (
                                <Copy className="h-3.5 w-3.5" />
                              )}
                            </button>
                            <a
                              href={`mailto:${cand.email}`}
                              className="text-sky-400 hover:text-sky-300"
                              title="Compose"
                            >
                              <Mail className="h-3.5 w-3.5" />
                            </a>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                ))}
                <p className="text-[11px] italic text-muted-foreground">
                  Guesses are the standard corporate patterns; confirm before
                  sending anything important.
                </p>
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

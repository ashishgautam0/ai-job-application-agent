"use client";

import { useEffect, useState } from "react";
import {
  getDashboard,
  getFollowUps,
  getColdDmTodos,
  getHrEmailTodos,
  setHrEmailTodoCompleted,
  getFollowUpDraft,
  getWeeklyTrend,
  getPlatformEffectiveness,
  getStatusFunnel,
  getRoleAnalysis,
  getFollowUpEffectiveness,
} from "@/lib/api";
import type {
  DashboardStats,
  FollowUp,
  ColdDmTodo,
  HrEmailTodo,
  FollowUpDraft,
  FollowUpEffectiveness,
  WeeklyTrend,
  PlatformEffectiveness,
  StatusFunnel,
  RoleAnalysis,
} from "@/lib/types";

import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import {
  Briefcase,
  Clock,
  Copy,
  Loader2,
  MessageSquareText,
  ThumbsUp,
  Trophy,
  XCircle,
  AlertTriangle,
  TrendingUp,
  Moon,
  BarChart3,
  CheckCircle2,
  Mail,
  Send,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { toast } from "sonner";

// Fallback only — the backend sends the real target with the stats.
const DEFAULT_DAILY_TARGET = 10;
const indiaToday = () => new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Kolkata", year: "numeric", month: "2-digit", day: "2-digit",
}).format(new Date());

export default function DashboardPage() {
  const router = useRouter();
  const [loading, setLoading] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [followUps, setFollowUps] = useState<FollowUp[]>([]);
  const [coldDmTodos, setColdDmTodos] = useState<ColdDmTodo[]>([]);
  const [coldDmLoadError, setColdDmLoadError] = useState(false);
  const [hrEmailTodos, setHrEmailTodos] = useState<HrEmailTodo[]>([]);
  const [hrEmailSaving, setHrEmailSaving] = useState<number | null>(null);
  const [hrEmailLoadError, setHrEmailLoadError] = useState(false);
  const [fuDrafts, setFuDrafts] = useState<Record<number, FollowUpDraft>>({});
  const [fuOpen, setFuOpen] = useState<Set<number>>(new Set());
  const [fuLoading, setFuLoading] = useState<number | null>(null);

  const toggleFuDraft = async (fu: FollowUp) => {
    const id = fu.id;
    if (fuOpen.has(id)) {
      setFuOpen((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      return;
    }
    let draft = fuDrafts[id];
    if (!draft) {
      setFuLoading(id);
      try {
        draft = await getFollowUpDraft(id);
        setFuDrafts((prev) => ({ ...prev, [id]: draft }));
      } catch {
        toast.error("Failed to load the follow-up draft");
        return;
      } finally {
        setFuLoading(null);
      }
    }
    if (draft.status === "ready" && draft.content) {
      setFuOpen((prev) => new Set(prev).add(id));
    } else if (draft.status === "pending") {
      toast.info("Queued — the next hourly run writes this follow-up.");
    } else {
      toast.info(
        "Not queued yet — the hourly run queues it now that the follow-up date is due."
      );
    }
  };
  const [weeklyTrend, setWeeklyTrend] = useState<WeeklyTrend[]>([]);
  const [platformData, setPlatformData] = useState<PlatformEffectiveness[]>([]);
  const [statusFunnel, setStatusFunnel] = useState<StatusFunnel | null>(null);
  const [roleAnalysis, setRoleAnalysis] = useState<RoleAnalysis[]>([]);
  const [effectiveness, setEffectiveness] = useState<FollowUpEffectiveness | null>(null);
  const [dataErrors, setDataErrors] = useState<Set<string>>(new Set());

  useEffect(() => {
    async function fetchAll() {
      try {
        const [
          dashboardRes,
          followUpsRes,
          coldDmTodosRes,
          hrEmailTodosRes,
          weeklyTrendRes,
          platformRes,
          statusFunnelRes,
          roleAnalysisRes,
          effectivenessRes,
        ] = await Promise.allSettled([
          getDashboard(),
          getFollowUps(),
          getColdDmTodos(),
          getHrEmailTodos(),
          getWeeklyTrend(),
          getPlatformEffectiveness(),
          getStatusFunnel(),
          getRoleAnalysis(),
          getFollowUpEffectiveness(),
        ]);

        if (dashboardRes.status === "fulfilled") setStats(dashboardRes.value);
        if (followUpsRes.status === "fulfilled") setFollowUps(Array.isArray(followUpsRes.value) ? followUpsRes.value : []);
        if (coldDmTodosRes.status === "fulfilled") setColdDmTodos(Array.isArray(coldDmTodosRes.value) ? coldDmTodosRes.value : []);
        setColdDmLoadError(coldDmTodosRes.status === "rejected");
        if (hrEmailTodosRes.status === "fulfilled") setHrEmailTodos(Array.isArray(hrEmailTodosRes.value) ? hrEmailTodosRes.value : []);
        setHrEmailLoadError(hrEmailTodosRes.status === "rejected");
        if (weeklyTrendRes.status === "fulfilled") setWeeklyTrend(Array.isArray(weeklyTrendRes.value) ? weeklyTrendRes.value : []);
        if (platformRes.status === "fulfilled") setPlatformData(Array.isArray(platformRes.value) ? platformRes.value : []);
        if (statusFunnelRes.status === "fulfilled" && statusFunnelRes.value && typeof statusFunnelRes.value === "object") setStatusFunnel(statusFunnelRes.value);
        if (roleAnalysisRes.status === "fulfilled") setRoleAnalysis(Array.isArray(roleAnalysisRes.value) ? roleAnalysisRes.value : []);
        if (effectivenessRes.status === "fulfilled") setEffectiveness(effectivenessRes.value);
        const named = [["weekly trend", weeklyTrendRes], ["platform effectiveness", platformRes], ["status breakdown", statusFunnelRes], ["role analysis", roleAnalysisRes]] as const;
        setDataErrors(new Set(named.filter(([, result]) => result.status === "rejected").map(([name]) => name)));
      } catch (err) {
        console.error("Failed to load dashboard data", err);
      } finally {
        setLoading(false);
      }
    }

    fetchAll();
  }, []);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-24">
        <p className="text-muted-foreground text-lg">Loading...</p>
      </div>
    );
  }

  const weeklyMax = Math.max(...weeklyTrend.map((row) => row.total), 1);

  const statCards = [
    {
      label: "Total Applied",
      value: stats?.total ?? 0,
      icon: Briefcase,
      color: "text-blue-400",
    },
    {
      label: "Awaiting Response",
      value: stats?.applied ?? 0,
      icon: Clock,
      color: "text-yellow-400",
    },
    {
      label: "Interviews",
      value: stats?.interview ?? 0,
      icon: ThumbsUp,
      color: "text-emerald-400",
    },
    {
      label: "Offers",
      value: stats?.offer ?? 0,
      icon: Trophy,
      color: "text-purple-400",
    },
    {
      label: "Rejected",
      value: stats?.rejected ?? 0,
      icon: XCircle,
      color: "text-red-400",
    },
  ];

  const todayCount = stats?.today ?? 0;
  const dailyTarget = stats?.daily_target || DEFAULT_DAILY_TARGET;
  const dailyPct = Math.min(Math.round((todayCount / dailyTarget) * 100), 100);
  const dailyMet = todayCount >= dailyTarget;
  // A due Cold DM is listed only once its note is ready to review; the rest
  // are counted, not shown, until the routine writes their note.
  const readyColdDms = coldDmTodos.filter((todo) => todo.cold_dm_ready && todo.scraped_job_id);
  const waitingColdDms = coldDmTodos.length - readyColdDms.length;
  // A follow-up is sendable only with both halves: the written draft and an
  // address to send it to. The rest are counted, not shown.
  const readyFollowUps = followUps.filter((fu) => fu.draft_ready && fu.recipient);
  const waitingFollowUps = followUps.length - readyFollowUps.length;
  const dmCount = stats?.dms_today ?? 0;
  const dmTarget = stats?.dm_target || DEFAULT_DAILY_TARGET;
  const dmPct = Math.min(Math.round((dmCount / dmTarget) * 100), 100);
  const dmMet = dmCount >= dmTarget;
  const hrCount = stats?.hr_emails_today ?? 0;
  const hrTarget = stats?.hr_email_target || DEFAULT_DAILY_TARGET;
  const hrPct = Math.min(Math.round((hrCount / hrTarget) * 100), 100);
  const hrMet = hrCount >= hrTarget;

  return (
    <div className="space-y-8">
      {/* ---- Page Header ---- */}
      <div>
        <h1 className="text-2xl sm:text-3xl font-bold tracking-tight">Dashboard</h1>
        <p className="text-muted-foreground mt-1">
          Your job search at a glance — stats, trends, and follow-ups.
        </p>
      </div>


      {/* ---- Empty State CTA ---- */}
      {stats?.total === 0 && (
        <Card className="border-dashed border-2">
          <CardContent className="flex flex-col items-center gap-4 pt-8 pb-8 text-center">
            <Moon className="h-12 w-12 text-muted-foreground" />
            <div>
              <p className="text-lg font-semibold">No applications yet</p>
              <p className="text-muted-foreground text-sm mt-1">
                Generate the Claude Desktop prompt in Settings and paste it into Claude
                Desktop. Every job it applies to is recorded here.
              </p>
            </div>
            <Button asChild>
              <Link href="/settings">
                <Moon className="mr-2 h-4 w-4" />
                Get the desktop prompt
              </Link>
            </Button>
          </CardContent>
        </Card>
      )}

      {/* ---- Stat Cards ---- */}
      <div className="grid grid-cols-2 gap-4 md:grid-cols-5">
        {statCards.map((s) => (
          <Card key={s.label}>
            <CardContent className="flex flex-col items-center gap-2 pt-6 text-center">
              <s.icon className={`h-6 w-6 ${s.color}`} />
              <p className="text-muted-foreground text-xs font-medium uppercase tracking-wide">
                {s.label}
              </p>
              <p className="text-3xl font-bold">{s.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* ---- Daily Targets: applications, cold DMs, then HR emails ---- */}
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <TrendingUp className="h-5 w-5 text-emerald-400" />
              Daily Target — Applications
            </CardTitle>
            <CardDescription>
              {todayCount} / {dailyTarget} applications today across LinkedIn, Indeed and Wellfound
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Progress value={dailyPct} className="h-3" />
            <p className="text-muted-foreground text-right text-sm">
              {dailyMet
                ? "Target reached — the agent moves on to cold DMs"
                : `${dailyTarget - todayCount} to go · ${dailyPct}%`}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Send className="h-5 w-5 text-sky-400" />
              Daily Target — Cold DMs
            </CardTitle>
            <CardDescription>
              {dmCount} / {dmTarget} LinkedIn connection notes sent today
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Progress value={dmPct} className="h-3" />
            <p className="text-muted-foreground text-right text-sm">
              {dmMet
                ? "Target reached — the agent stops for today"
                : `${dmTarget - dmCount} to go · ${dmPct}%`}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Mail className="h-5 w-5 text-amber-400" />
              Daily Target — HR Emails
            </CardTitle>
            <CardDescription>
              {hrCount} / {hrTarget} company HR emails sent today
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <Progress value={hrPct} className="h-3" />
            <p className="text-muted-foreground text-right text-sm">
              {hrMet
                ? "Target reached — the rest wait for tomorrow"
                : `${hrTarget - hrCount} to go · ${hrPct}%`}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* ---- Immediate Company HR email todos ---- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Mail className="h-5 w-5 text-sky-400" />
            Email Company HR
          </CardTitle>
          <CardDescription>
            Due 7 days after you apply — the same first-outreach day as the cold DM.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {hrEmailLoadError ? (
            <p className="text-sm text-red-400">Company HR email todos could not be loaded.</p>
          ) : hrEmailTodos.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No Company HR emails waiting.
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {hrEmailTodos.map((todo) => (
                <div
                  key={todo.id}
                  role="link"
                  tabIndex={0}
                  onClick={() => todo.scraped_job_id ? router.push(`/jobs/${todo.scraped_job_id}`) : toast.info("This tracker record has no matching scraped-job detail page.")}
                  onKeyDown={(event) => {
                    if (event.target === event.currentTarget && (event.key === "Enter" || event.key === " ")) {
                      event.preventDefault();
                      if (todo.scraped_job_id) router.push(`/jobs/${todo.scraped_job_id}`);
                      else toast.info("This tracker record has no matching scraped-job detail page.");
                    }
                  }}
                  className="cursor-pointer space-y-3 rounded-lg border border-sky-500/40 bg-sky-500/5 p-4 hover:border-primary/60"
                >
                  <div>
                    <div className="flex items-center justify-between gap-2">
                      <p className="font-semibold">{todo.company}</p>
                      <Badge variant="outline" className="border-sky-500/30 bg-sky-500/10 text-sky-400">
                        Todo now
                      </Badge>
                    </div>
                    <p className="text-muted-foreground text-sm">{todo.role}</p>
                  </div>
                  <div className="flex gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      className="flex-1"
                      onClick={(event) => {
                        event.stopPropagation();
                        if (todo.scraped_job_id) router.push(`/jobs/${todo.scraped_job_id}`);
                        else toast.info("This tracker record has no matching scraped-job detail page.");
                      }}
                    >
                      <Mail className="mr-1.5 h-3.5 w-3.5" />
                      Open email
                    </Button>
                    <Button
                      size="sm"
                      disabled={hrEmailSaving === todo.id}
                      onClick={async (event) => {
                        event.stopPropagation();
                        setHrEmailSaving(todo.id);
                        try {
                          await setHrEmailTodoCompleted(todo.id);
                          setHrEmailTodos((current) => current.filter((item) => item.id !== todo.id));
                          toast.success("Company HR email marked sent");
                        } catch {
                          toast.error("Failed to update the Company HR email todo");
                        } finally {
                          setHrEmailSaving(null);
                        }
                      }}
                    >
                      {hrEmailSaving === todo.id ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : <CheckCircle2 className="mr-1.5 h-3.5 w-3.5" />}
                      Mark emailed
                    </Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ---- Cold DMs Due: same existing follow-up schedule ---- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <MessageSquareText className="h-5 w-5 text-violet-400" />
            Cold DMs Due
          </CardTitle>
          <CardDescription>
            LinkedIn connection notes for jobs whose existing follow-up date is due. Sending one counts as that job&apos;s follow-up; record it only after a confirmed send.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {coldDmLoadError ? (
            <p role="alert" className="text-sm text-red-400">Cold DM todos could not be loaded. Retry the dashboard before sending.</p>
          ) : (
            <div className="space-y-3">
              {readyColdDms.length === 0 ? (
                <p className="text-sm text-muted-foreground">No Cold DMs ready to review.</p>
              ) : (
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {readyColdDms.map((todo) => (
                    <Link
                      key={todo.id}
                      href={`/jobs/${todo.scraped_job_id}#cold-dm`}
                      className="block space-y-3 rounded-lg border border-violet-500/40 bg-violet-500/5 p-4 transition-colors hover:border-primary/60 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary"
                      aria-label={`Open Cold DM for ${todo.role} at ${todo.company}`}
                    >
                      <div className="flex items-start justify-between gap-2">
                        <div><p className="font-semibold">{todo.company}</p><p className="text-sm text-muted-foreground">{todo.role}</p></div>
                        <Badge variant="outline" className="shrink-0 border-violet-500/30 bg-violet-500/10 text-violet-400">
                          Ready to review
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground">Due {todo.follow_up_date}</p>
                      <span className="inline-flex items-center text-sm font-medium text-violet-400">Open Cold DM</span>
                    </Link>
                  ))}
                </div>
              )}
              {waitingColdDms > 0 && (
                <p className="text-xs text-muted-foreground">
                  {waitingColdDms} more {waitingColdDms === 1 ? "is" : "are"} due but not ready yet — each appears here once its note is written for your current resume.
                </p>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {/* ---- Follow-ups Due ---- */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AlertTriangle className="h-5 w-5 text-amber-400" />
            Follow-ups Due
          </CardTitle>
          <CardDescription>
            Due seven days after a recorded LinkedIn connection note; opens the same Tracker job.
            Listed once the draft is written and a recipient is on record.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {followUps.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No follow-ups due. You&apos;re all caught up!
            </p>
          ) : readyFollowUps.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No follow-ups ready to send. {waitingFollowUps}{" "}
              {waitingFollowUps === 1 ? "is" : "are"} due but still waiting on a written
              draft or a verified recipient.
            </p>
          ) : (
            <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
              {readyFollowUps.map((fu) => {
                const today = indiaToday();
                const isOverdue = fu.follow_up_date < today;
                const isDueToday = fu.follow_up_date === today;
                const borderClass = isOverdue
                  ? "border-red-500/60 bg-red-500/5"
                  : isDueToday
                    ? "border-amber-500/60 bg-amber-500/10"
                    : "border-amber-500/40 bg-amber-500/5";
                const dateClass = isOverdue
                  ? "text-red-400 font-medium"
                  : isDueToday
                    ? "text-amber-400 font-medium"
                    : "text-amber-400";
                return (
                  <div
                    key={fu.id}
                    role="link"
                    tabIndex={0}
                    onClick={() => fu.scraped_job_id ? router.push(`/jobs/${fu.scraped_job_id}`) : toast.info("This tracker record has no matching scraped-job detail page.")}
                    onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); if (fu.scraped_job_id) router.push(`/jobs/${fu.scraped_job_id}`); else toast.info("This tracker record has no matching scraped-job detail page."); } }}
                    className={`rounded-lg border p-4 space-y-1 ${borderClass} cursor-pointer hover:border-primary/60`}
                  >
                    <div className="flex items-center justify-between">
                      <p className="font-semibold">{fu.company}</p>
                      <div className="flex items-center gap-1.5">
                        {isOverdue && (
                          <Badge variant="outline" className="text-[10px] h-5 bg-red-500/10 text-red-400 border-red-500/30">
                            Overdue
                          </Badge>
                        )}
                        {isDueToday && (
                          <Badge variant="outline" className="text-[10px] h-5 bg-amber-500/10 text-amber-400 border-amber-500/30">
                            Today
                          </Badge>
                        )}
                        {(fu.follow_up_count ?? 0) > 0 && (
                          <Badge variant="outline" className="text-[10px] h-5 bg-amber-500/10 text-amber-400 border-amber-500/30">
                            #{(fu.follow_up_count ?? 0) + 1} of 3
                          </Badge>
                        )}
                      </div>
                    </div>
                    <p className="text-muted-foreground text-sm">{fu.role}</p>
                    <div className="flex items-center justify-between pt-1">
                      <span className={`text-xs ${dateClass}`}>
                        {fu.follow_up_date}
                      </span>
                      <div className="flex items-center gap-2">
                        <span className="text-muted-foreground text-xs capitalize">
                          {fu.status}
                        </span>
                      </div>
                    </div>
                    {fuOpen.has(fu.id) && fuDrafts[fu.id]?.content && (
                      <div className="rounded-md border border-emerald-600/30 bg-emerald-600/5 p-3 space-y-2 mt-2">
                        <div className="flex items-center justify-between">
                          <span className="text-xs font-medium text-emerald-400">
                            Follow-up #{fuDrafts[fu.id]?.follow_up_number ?? 1}{" "}
                            (auto-written)
                          </span>
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-6 px-2 text-xs"
                            onClick={(e) => {
                              e.stopPropagation();
                              navigator.clipboard.writeText(
                                fuDrafts[fu.id]?.content || ""
                              );
                              toast.success("Follow-up copied to clipboard");
                            }}
                          >
                            <Copy className="mr-1 h-3 w-3" />
                            Copy
                          </Button>
                        </div>
                        <p className="text-sm leading-relaxed whitespace-pre-wrap break-words">
                          {fuDrafts[fu.id]?.content}
                        </p>
                      </div>
                    )}
                    <div className="flex gap-2 pt-2">
                      <Button
                        variant="outline"
                        size="sm"
                        className="flex-1"
                        disabled={fuLoading === fu.id}
                        onClick={(e) => { e.stopPropagation(); toggleFuDraft(fu); }}
                      >
                        {fuLoading === fu.id ? (
                          <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <MessageSquareText className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        {fuOpen.has(fu.id) ? "Hide Draft" : "View Draft"}
                      </Button>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
          {readyFollowUps.length > 0 && waitingFollowUps > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              {waitingFollowUps} more {waitingFollowUps === 1 ? "is" : "are"} due but not
              ready — each appears here once its draft is written and a recipient is on record.
            </p>
          )}
        </CardContent>
      </Card>

      {/* ---- Follow-up Effectiveness ---- */}
      {effectiveness && effectiveness.overall.total > 0 && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BarChart3 className="h-5 w-5 text-blue-400" />
              Follow-up Effectiveness
            </CardTitle>
            <CardDescription>
              Response rates from your follow-up messages.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex items-center gap-6">
              <div className="text-center">
                <p className="text-3xl font-bold">{effectiveness.overall.rate}%</p>
                <p className="text-xs text-muted-foreground">Response Rate</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold">{effectiveness.overall.total}</p>
                <p className="text-xs text-muted-foreground">Total Sent</p>
              </div>
              <div className="text-center">
                <p className="text-2xl font-bold text-emerald-400">{effectiveness.overall.responded}</p>
                <p className="text-xs text-muted-foreground">Responded</p>
              </div>
            </div>
            {effectiveness.by_channel.length > 1 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <p className="text-sm font-medium text-muted-foreground">By Channel</p>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {effectiveness.by_channel.map((ch) => (
                      <div key={ch.channel} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                        <span>{ch.channel}</span>
                        <span className="text-muted-foreground">{ch.responded}/{ch.total} ({ch.rate}%)</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
            {effectiveness.by_number.length > 1 && (
              <>
                <Separator />
                <div className="space-y-2">
                  <p className="text-sm font-medium text-muted-foreground">By Attempt</p>
                  <div className="grid gap-2 sm:grid-cols-3">
                    {effectiveness.by_number.map((n) => (
                      <div key={n.follow_up_number} className="flex items-center justify-between rounded-md border px-3 py-2 text-sm">
                        <span>Follow-up #{n.follow_up_number}</span>
                        <span className="text-muted-foreground">{n.rate}%</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      )}

      <Separator />

      {/* ---- Platform Effectiveness ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Platform Effectiveness</CardTitle>
          <CardDescription>
            Positive response rate = Interview or Offer ÷ all persisted applications on each platform.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dataErrors.has("platform effectiveness") ? <p className="text-sm text-red-400">Platform data could not be loaded.</p> : platformData.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No platform data available yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Platform</TableHead>
                  <TableHead className="text-right">Applications</TableHead>
                  <TableHead className="text-right">Responses</TableHead>
                  <TableHead className="w-[200px]">Rate</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {platformData.map((p) => (
                  <TableRow key={p.platform}>
                    <TableCell className="font-medium">{p.platform}</TableCell>
                    <TableCell className="text-right">
                      {p.applications}
                    </TableCell>
                    <TableCell className="text-right">{p.responses}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <Progress
                          value={p.response_rate ?? 0}
                          className="h-2 flex-1"
                        />
                        <span className="text-muted-foreground w-12 text-right text-xs">
                          {(p.response_rate ?? 0).toFixed(1)}%
                        </span>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ---- Status Funnel ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Status Funnel</CardTitle>
          <CardDescription>
            Breakdown of applications by current status.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dataErrors.has("status breakdown") ? <p className="text-sm text-red-400">Status data could not be loaded.</p> : !statusFunnel || Object.keys(statusFunnel).length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No status data available yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Count</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(statusFunnel).map(([status, count]) => (
                  <TableRow key={status}>
                    <TableCell className="font-medium capitalize">
                      {status}
                    </TableCell>
                    <TableCell className="text-right">{count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      {/* ---- Weekly Trend ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Weekly Trend</CardTitle>
          <CardDescription>
            Application volume over recent weeks.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dataErrors.has("weekly trend") ? <p className="text-sm text-red-400">Weekly trend could not be loaded.</p> : weeklyTrend.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No weekly trend data available yet.
            </p>
          ) : (
            <div className="space-y-3" aria-label="Weekly application trend graph">
              {weeklyTrend.map((w) => {
                return <div key={w.week} className="grid grid-cols-[7rem_1fr_2rem] items-center gap-3 text-sm">
                  <span className="text-muted-foreground">{w.week}</span>
                  <div className="flex h-6 overflow-hidden rounded bg-muted" title={`${w.total} applications: ${w.Job || 0} jobs, ${w.Internship || 0} internships`}>
                    <div className="bg-sky-500" style={{ width: `${((w.Job || 0) / weeklyMax) * 100}%` }} />
                    <div className="bg-violet-500" style={{ width: `${((w.Internship || 0) / weeklyMax) * 100}%` }} />
                  </div><strong className="text-right">{w.total}</strong>
                </div>;
              })}
              <p className="text-xs text-muted-foreground"><span className="text-sky-400">■</span> Jobs · <span className="text-violet-400">■</span> Internships · weeks start Monday</p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ---- Role Analysis ---- */}
      <Card>
        <CardHeader>
          <CardTitle>Role Analysis</CardTitle>
          <CardDescription>
            Mutually exclusive role families from persisted job titles, with observed positive responses.
          </CardDescription>
        </CardHeader>
        <CardContent>
          {dataErrors.has("role analysis") ? <p className="text-sm text-red-400">Role analysis could not be loaded.</p> : roleAnalysis.length === 0 ? (
            <p className="text-muted-foreground text-sm">
              No role analysis data available yet.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Role Keyword</TableHead>
                  <TableHead className="text-right">Applied</TableHead>
                  <TableHead className="text-right">Responses</TableHead>
                  <TableHead className="text-right">Rate</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {roleAnalysis.map((r) => (
                  <TableRow key={r.role_keyword}>
                    <TableCell className="font-medium">
                      <div>{r.role_keyword}</div>{r.example_roles?.length ? <div className="text-xs font-normal text-muted-foreground">{r.example_roles.join(" · ")}</div> : null}
                    </TableCell>
                    <TableCell className="text-right">{r.applied}</TableCell>
                    <TableCell className="text-right">{r.responses}</TableCell>
                    <TableCell className="text-right">
                      {(r.response_rate ?? 0).toFixed(1)}%
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

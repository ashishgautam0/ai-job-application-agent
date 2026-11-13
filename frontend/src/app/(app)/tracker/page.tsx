"use client";

import { useCallback, useEffect, useState } from "react";
import { getApplications } from "@/lib/api";
import type { Application } from "@/lib/types";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ClipboardList, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";

const PLATFORMS = [
  "LinkedIn", "Wellfound", "YC WaaS", "Internshala", "Instahyre",
  "Naukri", "Indeed", "HasJob", "Direct", "Referral", "Other",
] as const;

const STATUSES = [
  "Applied", "Follow-up Sent", "Assignment Submitted", "Interview",
  "Offer", "Rejected", "Ghosted", "Not Interested",
] as const;

const STATUS_COLORS: Record<string, string> = {
  Applied: "bg-blue-500/15 text-blue-400 border-blue-500/30",
  "Follow-up Sent": "bg-yellow-500/15 text-yellow-400 border-yellow-500/30",
  "Assignment Submitted": "bg-cyan-500/15 text-cyan-400 border-cyan-500/30",
  Interview: "bg-emerald-500/15 text-emerald-400 border-emerald-500/30",
  Offer: "bg-purple-500/15 text-purple-400 border-purple-500/30",
  Rejected: "bg-red-500/15 text-red-400 border-red-500/30",
  Ghosted: "bg-gray-500/15 text-gray-400 border-gray-500/30",
  "Not Interested": "bg-orange-500/15 text-orange-400 border-orange-500/30",
};

export default function TrackerPage() {
  const [applications, setApplications] = useState<Application[]>([]);
  const [appPage, setAppPage] = useState(1);
  const [appsLoading, setAppsLoading] = useState(true);
  const [filterStatus, setFilterStatus] = useState("All");
  const [filterPlatform, setFilterPlatform] = useState("All");
  const APPS_PER_PAGE = 10;

  const fetchApplications = useCallback(async () => {
    setAppsLoading(true);
    try {
      const filters: { status?: string; platform?: string } = {};
      if (filterStatus !== "All") filters.status = filterStatus;
      if (filterPlatform !== "All") filters.platform = filterPlatform;
      setApplications(await getApplications(filters));
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "Failed to load applications");
    } finally {
      setAppsLoading(false);
    }
  }, [filterPlatform, filterStatus]);

  useEffect(() => {
    fetchApplications();
  }, [fetchApplications]);

  useEffect(() => {
    setAppPage(1);
  }, [filterPlatform, filterStatus]);

  const appTotalPages = Math.max(1, Math.ceil(applications.length / APPS_PER_PAGE));
  const appSafePage = Math.min(appPage, appTotalPages);
  const pagedApplications = applications.slice(
    (appSafePage - 1) * APPS_PER_PAGE,
    appSafePage * APPS_PER_PAGE,
  );

  // A new tab, so the list you are working through keeps its filters, its
  // page and its scroll position instead of being navigated away.
  function openJobPage(app: Application) {
    if (app.scraped_job_id) {
      window.open(`/jobs/${app.scraped_job_id}`, "_blank", "noopener,noreferrer");
      return;
    }
    toast.info("This tracker record has no matching scraped-job detail page.");
  }

  function handleCardKeyDown(event: React.KeyboardEvent<HTMLDivElement>, app: Application) {
    if (event.target !== event.currentTarget) return;
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      openJobPage(app);
    }
  }

  return (
    <div className="space-y-8">
      <div>
        <h1 className="flex items-center gap-3 text-2xl font-bold tracking-tight sm:text-3xl">
          <ClipboardList className="h-8 w-8" />
          Application Tracker
        </h1>
        <p className="mt-1 text-muted-foreground">
          Select a tracker card to view its details and manage the application.
        </p>
      </div>

      <div className="flex flex-wrap items-end gap-3">
        <Select value={filterStatus} onValueChange={setFilterStatus}>
          <SelectTrigger className="w-[150px]">
            <SelectValue placeholder="Status" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="All">All Statuses</SelectItem>
            {STATUSES.map((status) => (
              <SelectItem key={status} value={status}>{status}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Select value={filterPlatform} onValueChange={setFilterPlatform}>
          <SelectTrigger className="w-[140px]">
            <SelectValue placeholder="Platform" />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="All">All Platforms</SelectItem>
            {PLATFORMS.map((platform) => (
              <SelectItem key={platform} value={platform}>{platform}</SelectItem>
            ))}
          </SelectContent>
        </Select>

        <span className="ml-auto text-sm text-muted-foreground">
          {applications.length} application{applications.length !== 1 && "s"}
        </span>
      </div>

      <div className="space-y-3">
        {appsLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
          </div>
        ) : applications.length === 0 ? (
          <Card>
            <CardContent className="py-12 text-center text-muted-foreground">
              No applications found. Applications you apply to appear here.
            </CardContent>
          </Card>
        ) : (
          <div className="space-y-3">
            {pagedApplications.map((app) => {
              const hasDetail = Boolean(app.scraped_job_id);
              return (
                <Card
                  key={app.id}
                  role="link"
                  tabIndex={0}
                  aria-label={`Open tracker details for ${app.role} at ${app.company}`}
                  aria-disabled={!hasDetail}
                  onClick={() => openJobPage(app)}
                  onKeyDown={(event) => handleCardKeyDown(event, app)}
                  className={cn(
                    "transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
                    hasDetail
                      ? "cursor-pointer hover:border-primary/60 hover:bg-muted/40"
                      : "cursor-not-allowed opacity-70",
                  )}
                >
                  <CardContent className="flex flex-wrap items-center gap-x-3 gap-y-2 py-4">
                    <Badge
                      variant="outline"
                      className={cn("text-xs font-medium", STATUS_COLORS[app.status])}
                    >
                      {app.status}
                    </Badge>
                    <div className="min-w-0 flex-1 basis-full sm:basis-auto">
                      <div className="flex min-w-0 flex-col gap-1 sm:flex-row sm:items-center sm:gap-3">
                        <span className="truncate font-bold">{app.company}</span>
                        <span className="truncate text-sm text-muted-foreground sm:text-base">
                          {app.role}
                        </span>
                      </div>
                    </div>
                    <Badge variant="secondary">{app.platform}</Badge>
                    {(app.follow_up_count ?? 0) > 0 && (
                      <Badge variant="outline" className="border-amber-500/30 bg-amber-500/10 text-xs text-amber-400">
                        {app.follow_up_count}/3 follow-ups
                      </Badge>
                    )}
                    {!hasDetail && (
                      <span className="text-xs text-muted-foreground">Detail unavailable</span>
                    )}
                  </CardContent>
                </Card>
              );
            })}

            {appTotalPages > 1 && (
              <div className="flex flex-wrap items-center justify-center gap-3 pt-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={appSafePage <= 1}
                  onClick={() => setAppPage(appSafePage - 1)}
                >
                  Previous
                </Button>
                <span className="text-sm tabular-nums text-muted-foreground">
                  Page {appSafePage} of {appTotalPages}
                </span>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={appSafePage >= appTotalPages}
                  onClick={() => setAppPage(appSafePage + 1)}
                >
                  Next
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

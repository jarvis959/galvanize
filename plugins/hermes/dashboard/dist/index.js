/**
 * Galvanize — Triggers dashboard tab
 *
 * Job management for event triggers (the cron-page analogue): per-trigger
 * watching state, last fire, fires today, last error, and row actions
 * (test-fire / enable-toggle / remove). MANAGES ONLY — creating triggers
 * stays agent/conversation-first by design.
 *
 * Plain IIFE, no build step. Uses window.__HERMES_PLUGIN_SDK__ (React +
 * host components + authed fetchJSON). Talks to /api/plugins/galvanize/.
 */
(function () {
  "use strict";

  var SDK = window.__HERMES_PLUGIN_SDK__;
  if (!SDK) return;

  var React = SDK.React;
  var h = React.createElement;
  var useState = SDK.hooks.useState;
  var useEffect = SDK.hooks.useEffect;
  var useCallback = SDK.hooks.useCallback;
  var Badge = SDK.components.Badge;
  var Button = SDK.components.Button;
  var Card = SDK.components.Card;
  var CardContent = SDK.components.CardContent;
  var cn = SDK.utils.cn || function () { return Array.prototype.slice.call(arguments).filter(Boolean).join(" "); };
  var timeAgo = SDK.utils.timeAgo;

  var API = "/api/plugins/galvanize";
  var POLL_MS = 5000;

  function errText(e) {
    if (!e) return "unknown error";
    if (e.detail && typeof e.detail === "string") return e.detail;
    return String(e.message || e);
  }

  function HealthChip(props) {
    var ok = props.ok, label = props.label, hint = props.note;
    return h("span", {
      className: cn("inline-flex items-center gap-1.5 text-xs",
        ok ? "text-green-600" : "text-red-600"),
      title: hint || "",
    }, h("span", {
      className: cn("inline-block w-2 h-2 rounded-full",
        ok ? "bg-green-500" : "bg-red-500"),
    }), label);
  }

  function Row(props) {
    var t = props.trigger;
    var busy = props.busy;
    var onAction = props.onAction;

    var wakeLabel = t.wake === "hermes" ? "Hermes" : t.wake;
    var srcLabel = { folder: "folder", imap: "email", webhook: "webhook", emit: "emit" }[t.source] || t.source;
    var detail = t.path || t.mailbox || "";

    return h("tr", { className: "border-b border-border/40" },
      h("td", { className: "py-2 pr-3 align-top" },
        h("div", { className: "flex items-center gap-2" },
          h("span", {
            className: cn("inline-block w-2 h-2 rounded-full shrink-0",
              !t.enabled ? "bg-muted-foreground/40"
                : t.watching ? "bg-green-500" : "bg-amber-500"),
            title: !t.enabled ? "disabled" : t.watching ? "watching" : "not watching",
          }),
          h("span", { className: "font-medium" }, t.name),
          t.description ? h("span", { className: "text-muted-foreground text-xs truncate max-w-52" },
            t.description) : null)),
      h("td", { className: "py-2 pr-3 align-top text-xs" },
        h(Badge, { variant: "outline" }, srcLabel),
        detail ? h("div", { className: "text-muted-foreground mt-1 truncate max-w-64", title: detail },
          detail) : null),
      h("td", { className: "py-2 pr-3 align-top text-xs text-muted-foreground" }, wakeLabel),
      h("td", { className: "py-2 pr-3 align-top text-xs" },
        t.last_fire === "never" || !t.last_fire
          ? h("span", { className: "text-muted-foreground" }, "never") : t.last_fire),
      h("td", { className: "py-2 pr-3 align-top text-xs tabular-nums" }, String(t.fires_today || 0)),
      h("td", { className: "py-2 pr-3 align-top text-xs" },
        t.last_error
          ? h("span", { className: "text-red-600", title: t.last_error },
            String(t.last_error).slice(0, 60))
          : h("span", { className: "text-muted-foreground" }, "—")),
      h("td", { className: "py-2 align-top text-right whitespace-nowrap" },
        h(Button, {
          size: "sm", variant: "ghost", disabled: busy,
          title: "Send a synthetic test event through the real dispatch path",
          onClick: function () { onAction("test", t); },
        }, "Test"),
        h(Button, {
          size: "sm", variant: "ghost", disabled: busy,
          onClick: function () { onAction("toggle", t); },
        }, t.enabled ? "Disable" : "Enable"),
        h(Button, {
          size: "sm", variant: "ghost", disabled: busy,
          className: "text-red-600",
          onClick: function () { onAction("remove", t); },
        }, "Remove")));
  }

  function TriggersPage() {
    var state = useState(null);  var data = state[0], setData = state[1];
    var busyS = useState("");    var busy = busyS[0], setBusy = busyS[1];
    var msgS = useState("");     var msg = msgS[0], setMsg = msgS[1];
    var errS = useState("");     var err = errS[0], setErr = errS[1];

    var refresh = useCallback(function () {
      SDK.fetchJSON(API + "/status").then(function (r) {
        setData(r);
        setErr("");
      }).catch(function (e) { setErr(errText(e)); });
    }, []);

    useEffect(function () {
      refresh();
      var iv = setInterval(refresh, POLL_MS);
      return function () { clearInterval(iv); };
    }, [refresh]);

    var act = useCallback(function (kind, t) {
      if (kind === "remove" &&
          !window.confirm("Remove trigger '" + t.name +
          "'?\nThis also removes its Hermes webhook route if it had one.")) {
        return;
      }
      setBusy(t.name); setMsg(""); setErr("");
      var url = API + "/" + (kind === "toggle" ? "toggle" : kind);
      var body = kind === "toggle"
        ? { name: t.name, enabled: !t.enabled }
        : { name: t.name };
      SDK.fetchJSON(url, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(function (r) {
        if (kind === "test") {
          setMsg(r && r.ok
            ? "Test event fired: " + (r.detail || "accepted") + " \u2014 a fresh agent session is starting."
            : "Test failed: " + (r && (r.error || r.detail) || "unknown"));
        }
        refresh();
      }).catch(function (e) { setErr(errText(e)); });
      // clear busy after the refresh settles (fetchJSON has no finally chain guarantee across hosts)
      setTimeout(function () { setBusy(""); refresh(); }, 1200);
    }, [refresh]);

    if (!data) {
      return h("div", { className: "p-6 text-sm text-muted-foreground" }, "Loading triggers…");
    }

    var notes = data.notes || [];
    var triggers = data.triggers || [];

    // --- header row ---
    var header = h("div", { className: "flex items-center justify-between flex-wrap gap-2" },
      h("div", null,
        h("h2", { className: "text-lg font-semibold" }, "Triggers"),
        h("div", { className: "text-xs text-muted-foreground" },
          "Events that wake agent sessions. To create one, just tell the agent:",
          " \u201cwatch my downloads folder and wake me when a CAD file lands.\u201d")),
      h("div", { className: "flex items-center gap-3" },
        h(HealthChip, { ok: !!data.daemon_alive, label: "daemon" }),
        h(HealthChip, { ok: !!data.webhook_enabled, label: "webhook lane" }),
        h(HealthChip, { ok: !!data.gateway_running, label: "gateway" }),
        h(Button, { size: "sm", variant: "ghost", onClick: refresh }, "Refresh")));

    // --- messages / notes card ---
    var msgCard = null;
    if (err || msg || notes.length) {
      msgCard = h(Card, null,
        h(CardContent, { className: "py-3 text-sm space-y-1" },
          err ? h("div", { className: "text-red-600" }, err) : null,
          msg ? h("div", { className: "text-green-600" }, msg) : null,
          notes.map(function (n, i) {
            return h("div", { key: i, className: "text-amber-600 text-xs" }, "! " + n);
          })));
    }

    // --- table or empty state ---
    var body;
    if (triggers.length === 0) {
      body = h("div", {
        className: "border border-dashed border-border rounded-lg p-8 text-center text-sm text-muted-foreground space-y-2",
      },
        h("div", null, "No triggers yet."),
        h("div", null, "Ask your agent:",
          h("code", { className: "text-xs" }, " wake me when resumes land in my inbox "),
          " or run ",
          h("code", { className: "text-xs" }, "galvanize add folder ~/watch --wake hermes"), "."));
    } else {
      var headRow = h("tr", { className: "text-left text-xs text-muted-foreground" },
        h("th", { className: "py-2 pr-3 font-medium" }, "Trigger"),
        h("th", { className: "py-2 pr-3 font-medium" }, "Source"),
        h("th", { className: "py-2 pr-3 font-medium" }, "Wake"),
        h("th", { className: "py-2 pr-3 font-medium" }, "Last fire"),
        h("th", { className: "py-2 pr-3 font-medium" }, "Today"),
        h("th", { className: "py-2 pr-3 font-medium" }, "Last error"),
        h("th", { className: "py-2 font-medium" }, ""));
      var rows = triggers.map(function (t) {
        return h(Row, { key: t.name, trigger: t, busy: busy === t.name, onAction: act });
      });
      body = h("div", { className: "overflow-x-auto" },
        h("table", { className: "w-full text-sm" },
          h("thead", null, headRow),
          h("tbody", null, rows)));
    }

    var footer = h("div", { className: "text-xs text-muted-foreground" },
      "Managed by galvanize \u00b7 status refreshes every " + (POLL_MS / 1000) + "s");

    return h("div", { className: "p-4 space-y-4" }, header, msgCard, body, footer);
  }

  if (window.__HERMES_PLUGINS__ && typeof window.__HERMES_PLUGINS__.register === "function") {
    window.__HERMES_PLUGINS__.register("galvanize", TriggersPage);
  }
})();

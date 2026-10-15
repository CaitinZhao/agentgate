<script setup lang="ts">
// Execution-overview building blocks, styled after the report/analysis redesign.
//
// The point of these is not decoration: each one answers a question the per-case tables
// cannot. Metric cards put the headline numbers side by side (including the two readings
// and their gap), the time split shows WHERE the wall time went, the signal bars rank
// which dimension actually loses the most points. All read from GET /runs/{id}/view.
import { computed } from "vue";
import { useI18n } from "vue-i18n";

const props = withDefaults(defineProps<{
  view: any;                       // /runs/{id}/view payload
  parts?: string[] | null;         // subset of panels to render (null = all)
}>(), { parts: null });

/** whether a named panel should render (used when this block is embedded elsewhere) */
function show(part: string): boolean {
  return !props.parts || props.parts.includes(part);
}

const { t } = useI18n();

const m = computed(() => props.view?.metrics || {});
const counts = computed(() => props.view?.counts || {});
const loop = computed(() => props.view?.loop || {});

/** verdict -> semantic colour class */
function vclass(v: string): string {
  return v === "PASS" ? "ok" : v === "FAIL" ? "bad" : v === "PENDING" ? "warn" : "muted";
}
function fmtInt(n: any): string {
  return typeof n === "number" ? n.toLocaleString() : "—";
}
function fmtNum(n: any, digits = 1): string {
  return typeof n === "number" ? n.toFixed(digits) : "—";
}

// ---- time split (LLM vs tool vs wait): where the run actually spent its wall clock ----
const timeBars = computed(() => {
  const total = (m.value.llm_s || 0) + (m.value.tool_s || 0) + (m.value.wait_s || 0);
  if (!total) return [];
  return [
    { key: "llm", label: t("view.timeLlm"), s: m.value.llm_s || 0, cls: "llm" },
    { key: "tool", label: t("view.timeTool"), s: m.value.tool_s || 0, cls: "tool" },
    { key: "wait", label: t("view.timeWait"), s: m.value.wait_s || 0, cls: "wait" },
  ].map((b) => ({ ...b, pct: (b.s / total) * 100 }));
});

// ---- signal ranking: which dimension costs the most ----
const signalBars = computed(() => {
  const sc = props.view?.signal_count || {};
  const names: Record<string, string> = {
    success: t("scores.dim.success"), quality: t("scores.dim.quality"),
    reliability: t("scores.dim.reliability"), stability: t("scores.dim.stability"),
    efficiency: t("scores.dim.efficiency"), cost: t("scores.dim.cost"),
    safety: t("scores.dim.safety"),
  };
  const rows = Object.entries(sc).map(([k, v]) => ({
    key: k, label: names[k] || k, n: Number(v) || 0,
    top: (props.view?.top_signal || {})[k] || "",
  }));
  const max = Math.max(1, ...rows.map((r) => r.n));
  return rows.filter((r) => r.n > 0).sort((a, b) => b.n - a.n).map((r) => ({ ...r, pct: (r.n / max) * 100 }));
});

function fmtTok(n: number): string {
  if (n >= 1e6) return (n / 1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n / 1e3).toFixed(1) + "k";
  return String(n);
}
// ---- token distribution histogram (log-ish buckets, the mockup's cost panel) ----
const buckets = computed(() => {
  const toks = (props.view?.cases || []).map((c: any) => c.tokens || 0).filter((n: number) => n > 0).sort((a: number, b: number) => a - b);
  if (!toks.length) return [];
  const edges = [0, 5000, 10000, 20000, 40000, 80000, 160000, Infinity];
  const labels = ["<5k", "5-10k", "10-20k", "20-40k", "40-80k", "80-160k", ">160k"];
  const out = labels.map((l) => ({ label: l, n: 0 }));
  for (const tk of toks) {
    for (let i = 1; i < edges.length; i++) {
      if (tk < edges[i]) { out[i - 1].n++; break; }
    }
  }
  const max = Math.max(1, ...out.map((o) => o.n));
  const median = toks[Math.floor(toks.length / 2)];
  return out.map((o) => ({ ...o, pct: (o.n / max) * 100, median, p95: toks[Math.floor(toks.length * 0.95)] }));
});
</script>

<template>
  <div v-if="view" class="view">
    <!-- headline metrics: the numbers that direct a decision, side by side -->
    <div class="statrow" v-if="show('stats')">
      <div class="stat">
        <div class="stat-label">{{ $t("scores.total") }}</div>
        <div class="stat-value" :class="{ muted: m.total_score == null }">
          {{ m.total_score == null ? $t("view.noScore") : fmtNum(m.total_score) }}</div>
        <div class="stat-sub">{{ $t("view.weightedHint") }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ $t("view.passRate") }}</div>
        <div class="stat-value">{{ m.pass_rate == null ? "—" : fmtNum(m.pass_rate) + "%" }}</div>
        <div class="stat-sub">
          <span class="ok">{{ counts.pass }} {{ $t("verdict.PASS") }}</span> ·
          <span class="bad">{{ counts.fail }} {{ $t("verdict.FAIL") }}</span> ·
          <span class="warn">{{ counts.pending }} {{ $t("verdict.PENDING") }}</span>
        </div>
      </div>
      <div class="stat" v-if="m.native_rate != null">
        <div class="stat-label">{{ $t("view.nativeRate") }}</div>
        <div class="stat-value">{{ fmtNum(m.native_rate) }}%</div>
        <div class="stat-sub">
          {{ $t("view.readingGap") }}:
          <b :class="(m.two_reading_gap || 0) < 0 ? 'bad' : 'ok'">{{ fmtNum(m.two_reading_gap) }}</b>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">{{ $t("view.tokens") }}</div>
        <div class="stat-value">{{ fmtTok(m.tokens_total || 0) }}</div>
        <div class="stat-sub">{{ $t("view.avgWall", { s: fmtNum(m.avg_wall_s) }) }}</div>
      </div>
    </div>

    <div class="panelgrid">
      <!-- where the wall time went -->
      <div class="card" v-if="show('time') && timeBars.length">
        <h4>{{ $t("view.timeTitle") }}</h4>
        <div class="bars">
          <div v-for="b in timeBars" :key="b.key" class="barrow">
            <span class="barlabel">{{ b.label }}</span>
            <div class="bartrack"><div class="barfill" :class="b.cls" :style="{ width: b.pct + '%' }"></div></div>
            <span class="barval">{{ b.pct.toFixed(1) }}%</span>
          </div>
        </div>
        <div class="note" v-if="m.llm_share != null && m.llm_share > 80">
          {{ $t("view.llmDominant", { p: fmtNum(m.llm_share) }) }}
        </div>
      </div>

      <!-- which dimension loses the most points -->
      <div class="card" v-if="show('signals') && signalBars.length">
        <h4>{{ $t("view.signalsTitle") }}</h4>
        <div class="bars">
          <div v-for="b in signalBars" :key="b.key" class="barrow">
            <span class="barlabel">{{ b.label }}</span>
            <div class="bartrack"><div class="barfill sig" :style="{ width: b.pct + '%' }"></div></div>
            <span class="barval">{{ b.n }}</span>
          </div>
        </div>
      </div>

      <!-- loop cost: the number the per-case view cannot show -->
      <div class="card" v-if="show('loop') && loop.cases">
        <h4>{{ $t("view.loopTitle") }}</h4>
        <div class="looprow">
          <div><b class="warn">{{ loop.cases }}</b> <span class="muted">{{ $t("view.loopCases") }}</span></div>
          <div><b>{{ loop.groups }}</b> <span class="muted">{{ $t("view.loopGroups") }}</span></div>
          <div v-if="loop.longest"><b>{{ loop.longest }}×</b> <span class="muted">{{ $t("view.loopLongest") }}</span></div>
        </div>
        <div class="loopcost" v-if="loop.token_avg_loop && loop.token_avg_clean">
          <span class="muted">{{ $t("view.loopCost") }}:</span>
          <b class="bad">{{ fmtTok(loop.token_avg_loop) }}</b>
          <span class="muted">vs</span>
          <b class="ok">{{ fmtTok(loop.token_avg_clean) }}</b>
          <b>×{{ (loop.token_avg_loop / loop.token_avg_clean).toFixed(1) }}</b>
        </div>
      </div>

      <!-- token distribution -->
      <div class="card" v-if="show('hist') && buckets.length">
        <h4>{{ $t("view.costTitle") }}</h4>
        <div class="hist">
          <div v-for="b in buckets" :key="b.label" class="histcol">
            <div class="histbar" :style="{ height: b.pct + '%' }" :title="b.n + ' ' + $t('view.casesUnit')"></div>
            <span class="histlabel">{{ b.label }}</span>
          </div>
        </div>
        <div class="note">{{ $t("view.median", { v: fmtTok(buckets[0]?.median || 0) }) }}</div>
      </div>
    </div>

    <!-- what the artifacts did not contain: stated, not silently absent -->
    <div class="card gapcard" v-if="show('gaps') && (view.data_gaps || []).length">
      <h4>{{ $t("view.gapsTitle") }}</h4>
      <div class="gaps">
        <span v-for="g in view.data_gaps" :key="g" class="gapchip">{{ $t("view.gap." + g) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.statrow { display: grid; grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
           gap: 10px; margin-bottom: 12px; }
.stat { background: var(--ag-surface); border: 1px solid var(--ag-line);
        border-radius: var(--ag-radius); padding: 11px 13px; box-shadow: var(--ag-shadow); }
.stat-label { font-size: 12px; color: var(--ag-muted); margin-bottom: 3px; }
.stat-value { font-size: 25px; font-weight: 650; letter-spacing: -.4px; line-height: 1.15; }
.stat-value.muted { color: var(--ag-muted); font-size: 17px; }
.stat-sub { font-size: 12px; color: var(--ag-muted); margin-top: 3px; }
.ok { color: var(--ag-ok); } .bad { color: var(--ag-bad); }
.warn { color: var(--ag-warn); } .muted { color: var(--ag-muted); }

.panelgrid { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 12px; }
.card { background: var(--ag-surface); border: 1px solid var(--ag-line);
        border-radius: var(--ag-radius); padding: 12px 14px; margin-bottom: 12px;
        box-shadow: var(--ag-shadow); }
.card h4 { margin: 0 0 9px; font-size: 13px; color: var(--ag-muted); font-weight: 600; }

.bars { display: grid; gap: 6px; }
.barrow { display: grid; grid-template-columns: 78px 1fr 52px; align-items: center; gap: 8px;
          font-size: 12.5px; }
.barlabel { color: var(--ag-muted); }
.bartrack { background: var(--ag-surface-2); border-radius: 4px; height: 9px; overflow: hidden; }
.barfill { height: 100%; border-radius: 4px; background: var(--ag-primary); }
.barfill.llm { background: var(--ag-llm); }
.barfill.tool { background: var(--ag-tool); }
.barfill.wait { background: var(--ag-muted); }
.barfill.sig { background: var(--ag-warn); }
.barval { text-align: right; font-variant-numeric: tabular-nums; color: var(--ag-muted); }
.note { margin-top: 8px; font-size: 12px; color: var(--ag-muted); }

.looprow { display: flex; gap: 18px; font-size: 13px; flex-wrap: wrap; }
.loopcost { margin-top: 8px; font-size: 13px; display: flex; gap: 7px; align-items: baseline; }

.hist { display: flex; align-items: flex-end; gap: 5px; height: 92px; margin-top: 4px; }
.histcol { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%;
           justify-content: flex-end; gap: 4px; }
.histbar { width: 100%; background: var(--ag-primary); opacity: .8; border-radius: 3px 3px 0 0;
           min-height: 2px; }
.histlabel { font-size: 10.5px; color: var(--ag-muted); white-space: nowrap; }


.gapcard { border-style: dashed; }
.gaps { display: flex; flex-wrap: wrap; gap: 7px; }
.gapchip { background: var(--ag-surface-2); border: 1px solid var(--ag-line);
           border-radius: 6px; padding: 3px 9px; font-size: 12px; color: var(--ag-muted); }
</style>

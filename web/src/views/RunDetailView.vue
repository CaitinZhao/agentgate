<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { useI18n } from "vue-i18n";
import { useRoute } from "vue-router";
import { marked } from "marked";
import { api, download, fetchText, fmtTime } from "../store";
import ScoreRadar from "../components/ScoreRadar.vue";

const { t, locale } = useI18n();
const route = useRoute();
const id = route.params.id as string;
const run = ref<any>(null);
const err = ref("");
const msgOk = ref("");
const tab = ref<"items" | "report" | "analysis" | "artifacts">("items");
const reportText = ref("");
const analysisText = ref("");
const reportLang = ref<"zh" | "en">(locale.value === "en" ? "en" : "zh");
const scores = ref<any>(null);          // scores.json (six-dimension hexagon, doc 35)
const openCard = ref("");
let timer: any = null;

// reports/analysis are platform-generated markdown; render to HTML (script tags stripped)
function md(src: string): string {
  const html = marked.parse(src || "", { async: false }) as string;
  return html.replace(/<script[\s\S]*?<\/script>/gi, "");
}

const done = computed(() =>
  run.value && ["succeeded", "failed", "cancelled"].includes(run.value.status));

const ARTIFACTS = ["eval_results.json", "runs_meta.json", "failures.jsonl",
  "spans_raw.json", "llm_calls_raw.json", "scores.json", "judge.jsonl"];

async function load() {
  try {
    run.value = await api("GET", "/runs/" + id);
    if (done.value && scores.value === null) loadScores();
  } catch (e: any) { err.value = e.message; clearInterval(timer); }
}

async function loadScores() {
  try { scores.value = await api("GET", "/runs/" + id + "/scores"); }
  catch { scores.value = false; }        // false = pre-C1 run, no scores file
}

async function showReport(lang: "zh" | "en") {
  reportLang.value = lang;
  try { reportText.value = await fetchText(`/runs/${id}/report?lang=${lang}`); }
  catch (e: any) { reportText.value = "// " + e.message; }
}

// post-hoc AI report summary (for runs that finished before AI was configured)
const aiSummaryBusy = ref(false);
const rebuildBusy = ref(false);
const hasAiSummary = computed(() =>
  /AI 摘要与改进建议|AI Summary \(drafted by/.test(reportText.value || ""));
async function genAiSummary() {
  aiSummaryBusy.value = true;
  err.value = "";
  try {
    await api("POST", `/runs/${id}/ai-summary`, {});
    await showReport(reportLang.value);
    msgOk.value = t("runDetail.aiSummaryDone");
  } catch (e: any) { err.value = e.message; }
  aiSummaryBusy.value = false;
}
async function rebuildReport() {
  rebuildBusy.value = true;
  err.value = "";
  try {
    await api("POST", `/runs/${id}/rebuild-report`, {});
    await showReport(reportLang.value);
    msgOk.value = t("runDetail.reportRebuilt");
  } catch (e: any) { err.value = e.message; }
  rebuildBusy.value = false;
}

async function showAnalysis() {
  try { analysisText.value = await fetchText(`/runs/${id}/artifacts/analysis.md`); }
  catch {
    try { analysisText.value = await fetchText(`/runs/${id}/artifacts/analysis-en.md`); }
    catch { analysisText.value = ""; }
  }
}

function switchTab(name: any) {
  tab.value = name;
  if (name === "report" && !reportText.value) showReport(reportLang.value);
  if (name === "analysis" && !analysisText.value) showAnalysis();
}

onMounted(() => { load(); timer = setInterval(load, 2000); });
onUnmounted(() => clearInterval(timer));

const reviewNote = ref<Record<string, string>>({});
const judgeBusy = ref<Record<string, boolean>>({});
const judgeSug = ref<Record<string, any>>({});     // post-hoc AI judge suggestions
async function aiJudge(it: any) {
  judgeBusy.value[it.case_id] = true;
  err.value = "";
  try {
    const r = await api("POST", `/runs/${id}/cases/${it.case_id}/ai-judge`, {});
    judgeSug.value[it.case_id] = r.suggestion;
  } catch (e: any) { err.value = e.message; }
  judgeBusy.value[it.case_id] = false;
}
// batch post-hoc AI judging: loops 10-case batches until no PENDING remains
const batchBusy = ref(false);
const batchDone = ref(0);
const batchTotal = ref(0);
// batch AI judging is an ASYNC job: enqueue once, then poll progress; a page
// refresh re-attaches via the latest-job endpoint, and cancel stops between cases
let pollTimer: any = null;
async function batchJudge() {
  err.value = "";
  batchBusy.value = true;
  batchDone.value = 0;
  batchTotal.value = (run.value?.items || []).filter((i: any) => i.verdict === "PENDING").length;
  try {
    const r = await api("POST", `/runs/${id}/ai-judge-batch`, { limit: 30 });
    if (!r.job_id) { batchBusy.value = false; return; }   // nothing to judge
    await pollBatchJob();
  } catch (e: any) { err.value = e.message; batchBusy.value = false; }
}

async function pollBatchJob() {
  if (pollTimer) { clearTimeout(pollTimer); pollTimer = null; }
  try {
    const st = await api("GET", `/runs/${id}/ai-judge-jobs/latest`);
    if (st.status === "none") { batchBusy.value = false; return; }
    batchDone.value = st.done;
    batchTotal.value = st.total;
    if (st.status === "running") {
      pollTimer = setTimeout(pollBatchJob, 2500);
      return;
    }
    // done / cancelled / failed-batch: show what we have and refresh suggestions
    if (st.failed?.length) err.value = st.failed[0].error;
    else msgOk.value = t("runDetail.batchJudgeDone", { n: st.judged?.length ?? batchDone.value });
    await load();
  } catch (e: any) { err.value = e.message; }
  batchBusy.value = false;
}

async function cancelBatchJudge() {
  try {
    const st = await api("GET", `/runs/${id}/ai-judge-jobs/latest`);
    if (st.id) await api("POST", `/ai-judge-jobs/${st.id}/cancel`);
  } catch (e: any) { err.value = e.message; }
}

async function review(it: any, verdict: "PASS" | "FAIL") {
  const note = reviewNote.value[it.case_id] || (verdict === "PASS" ? "采纳 judge 建议" : "人工复核不通过");
  try {
    await api("POST", `/runs/${id}/review`, { case_id: it.case_id, verdict, note });
    closeCaseDetail();
    await load();
  } catch (e: any) { err.value = e.message; }
}

// ---- per-case detail dialog: question / agent answer / judgment / gold / tool evidence ----
const caseDetail = ref<any>(null);        // full detail payload
const caseDetailId = ref("");
const caseDetailErr = ref("");
async function openCaseDetail(caseId: string) {
  caseDetailId.value = caseId;
  caseDetailErr.value = "";
  caseDetail.value = null;
  try {
    caseDetail.value = await api("GET", `/runs/${id}/cases/${caseId}/detail`);
  } catch (e: any) { caseDetailErr.value = e.message; }
}
function closeCaseDetail() { caseDetailId.value = ""; caseDetail.value = null; }

// ---- AI-assisted gold revision (user disputes a verdict; draft only, user confirms) ----
const goldFix = ref<any>(null);          // set ONLY after the draft arrives (no flash-away)
const goldFixLoading = ref(false);
const goldDraftText = ref("");
const goldFixType = ref("");
const goldFixNote = ref("");
const goldFixErr = ref("");
async function openGoldFix(it: any) {
  goldFixLoading.value = true;
  goldFixErr.value = "";
  err.value = "";
  try {
    const r = await api("POST", `/runs/${id}/cases/${it.case_id}/ai-fix-gold`, {});
    goldFix.value = { ...r, case_id: it.case_id, saving: false };
    goldDraftText.value = JSON.stringify(r.draft?.gold ?? r.current?.gold ?? {}, null, 2);
    goldFixType.value = r.draft?.type || r.current?.type || "free_text";
    goldFixNote.value = (r.draft?.diagnosis || "") + " — " + (r.draft?.explanation || "");
  } catch (e: any) {
    // keep the button usable and show WHY inside the dialog area (no silent flash-away)
    goldFixErr.value = e.message;
  }
  goldFixLoading.value = false;
}
async function applyGoldFix() {
  const gf = goldFix.value;
  let parsed: any;
  try { parsed = JSON.parse(goldDraftText.value); }
  catch { goldFixErr.value = t("runDetail.fixGoldJsonBad"); return; }
  gf.saving = true;
  goldFixErr.value = "";
  try {
    await api("PATCH", `/benchmarks/${gf.bank}/cases/${gf.case_id}`,
              { type: goldFixType.value, gold: parsed });
    goldFix.value = null;
    msgOk.value = t("runDetail.goldFixed", { case: gf.case_id });
    await load();
  } catch (e: any) { goldFixErr.value = e.message; }
  gf.saving = false;
}

// native (dataset-original) reading chips per bank — the dual-score convention
const nativeRows = computed(() => {
  const nat = scores.value?.native;
  if (!nat || typeof nat !== "object") return [];
  return Object.entries(nat).map(([suite, v]: [string, any]) => ({
    suite, ...v,
    label: v.label || (v.family === "generic" ? "typed hard check + judge" : v.family),
  }));
});

function dl(fname: string) {
  download(`/runs/${id}/artifacts/${fname}`, fname).catch((e) => (err.value = e.message));
}
</script>

<template>
  <div v-if="run">
  <div class="inline" style="margin-bottom: 8px">
    <router-link class="btn small" to="/runs">{{ $t("overview.back") }}</router-link>
    <h2 class="page-title" style="margin: 0">{{ run.name }}</h2>
    <span class="chip" :class="run.status">{{ $t("status." + run.status) }}</span>
    <span class="muted">{{ run.creator || run.user_name }} · {{ fmtTime(run.created_at) }}</span>
  </div>
    <div v-if="err" class="error-box">{{ err }}</div>
    <div v-if="run.error" class="error-box">{{ $t("runDetail.error") }}: {{ run.error }}</div>

    <div class="card">
      <div class="inline" style="margin-bottom: 6px">
        <b>{{ $t("runDetail.progress") }}</b>
        <span class="muted">{{ run.done_cases }}/{{ run.total_cases }}</span>
        <span v-if="run.status === 'running' && run.est_duration_s" class="muted">
          {{ $t("runDetail.eta", { sec: Math.max(0, run.est_duration_s * (1 - run.done_cases / Math.max(1, run.total_cases))) | 0 }) }}</span>
        <span v-if="run.status === 'queued'" class="muted">
          {{ $t("runDetail.queuePos", { pos: run.queue_position || 1 }) }}</span>
      </div>
      <div class="progressbar"><div :style="{ width: (run.total_cases ? (run.done_cases / run.total_cases) * 100 : 0) + '%' }"></div></div>
      <div class="muted" style="margin-top: 6px" v-if="run.gate_decision">
        {{ $t("runDetail.gate") }}: <b :title="$t('runDetail.pendingHint')">{{ run.gate_decision }}</b>
        <template v-if="run.score"> · {{ $t("runDetail.autoScore") }}: {{ run.score }}</template>
        <template v-if="run.scheduled_for"> · scheduled: {{ fmtTime(run.scheduled_for) }}</template>
      </div>
    <div class="muted" v-if="String(run.gate_decision || '').startsWith('PENDING')">
      {{ $t("runDetail.pendingHint") }}
    </div>
    </div>

    <!-- six-dimension hexagon + diagnostic cards (doc 35 §2/§3/§6) -->
    <div class="card" v-if="done && scores && scores.run_scores">
      <h4 style="margin: 0 0 4px">{{ $t("scores.title") }}</h4>
      <div class="inline" style="align-items: flex-start">
        <ScoreRadar :scores="scores.run_scores" :size="260" />
        <div style="flex: 1; min-width: 0">
          <div class="inline" style="margin-bottom: 8px">
            <b>{{ $t("scores.total") }}:
              {{ scores.total ?? "n/a" }}</b>
            <span v-if="scores.capped_by_safety" class="chip FAIL">{{ $t("scores.safetyCapped") }}</span>
            <span v-if="(run.stability_k || 1) > 1" class="chip PENDING">
              {{ $t("scores.stabilityOn", { k: run.stability_k }) }}</span>
          </div>
          <div class="muted" style="margin-bottom: 8px">
            {{ $t("scores.counts", { p: scores.counts.pass, f: scores.counts.fail,
                                     n: scores.counts.pending, s: scores.counts.skipped }) }}
          </div>
          <div class="checkbar" style="flex-wrap: wrap">
            <button v-for="(card, dim) in scores.cards" :key="dim" class="btn small"
                    :class="{ primary: openCard === dim }" @click="openCard = openCard === dim ? '' : dim">
              {{ $t("scores.dim." + dim) }}: {{ card.score ?? "n/a" }}
            </button>
          </div>
          <div v-for="(card, dim) in scores.cards" :key="'card' + dim" v-show="openCard === dim"
               style="border-top: 1px solid #e2e8f0; margin-top: 8px; padding-top: 8px">
            <div v-if="card.signals?.length">
              <b class="muted">{{ $t("scores.signals") }}</b>
              <ul style="margin: 4px 0">
                <li v-for="(s, i) in card.signals" :key="i">
                  <span class="mono muted">{{ s.case_id }}</span> — {{ s.note }}
                </li>
              </ul>
            </div>
            <div v-if="card.worst?.length">
              <b class="muted">{{ $t("scores.worst") }}</b>
              <ul style="margin: 4px 0">
                <li v-for="(w, i) in card.worst" :key="i">
                  <span class="mono">{{ w.case_id }}</span> — {{ w.note }}
                  <span v-if="w.asi" class="muted"> · ASI: {{ w.asi }}</span>
                </li>
              </ul>
            </div>
            <div v-if="card.improvements?.length">
              <b class="muted">{{ $t("scores.improvements") }}</b>
              <ul style="margin: 4px 0">
                <li v-for="(im, i) in card.improvements" :key="i">{{ im }}</li>
              </ul>
            </div>
            <div v-if="!card.signals?.length && !card.improvements?.length" class="muted">
              {{ $t("scores.noSignals") }}
            </div>
          </div>
        </div>
      </div>
    </div>
    <div class="card muted" v-if="done && scores === false">{{ $t("scores.legacyRun") }}</div>

    <!-- native (dataset-original) reading: per-bank metric under the dataset's own rules -->
    <div class="card" v-if="done && nativeRows.length">
      <h4 style="margin: 0 0 4px">{{ $t("scores.nativeTitle") }}</h4>
      <div class="muted" style="margin-bottom: 6px">{{ $t("scores.nativeHint") }}</div>
      <table class="tbl" style="width: auto">
        <thead><tr>
          <th>{{ $t("runDetail.colBank") }}</th><th>{{ $t("scores.nativeMetric") }}</th>
          <th>{{ $t("scores.nativeRate") }}</th><th></th>
        </tr></thead>
        <tbody>
          <tr v-for="n in nativeRows" :key="n.suite">
            <td class="mono">{{ n.suite }}</td>
            <td>{{ n.label }}</td>
            <td><b>{{ n.rate ?? "n/a" }}</b><span class="muted">{{ n.family === 'airbench' ? '' : '%' }}</span></td>
            <td class="muted">{{ n.pass }}/{{ n.total }}
              <span v-if="n.metrics" class="mono">
                {{ Object.entries(n.metrics).map(([k, v]) => k + "=" + v).join(" · ") }}</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="checkbar" style="margin-bottom: 10px">
      <button class="btn small" :class="{ primary: tab === 'items' }" @click="switchTab('items')">{{ $t("runDetail.items") }}</button>
      <button v-if="done" class="btn small" :class="{ primary: tab === 'report' }" @click="switchTab('report')">{{ $t("runDetail.tabReport") }}</button>
      <button v-if="done" class="btn small" :class="{ primary: tab === 'analysis' }" @click="switchTab('analysis')">{{ $t("runDetail.tabAnalysis") }}</button>
      <button v-if="done" class="btn small" :class="{ primary: tab === 'artifacts' }" @click="switchTab('artifacts')">{{ $t("runDetail.tabArtifacts") }}</button>
      <button v-if="done && (run.items || []).some(i => i.verdict === 'PENDING')" class="btn small"
              :disabled="batchBusy" @click="batchJudge">
        ✨ {{ $t("runDetail.batchJudge") }}{{ batchBusy ? ` (${batchDone}/${batchTotal})` : "" }}</button>
      <button v-if="batchBusy" class="btn small danger" @click="cancelBatchJudge">
        {{ $t("runDetail.batchJudgeCancel") }}</button>
    </div>

    <div class="tbl-wrap">
    <table class="tbl" v-if="tab === 'items'">
      <thead><tr>
        <th>{{ $t("runDetail.colCase") }}</th><th>{{ $t("runDetail.colBank") }}</th>
        <th>{{ $t("runDetail.colLevel") }}</th><th>{{ $t("runDetail.colVerdict") }}</th><th>{{ $t("runDetail.colHistory") }}</th>
        <th>{{ $t("runDetail.colTokens") }}</th><th>{{ $t("runDetail.colWall") }}</th>
        <th><span :title="$t('runDetail.asiHint')">{{ $t("runDetail.colAsi") }}</span></th><th></th>
      </tr></thead>
      <tbody>
        <tr v-if="!run.items.length"><td colspan="9" class="muted">{{ $t("common.loading") }}</td></tr>
        <tr v-for="it in run.items" :key="it.case_id">
          <td class="mono"><a href="javascript:void(0)" class="link"
                              @click="openCaseDetail(it.case_id)">{{ it.case_id }}</a></td>
          <td class="muted">{{ it.bank }}</td>
          <td class="mono">{{ it.level }}</td>
          <td><span class="chip" :class="it.verdict">{{ $t("verdict." + it.verdict) }}</span>
            <span v-if="it.final_verdict" class="chip" :class="it.final_verdict"
                  :title="$t('runDetail.reviewTitle') + '：' + it.reviewed_by">{{ $t("runDetail.reviewed") }}·{{ $t("verdict." + it.final_verdict) }}</span>
          </td>
          <td class="muted">{{ it.history_pass }}/{{ it.history_runs }}</td>
          <td>{{ it.tokens }}</td>
          <td>{{ it.wall_time_s?.toFixed?.(1) ?? it.wall_time_s }}</td>
          <td class="muted" style="max-width: 420px">{{ (locale === "en" ? (it.asi_display_en || it.asi_display) : it.asi_display) || it.asi }}</td>
          <td style="white-space: nowrap">
            <button v-if="it.verdict === 'PENDING' && !it.final_verdict" class="btn small primary"
                    @click="openCaseDetail(it.case_id)">✧ {{ $t("runDetail.arbitrate") }}</button>
            <button v-else class="btn small" @click="openCaseDetail(it.case_id)">{{ $t("runDetail.caseDetail") }}</button>
            <button v-if="it.verdict === 'FAIL'" class="btn small" :disabled="goldFixLoading"
                    @click="openGoldFix(it)">✨ {{ $t("runDetail.fixGold") }}</button>
          </td>
        </tr>
      </tbody>
    </table>
    </div>

    <template v-if="tab === 'report'">
      <div class="checkbar" style="margin-bottom: 6px">
        <button class="btn small" :class="{ primary: reportLang === 'zh' }" @click="showReport('zh')">中文</button>
        <button class="btn small" :class="{ primary: reportLang === 'en' }" @click="showReport('en')">English</button>
        <button v-if="done && !hasAiSummary" class="btn small" :disabled="aiSummaryBusy"
                @click="genAiSummary">✨ {{ $t("runDetail.aiSummaryGen") }}</button>
        <button v-if="done" class="btn small" :disabled="rebuildBusy"
                @click="rebuildReport">{{ $t("runDetail.rebuildReport") }}</button>
      </div>
      <div class="card md-body" v-html="md(reportText)"></div>
    </template>

    <template v-if="tab === 'analysis'">
      <div v-if="analysisText" class="card md-body" v-html="md(analysisText)"></div>
      <div v-else class="card muted">{{ $t("runDetail.noAnalysis") }}</div>
    </template>

    <template v-if="tab === 'artifacts'">
      <div class="card">
        <h4>{{ $t("runDetail.artifactsHint") }}</h4>
        <div class="inline">
          <button v-for="a in ARTIFACTS" :key="a" class="btn small mono" @click="dl(a)">{{ a }}</button>
        </div>
      </div>
    </template>

    <!-- per-case detail: question / answer / judgment / gold / tool evidence -->
    <dialog :open="!!caseDetailId" style="min-width: 680px; max-width: 860px">
      <div v-if="caseDetailErr" class="error-box">{{ caseDetailErr }}</div>
      <template v-if="caseDetail">
        <div class="inline" style="margin-bottom: 6px">
          <h3 style="margin: 0"><span class="mono">{{ caseDetail.case_id }}</span></h3>
          <span class="chip" :class="caseDetail.verdict">{{ $t("verdict." + caseDetail.verdict) }}</span>
          <span class="muted">{{ caseDetail.bank }} · {{ caseDetail.level }} ·
            {{ caseDetail.tokens }} tokens · {{ caseDetail.wall_time_s?.toFixed?.(1) }}s</span>
          <span class="spacer" style="flex:1"></span>
          <button class="btn small" @click="closeCaseDetail">{{ $t("common.close") }}</button>
        </div>
        <div class="muted" style="margin: 4px 0"><b>{{ $t("runDetail.dQuestion") }}</b></div>
        <pre class="mono" style="max-height: 120px; overflow: auto; white-space: pre-wrap">{{ caseDetail.query || "(无题面记录)" }}</pre>
        <div v-if="caseDetail.context_chars" class="muted" style="font-size: 12px">
          {{ $t("runDetail.dContext", { n: caseDetail.context_chars }) }}：{{ caseDetail.context_head }}…
        </div>
        <div v-if="Object.keys(caseDetail.input_extra || {}).length" class="muted mono" style="font-size: 12px">
          {{ JSON.stringify(caseDetail.input_extra) }}
        </div>
        <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dAnswer") }}</b></div>
        <pre class="mono" style="max-height: 220px; overflow: auto; white-space: pre-wrap">{{ caseDetail.answer || "(无回答归档)" }}</pre>
        <div v-if="caseDetail.final_json && Object.keys(caseDetail.final_json).length" class="muted mono" style="font-size: 12px">
          FINAL: {{ JSON.stringify(caseDetail.final_json) }}
        </div>
        <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dJudgment") }}</b></div>
        <div style="margin: 2px 0">{{ (locale === "en" ? (caseDetail.asi_display_en || caseDetail.asi_display) : caseDetail.asi_display) || caseDetail.asi }}</div>
        <div v-if="caseDetail.judge_suggestion" class="muted" style="font-size: 12px">
          {{ $t("runDetail.judgeSuggestion") }}：{{ caseDetail.judge_suggestion.verdict_suggest }}
          ({{ caseDetail.judge_suggestion.score }}) · {{ caseDetail.judge_suggestion.rationale }}
        </div>
        <template v-if="(caseDetail.checks || []).length">
          <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dChecks") }}</b></div>
          <ul style="margin: 4px 0; padding-left: 18px">
            <li v-for="(c, i) in caseDetail.checks" :key="i" :class="c.ok ? '' : 'mono'"
                :style="c.ok ? 'color: #166534' : 'color: #991b1b'">
              {{ c.ok ? '✓' : '✗' }} {{ c.name }} — {{ (locale === "en" ? (c.reason_en || c.reason) : c.reason) || c.asi }}
            </li>
          </ul>
        </template>
        <template v-if="(caseDetail.judge_suggestion?.coverage || []).length">
          <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dCoverage") }}</b>
            <span class="muted">({{ $t("runDetail.dCoverageHint") }})</span></div>
          <ul style="margin: 4px 0; padding-left: 18px">
            <li v-for="(c, i) in caseDetail.judge_suggestion.coverage" :key="'c' + i">
              {{ c.hit ? '✓' : '✗' }} {{ c.point }} <span v-if="c.evidence" class="muted">— {{ c.evidence }}</span>
            </li>
          </ul>
        </template>
        <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dGold") }}</b></div>
        <pre class="mono" style="max-height: 140px; overflow: auto; font-size: 12px">{{ JSON.stringify(caseDetail.gold?.final ?? {}, null, 1) }}</pre>
        <div class="muted" style="margin: 8px 0 4px"><b>{{ $t("runDetail.dEvidence") }}</b></div>
        <div class="mono muted" style="font-size: 12px">
          <div>{{ $t("runDetail.dModel") }}: {{ caseDetail.model || "-" }} · trace: {{ caseDetail.trace_id || "-" }}</div>
          <div>{{ $t("runDetail.dTools") }} ({{ (caseDetail.tool_calls || []).length }}):
            {{ (caseDetail.tool_calls || []).join(" → ") || $t("runDetail.dNoTools") }}</div>
          <div v-if="(caseDetail.denied_tools || []).length">{{ $t("runDetail.dDenied") }}:
            {{ caseDetail.denied_tools.join(", ") }}</div>
        </div>
        <!-- final ruling panel: PENDING cases are arbitrated right here in context -->
        <div v-if="caseDetail.verdict === 'PENDING' && !caseDetail.final_verdict"
             style="border-top: 1px dashed #e2e8f0; margin-top: 10px; padding-top: 8px">
          <div v-if="caseDetail.judge_suggestion || judgeSug[caseDetail.case_id]" class="muted" style="margin: 4px 0">
            {{ $t("runDetail.judgeSuggestion") }}：
            <span class="chip" :class="(caseDetail.judge_suggestion || judgeSug[caseDetail.case_id]).verdict_suggest === 'PASS' ? 'PASS' : 'FAIL'">
              {{ (caseDetail.judge_suggestion || judgeSug[caseDetail.case_id]).verdict_suggest }}
              ({{ (caseDetail.judge_suggestion || judgeSug[caseDetail.case_id]).score }})</span>
            · {{ (caseDetail.judge_suggestion || judgeSug[caseDetail.case_id]).rationale }}
          </div>
          <div class="inline">
            <b>{{ $t("runDetail.reviewTitle") }}</b>
            <button class="btn small" :disabled="judgeBusy[caseDetail.case_id]"
                    @click="aiJudge(caseDetail)">✨ {{ $t("runDetail.aiJudge") }}</button>
            <button class="btn small" @click="review(caseDetail, 'PASS')">{{ $t("runDetail.reviewPass") }}</button>
            <button class="btn small danger" @click="review(caseDetail, 'FAIL')">{{ $t("runDetail.reviewFail") }}</button>
            <input v-model="reviewNote[caseDetail.case_id]" style="flex:1; min-width: 220px"
                   :placeholder="$t('runDetail.reviewNote')" />
          </div>
          <div class="muted" style="font-size: 12px">{{ $t("runDetail.reviewHint") }}</div>
        </div>
        <div v-if="caseDetail.final_verdict" class="ok-box" style="margin-top: 8px">
          {{ $t("runDetail.reviewed") }}：{{ $t("verdict." + caseDetail.final_verdict) }}
          <template v-if="caseDetail.reviewed_by"> · {{ caseDetail.reviewed_by }}</template>
          <template v-if="caseDetail.review_note"> · {{ caseDetail.review_note }}</template>
        </div>
      </template>
      <div v-else class="muted">{{ $t("common.loading") }}</div>
    </dialog>

    <!-- AI gold-fix modal: review the AI draft, edit, then apply to the bank case -->
    <dialog :open="!!goldFix" style="min-width: 640px; max-width: 760px">
      <template v-if="goldFix">
        <h3 style="margin: 0 0 6px">✨ {{ $t("runDetail.fixGoldTitle") }}
          <span class="mono muted">{{ goldFix.case_id }}</span></h3>
        <div v-if="goldFixErr" class="error-box">{{ goldFixErr }}</div>
        <div v-if="goldFixNote" class="ok-box">{{ goldFixNote }}</div>
        <div class="muted" style="margin: 6px 0">{{ $t("runDetail.fixGoldAnswer") }}:</div>
        <pre class="mono" style="max-height: 140px; overflow: auto; white-space: pre-wrap">{{ goldFix.answer || "(无)" }}</pre>
        <div class="form-grid" style="margin-top: 8px">
          <div class="form-row"><label>{{ $t("runDetail.fixGoldType") }}</label>
            <select v-model="goldFixType">
              <option value="auto">auto</option><option value="numeric">numeric</option>
              <option value="boolean">boolean</option><option value="extractive">extractive</option>
              <option value="free_text">free_text</option><option value="refusal">refusal</option>
            </select></div>
        </div>
        <div class="muted" style="margin: 6px 0">{{ $t("runDetail.fixGoldJson") }}:</div>
        <textarea v-model="goldDraftText" rows="12"
                  style="width: 100%; font-family: monospace; font-size: 12px" />
        <div class="inline" style="margin-top: 8px">
          <button class="btn primary" :disabled="goldFix.saving" @click="applyGoldFix">
            {{ $t("runDetail.fixGoldApply") }}</button>
          <button class="btn" @click="goldFix = null">{{ $t("common.close") }}</button>
          <span class="muted">{{ $t("runDetail.fixGoldHint") }}</span>
        </div>
      </template>
    </dialog>
  </div>
</template>

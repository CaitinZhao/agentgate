<script setup lang="ts">
// Case v2 form (doc 35 §4): the author fills query + level + gold (final answer, optional
// checkpoints / rubric); `type` may stay auto — the judging pipeline infers it from the
// gold shape. Pack-level config (evidence/caliber/red lines) is inherited, never typed.
// Optional AI drafting (35 §4.1.1): paste a material/dataset answer, get a structured
// gold draft — the author confirms every item (LLM never invents gold).
import { computed, reactive, ref } from "vue";
import { useI18n } from "vue-i18n";
import Sel from "./Sel.vue";
import { api } from "../store";

const { t } = useI18n();
const LEVELS = [
  { value: "L0", label: "L0" }, { value: "L1", label: "L1" }, { value: "L2", label: "L2" }];
const STATUS = [
  { value: "active", label: "active" }, { value: "retired", label: "retired" }];
const TYPES = ["auto", "numeric", "boolean", "extractive", "free_text", "refusal"]
  .map((v) => ({ value: v, label: v }));

const props = defineProps<{
  initial?: any;          // existing case (edit mode) or null (add mode)
  caseIdLocked?: boolean;
  bankName?: string;      // needed for AI drafting
}>();
const emit = defineEmits<{ (e: "submit", payload: any): void; (e: "cancel"): void }>();

function split(s: string) {
  return String(s).split(/；|;/).map((x: string) => x.trim()).filter(Boolean);
}
const gold = props.initial?.gold || {};
const gfinal = gold.final || {};

const f = reactive<any>({
  case_id: props.initial?.case_id || "",
  query: props.initial?.query || props.initial?.input?.query || "",
  level: props.initial?.level || "L2",
  status: props.initial?.status || "active",
  type: props.initial?.type || "auto",
  // typed final (only the section matching `type` matters; kept separately for round-trips)
  value: gfinal.value ?? "",
  unit: gfinal.unit || "",
  tol_rel: gfinal.tol_rel ?? 0.01,
  basis_tokens: (gfinal.basis_tokens || []).join("；"),
  aliases: (gfinal.aliases || []).join("；"),
  keywords: (gfinal.keywords || []).join("；"),
  forbidden_regex: (gfinal.forbidden_answer_regex || []).join("；"),
  checkpoints: (gold.checkpoints || []).map((c: any) => ({ ...c })),
  rubric: (gold.rubric || []).map((r: any) => ({ ...r })),
  failure_kind: props.initial?.diagnosis_hint?.failure_kind || "unknown",
  target_layer: props.initial?.diagnosis_hint?.target_layer || "none",
  expected_behavior: props.initial?.diagnosis_hint?.expected_behavior || "",
});

function buildFinal(): any {
  const type = f.type;
  const fin: any = {};
  if (type === "numeric" || (type === "auto" && String(f.value).trim() !== "" && f.unit !== undefined
        && !isNaN(parseFloat(f.value)) && (f.tol_rel || f.unit || f.basis_tokens))) {
    fin.value = parseFloat(f.value);
    fin.unit = f.unit || "none";
    fin.tol_rel = parseFloat(f.tol_rel) || 0.01;
    const bt = split(f.basis_tokens);
    if (bt.length) fin.basis_tokens = bt;
  } else if (f.value !== "" && f.value !== null && f.value !== undefined) {
    fin.value = String(f.value).trim();
    const al = split(f.aliases);
    if (al.length) fin.aliases = al;
    const kw = split(f.keywords);
    if (kw.length) fin.keywords = kw;
  } else {
    const kw = split(f.keywords);
    if (kw.length) fin.keywords = kw;
  }
  const rx = split(f.forbidden_regex);
  if (rx.length) fin.forbidden_answer_regex = rx;
  return fin;
}

function payload() {
  return {
    case_id: f.case_id.trim(),
    query: f.query.trim(),
    level: f.level,
    status: f.status,
    type: f.type,
    gold: {
      final: buildFinal(),
      checkpoints: f.checkpoints
        .filter((c: any) => String(c.pattern || "").trim())
        .map((c: any) => ({ desc: c.desc || c.pattern, signal: c.signal || "content",
                            pattern: c.pattern, weight: Number(c.weight) || 1 })),
      rubric: f.rubric
        .filter((r: any) => String(r.point || "").trim())
        .map((r: any) => ({ point: r.point, weight: Number(r.weight) || 1 })),
    },
    diagnosis_hint: {
      failure_kind: f.failure_kind.trim() || "unknown",
      target_layer: f.target_layer.trim() || "none",
      expected_behavior: f.expected_behavior.trim(),
    },
  };
}
function submit() {
  if (!f.query.trim()) { alert(t("edit.queryRequired")); return; }
  emit("submit", payload());
}

// ---- AI drafting (optional; needs the User-Center AI config) ----
const aiOpen = ref(false);
const aiMaterial = ref("");
const aiBusy = ref(false);
const aiErr = ref("");
async function aiDraft() {
  aiErr.value = ""; aiBusy.value = true;
  try {
    const r = await api("POST", `/benchmarks/${props.bankName}/ai-draft-case`,
                        { query: f.query, material: aiMaterial.value });
    const d = r.draft || {};
    if (d.type) f.type = d.type;
    const gf = d.gold?.final || {};
    if (gf.value !== undefined && gf.value !== null) f.value = gf.value;
    if (gf.unit) f.unit = gf.unit;
    if (gf.tol_rel) f.tol_rel = gf.tol_rel;
    if (gf.aliases) f.aliases = gf.aliases.join("；");
    if (gf.keywords) f.keywords = gf.keywords.join("；");
    if (d.gold?.checkpoints) f.checkpoints = d.gold.checkpoints.map((c: any) => ({ ...c }));
    if (d.gold?.rubric) f.rubric = d.gold.rubric.map((r2: any) => ({ ...r2 }));
    aiOpen.value = false;
  } catch (e: any) { aiErr.value = e.message; }
  aiBusy.value = false;
}

const showChecks = computed(() => f.type !== "free_text" || f.checkpoints.length > 0);

// ---- AI rubric-point drafting: decompose the gold answer into checkable points ----
const rubricBusy = ref(false);
async function aiRubric() {
  if (!bankName) return;
  rubricBusy.value = true;
  try {
    const answerText = [f.value, f.aliases, f.keywords].filter(Boolean).join("；")
      || aiMaterial.value || f.query;
    const r = await api("POST", `/benchmarks/${bankName}/ai-rubric`,
                        { query: f.query, answer: answerText });
    if (r.rubric?.length) f.rubric = r.rubric.map((x: any) => ({ ...x }));
  } catch (e: any) { alert(e.message); }
  rubricBusy.value = false;
}
</script>

<template>
  <form @submit.prevent="submit">
    <h4 class="section-title">{{ $t("edit.requiredSection") }}</h4>
    <div class="form-row" v-if="!caseIdLocked || f.case_id">
      <label>{{ $t("detail.caseId") }}</label>
      <input v-model="f.case_id" class="mono" :disabled="caseIdLocked" />
    </div>
    <div class="form-row"><label>{{ $t("detail.query") }} *</label>
      <textarea v-model="f.query" rows="3" /></div>
    <div class="form-grid">
      <div class="form-row"><label>{{ $t("detail.level") }}</label>
        <Sel v-model="f.level" :options="LEVELS" /></div>
      <div class="form-row" v-if="caseIdLocked"><label>{{ $t("detail.colStatus") }}</label>
        <Sel v-model="f.status" :options="STATUS" /></div>
      <div class="form-row"><label>{{ $t("edit.caseType") }}</label>
        <Sel v-model="f.type" :options="TYPES" /></div>
    </div>
    <div class="muted" style="margin: -4px 0 8px">{{ $t("edit.caseTypeHint") }}</div>

    <h4 class="section-title">{{ $t("edit.goldSection") }}</h4>
    <div class="form-grid">
      <div class="form-row"><label>{{ $t("edit.goldValue") }} *</label>
        <input v-model="f.value" class="mono" placeholder="96.2 / yes / 2026-09-01" /></div>
      <div class="form-row" v-if="f.type === 'numeric' || f.type === 'auto'">
        <label>{{ $t("edit.numericUnit") }}</label>
        <input v-model="f.unit" placeholder="亿元 / % / USD millions" /></div>
      <div class="form-row" v-if="f.type === 'numeric' || f.type === 'auto'">
        <label>{{ $t("edit.numericTol") }}</label>
        <input v-model="f.tol_rel" class="mono" placeholder="0.01" /></div>
    </div>
    <div class="form-row" v-if="f.type === 'numeric' || f.type === 'auto'">
      <label>{{ $t("edit.basisTokens") }}</label>
      <input v-model="f.basis_tokens" placeholder="consolidated；合并口径" /></div>
    <div class="form-row" v-if="f.type === 'extractive' || f.type === 'auto'">
      <label>{{ $t("edit.goldAliases") }}</label>
      <input v-model="f.aliases" placeholder="2026年9月1日；9月1日" /></div>
    <div class="form-row" v-if="f.type === 'refusal' || f.type === 'auto'">
      <label>{{ $t("edit.refusalKeywords") }}</label>
      <input v-model="f.keywords" placeholder="未披露；not mentioned" /></div>
    <div class="form-row">
      <label>{{ $t("edit.forbiddenRegex") }}（{{ $t("edit.optional") }}）</label>
      <input v-model="f.forbidden_regex" class="mono" placeholder="银行W.{0,20}%" /></div>

    <h4 class="section-title">{{ $t("edit.checkpointsSection") }}（{{ $t("edit.optional") }}）</h4>
    <div class="muted" style="margin-bottom: 6px">{{ $t("edit.checkpointsHint") }}</div>
    <table class="tbl" v-if="showChecks" style="margin-bottom: 8px">
      <thead><tr>
        <th style="width: 90px">signal</th><th>pattern</th><th>desc</th>
        <th style="width: 70px">weight</th><th style="width: 40px"></th>
      </tr></thead>
      <tbody>
        <tr v-for="(c, i) in f.checkpoints" :key="i">
          <td><Sel small v-model="c.signal" :options="[
            { value: 'tool', label: 'tool' }, { value: 'content', label: 'content' }]" /></td>
          <td><input v-model="c.pattern" class="mono" placeholder="retrieve_report / 88.0" /></td>
          <td><input v-model="c.desc" placeholder="检索到 2026H1 营收 96.2 亿元" /></td>
          <td><input v-model="c.weight" class="mono" /></td>
          <td><button class="btn small" type="button" @click="f.checkpoints.splice(i, 1)">✕</button></td>
        </tr>
        <tr v-if="!f.checkpoints.length"><td colspan="5" class="muted">{{ $t("edit.noCheckpoints") }}</td></tr>
      </tbody>
    </table>
    <button class="btn small" type="button" @click="f.checkpoints.push({ signal: 'content', pattern: '', desc: '', weight: 1 })">
      + {{ $t("edit.addCheckpoint") }}</button>

    <template v-if="f.type === 'free_text' || f.rubric.length">
      <h4 class="section-title">{{ $t("edit.rubricSection") }}</h4>
      <div class="muted" style="margin-bottom: 6px">{{ $t("edit.rubricHint") }}</div>
      <table class="tbl" style="margin-bottom: 8px">
        <thead><tr><th>point</th><th style="width: 70px">weight</th><th style="width: 40px"></th></tr></thead>
        <tbody>
          <tr v-for="(r, i) in f.rubric" :key="i">
            <td><input v-model="r.point" placeholder="营收同比 +9.3%" /></td>
            <td><input v-model="r.weight" class="mono" /></td>
            <td><button class="btn small" type="button" @click="f.rubric.splice(i, 1)">✕</button></td>
          </tr>
          <tr v-if="!f.rubric.length"><td colspan="3" class="muted">{{ $t("edit.noRubric") }}</td></tr>
        </tbody>
      </table>
      <button v-if="bankName" class="btn small" type="button" :disabled="rubricBusy"
              @click="aiRubric">✨ {{ $t("edit.aiRubric") }}</button>
      <button class="btn small" type="button" @click="f.rubric.push({ point: '', weight: 1 })">
        + {{ $t("edit.addRubric") }}</button>
    </template>

    <h4 class="section-title">{{ $t("edit.hintSection") }}（{{ $t("edit.optional") }}）</h4>
    <div class="form-grid">
      <div class="form-row"><label>{{ $t("edit.failureKind") }}</label>
        <input v-model="f.failure_kind" /></div>
      <div class="form-row"><label>{{ $t("edit.targetLayer") }}</label>
        <input v-model="f.target_layer" /></div>
    </div>
    <div class="form-row"><label>{{ $t("edit.expectedBehavior") }}</label>
      <textarea v-model="f.expected_behavior" rows="2" /></div>

    <div v-if="aiErr" class="error-box">{{ aiErr }}</div>
    <div class="inline" style="margin-top: 10px">
      <button class="btn primary" type="submit">{{ $t("common.save") }}</button>
      <button class="btn" type="button" @click="emit('cancel')">{{ $t("common.cancel") }}</button>
      <button v-if="bankName" class="btn" type="button" @click="aiOpen = true">✨ {{ $t("edit.aiDraft") }}</button>
    </div>

    <dialog :open="aiOpen" style="min-width: 460px">
      <h4>{{ $t("edit.aiDraftTitle") }}</h4>
      <p class="muted">{{ $t("edit.aiDraftHint") }}</p>
      <div class="form-row"><label>{{ $t("edit.aiMaterial") }}</label>
        <textarea v-model="aiMaterial" rows="6"
                  placeholder="材料原文 / 数据集金标答案（LLM 只做结构化，不发明金标）" /></div>
      <div v-if="aiErr" class="error-box">{{ aiErr }}</div>
      <div class="inline" style="margin-top: 8px">
        <button class="btn primary" type="button" :disabled="aiBusy || !f.query.trim()" @click="aiDraft">
          {{ aiBusy ? $t("common.loading") : $t("edit.aiDraftGo") }}</button>
        <button class="btn" type="button" @click="aiOpen = false">{{ $t("common.cancel") }}</button>
      </div>
    </dialog>
  </form>
</template>
